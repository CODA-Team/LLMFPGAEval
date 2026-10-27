from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import re
import signal
import shutil
import subprocess
import threading
import time
import traceback
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from tools import td_report_parser
from .config import PROJECTS, WORK, load_config, snapshot, require_remote_config
from .metrics import number, parse_orfs_metrics, unknown_power
from .remote_worker import extract_snapshot
from .transport import remote_execute
from .lease import TaskLease
from .board_state import begin_download, finish_download


def now():
    return datetime.now(timezone.utc).isoformat()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def td_success(returncode, log, bit):
    return (returncode == 0 and not re.search(r"\b(ERROR|FATAL)\b", log)
            and Path(bit).is_file() and Path(bit).stat().st_size > 0)


def sim_success(returncode, log):
    return (returncode == 0 and "ALL_TESTS_PASSED" in log
            and not re.search(r"(?m)^\s*(?:ERROR|FATAL|FAIL|FAILED)\b", log))


def execute(command, cwd, logfile, timeout):
    started = time.monotonic()
    logfile = Path(logfile)
    with logfile.open("wb") as output:
        options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=cwd, stdout=output, stderr=subprocess.STDOUT, **options)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            else:
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=20)
            output.write(b"\nERROR: process timed out\n")
            code = 124
    return code, logfile.read_text(encoding="utf-8", errors="replace"), round(time.monotonic() - started, 3)


def archive_source(source):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for p in sorted(Path(source).rglob("*")):
            if p.is_file():
                archive.write(p, p.relative_to(source).as_posix())
    return base64.b64encode(data.getvalue()).decode()


class Runner:
    def __init__(self, work=WORK):
        self.work = Path(work)
        self.runs = self.work / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._lease = TaskLease(self.work / ".execution.lock")
        self._busy = False
        # Check/CLI/another UI must not interrupt a job owned by a live process.
        if not self._lease.acquire():
            return
        try:
            self._recover_interrupted()
        finally:
            self._lease.release()

    def _recover_interrupted(self):
        for p in self.runs.glob("*/result.json"):
            try:
                job = json.loads(p.read_text(encoding="utf-8"))
                if job.get("status") in ("queued", "running"):
                    job["status"] = "interrupted"
                    job["error"] = "本地服务已重启；远程任务可能仍在其专用目录运行，请检查日志。"
                    self.save(job)
            except (OSError, ValueError):
                pass

    @property
    def busy(self):
        return self._busy or self._lease.occupied()

    @busy.setter
    def busy(self, value):
        if value:
            if not self._lease.acquire():
                raise ValueError("另一个工作台或命令行任务正在使用本工程")
            self._busy = True
        else:
            self._lease.release()
            self._busy = False

    def directory(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
            raise ValueError("任务 ID 无效")
        return self.runs / job_id

    def save(self, job):
        with self.lock:
            path = self.directory(job["id"]) / "result.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
            tmp.replace(path)

    def get(self, job_id):
        with self.lock:
            return json.loads((self.directory(job_id) / "result.json").read_text(encoding="utf-8"))

    def history(self):
        jobs = []
        for p in sorted(self.runs.glob("*/result.json"), reverse=True)[:100]:
            try:
                jobs.append(json.loads(p.read_text(encoding="utf-8")))
            except (ValueError, OSError):
                pass
        return jobs

    def create(self, project_id, targets):
        if project_id not in PROJECTS or not isinstance(targets, list) or not targets or set(targets) - {"fpga", "asic"}:
            raise ValueError("请选择工程和有效流程")
        config = load_config()
        if 'asic' in targets:
            require_remote_config(config)
        project = copy.deepcopy(PROJECTS[project_id])
        with self.lock:
            if self.busy:
                raise ValueError("已有任务在运行，请等待当前任务完成")
            self.busy = True
        try:
            job_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
            folder = self.directory(job_id)
            folder.mkdir()
            manifest = snapshot(project, folder / "source")
            job = {"id": job_id, "project_id": project_id, "project_name": project["name"],
                   "status": "queued", "phase": "准备", "created_at": now(), "updated_at": now(),
                   "project": project, "configuration": config, "targets": sorted(set(targets)),
                   "manifest": manifest, "simulation": None, "fpga": None, "asic": None,
                   "programming": None, "hardware_functional": "not_tested"}
            self.save(job)
            return job
        except Exception:
            self.busy = False
            raise

    def run(self, job_id):
        job = self.get(job_id)
        folder = self.directory(job_id)
        config = job["configuration"]
        try:
            job.update(status="running", phase="RTL 功能仿真")
            self.save(job)
            job["simulation"] = self.simulate(job, folder)
            self.save(job)
            for target in job["targets"]:
                job["phase"] = "Windows TD 编译" if target == "fpga" else "Linux ORFS 编译"
                self.save(job)
                try:
                    job[target] = self.fpga(job, folder) if target == "fpga" else self.asic(job, folder)
                except Exception as exc:
                    (folder / (target + "-error.log")).write_text(traceback.format_exc(), encoding="utf-8")
                    job[target] = {"ok": False, "error": str(exc)}
                self.save(job)
            success = job["simulation"]["ok"] and all(job[t] and job[t]["ok"] for t in job["targets"])
            job.update(status="completed" if success else "failed", phase="完成" if success else "存在失败项")
        except Exception as exc:
            job.update(status="failed", phase="执行失败", error=str(exc))
            (folder / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            job["updated_at"] = now()
            try:
                self.save(job)
            finally:
                self.busy = False
        return job

    def simulate(self, job, folder):
        config = job["configuration"]
        source = folder / "source"
        out = folder / "simulation"
        out.mkdir()
        results = []
        for executable in ("iverilog", "vvp"):
            if not Path(config[executable]).is_file():
                return {"ok": False, "scope": "shared_rtl", "error": executable + " 未配置", "tests": []}
        version = subprocess.run([config["iverilog"], "-V"], capture_output=True, timeout=15).stdout.decode(errors="replace").splitlines()[0]
        for relative in job["project"]["testbenches"]:
            tb = source / relative
            match = re.search(r"(?m)^\s*module\s+([A-Za-z_]\w*)", tb.read_text(encoding="utf-8"))
            if not match:
                raise ValueError("未识别 Testbench 顶层：" + relative)
            name = tb.stem
            binary = out / (name + ".vvp")
            command = [config["iverilog"], "-g2012", "-s", match.group(1), "-I", str(source / "sim"),
                       "-o", str(binary)] + [str(source / p) for p in job["manifest"]["rtl_files"]] + [str(tb)]
            code, log, duration = execute(command, source, out / (name + "-compile.log"), 90)
            if code == 0:
                code, log, elapsed = execute([config["vvp"], str(binary)], source, out / (name + ".log"), 180)
                duration += elapsed
            results.append({"name": name, "ok": sim_success(code, log), "exit_code": code,
                            "duration_s": duration, "log_tail": "\n".join(log.splitlines()[-8:])})
        return {"ok": bool(results) and all(r["ok"] for r in results), "scope": "shared_rtl",
                "rtl_sha256": job["manifest"]["rtl_sha256"], "tool_version": version,
                "passed": sum(r["ok"] for r in results), "total": len(results), "tests": results,
                "notes": "参数化 RTL 自检；不能替代映射后验证或实际板卡功能测试。"}

    def fpga(self, job, folder):
        config = job["configuration"]
        exe = Path(config["td_root"]) / "bin/td_commands_prompt.exe"
        if not exe.is_file():
            raise ValueError("找不到 Windows TD：" + str(exe))
        out = folder / "fpga"
        shutil.copytree(folder / "source", out)
        flow = out / job["project"]["flow"]
        # TD runs the source flow verbatim, then exits its command shell.
        entry = out / "_workbench.tcl"
        entry.write_text("source {" + flow.as_posix() + "}\nexit\n", encoding="utf-8")
        code, log, duration = execute([str(exe), str(entry)], out, out / "td.log", config["timeout_s"])
        reports = td_report_parser.parse_report_paths(str(flow))
        bit = Path(reports["bit"]) if reports["bit"] else out / job["project"]["bit"]
        timing = td_report_parser.parse_timing(reports["timing"]) if reports["timing"] else {}
        period = timing.get("min_period_ns")
        timing["fmax_mhz"] = 1000 / period if period and period > 0 else None
        timing["stage"] = "post_route"
        version = re.search(r"Tang Dynasty,\s*(\S+)", log)
        ok = td_success(code, log, bit)
        return {"ok": ok, "exit_code": code, "duration_s": duration,
                "rtl_sha256": job["manifest"]["rtl_sha256"], "tool_version": version.group(1) if version else None,
                "device": job["project"]["device"], "timing": timing,
                "resources": td_report_parser.parse_resource_text(log), "power": unknown_power(),
                "bit": bit.relative_to(folder).as_posix() if bit.is_file() else None,
                "bit_sha256": file_hash(bit) if bit.is_file() else None,
                "functional": {"post_map": "not_run", "hardware": "not_tested"},
                "warnings": [l for l in log.splitlines() if "WARNING" in l][:20],
                "log_tail": "\n".join(log.splitlines()[-20:])}

    def asic(self, job, folder):
        result = remote_execute(job["configuration"], {
            "action": "run", "job_id": job["id"], "project": job["project"],
            "archive": archive_source(folder / "source"),
        })
        packed = result.pop("artifact_archive", None)
        if packed:
            extract_snapshot(base64.b64decode(packed, validate=True), folder / "asic")
        raw = result.pop("metrics", {})
        # Keep original metrics as a source artifact; expose finite normalized numbers only.
        result.update(parse_orfs_metrics(raw))
        if result.get("rtl_sha256") != job["manifest"]["rtl_sha256"]:
            result["ok"] = False
            result["error"] = result.get("error", "服务器 RTL 校验值不一致")
        return result

    def program(self, job_id, confirmation):
        if confirmation != "PROGRAM SRAM":
            raise ValueError("必须明确确认本次 SRAM 烧录")
        with self.lock:
            if self.busy:
                raise ValueError("已有任务运行中")
            self.busy = True
        try:
            job = self.get(job_id)
            fpga = job.get("fpga") or {}
            if not fpga.get("ok") or not fpga.get("bit"):
                raise ValueError("该任务没有成功生成位流")
            if not (job.get("simulation") or {}).get("ok") or job["status"] in ("running", "queued", "interrupted"):
                raise ValueError("RTL 仿真未通过或任务尚未结束，不能烧录")
            folder = self.directory(job_id)
            bit = (folder / fpga["bit"]).resolve()
            if not bit.is_relative_to(folder.resolve()) or file_hash(bit) != fpga["bit_sha256"]:
                raise ValueError("位流已被修改，请重新编译")
            exe = Path(job["configuration"]["td_root"]) / "bin/td_commands_prompt.exe"
            script = folder / "program.tcl"
            script.write_text("download -bit {" + bit.as_posix() + "} -cable 0\nexit\n", encoding="utf-8")
            board_attempt = begin_download(folder.resolve(), fpga['bit_sha256'])
            code, log, elapsed = execute([str(exe), str(script)], folder, folder / "program.log", 180)
            ok = code == 0 and "download success" in log.lower() and not re.search(r"\b(ERROR|FATAL)\b", log)
            job["programming"] = {"ok": ok, "at": now(), "mode": "SRAM", "duration_s": elapsed,
                                  "bit_sha256": fpga["bit_sha256"], "log_tail": log[-2500:], 'board_attempt': board_attempt}
            self.save(job)
            finish_download(board_attempt, ok)
            return job["programming"]
        finally:
            self.busy = False

    def record_power(self, job_id, values):
        with self.lock:
            if self.busy:
                raise ValueError("请等待任务完成")
            self.busy = True
        try:
            return self._record_power(job_id, values)
        finally:
            self.busy = False

    def _record_power(self, job_id, values):
        job = self.get(job_id)
        total = number(values.get("total_mw"))
        frequency = number(values.get("frequency_mhz"))
        if total is None or total < 0 or frequency is None or frequency <= 0:
            raise ValueError("功耗及频率必须是有限有效数值")
        if values.get("method") not in ("td_estimator", "measured"):
            raise ValueError("请选择 TD 估计或实测")
        if values.get("scope") not in ("fpga_chip", "whole_board"):
            raise ValueError("请说明芯片或整板范围")
        if not (job.get("fpga") or {}).get("ok"):
            raise ValueError("该任务没有 FPGA 编译结果")
        if job["status"] in ("running", "queued"):
            raise ValueError("请等待任务完成")
        source = Path(values.get("evidence_path", ""))
        activity = str(values.get("activity", "")).strip()
        if not activity or not source.is_file() or source.stat().st_size > 10 * 1024 * 1024:
            raise ValueError("请提供活动条件和不超过 10 MB 的原始报告/测量证据文件")
        out = self.directory(job_id) / "power-evidence"
        out.mkdir(exist_ok=True)
        evidence = out / (uuid.uuid4().hex[:8] + source.suffix)
        shutil.copyfile(source, evidence)
        job["fpga"]["power"] = {k: values[k] for k in ("method", "scope")}
        job["fpga"]["power"].update(total_mw=total, frequency_mhz=frequency, activity=activity,
                                    evidence=evidence.relative_to(self.directory(job_id)).as_posix(),
                                    evidence_sha256=file_hash(evidence), recorded_at=now(),
                                    rtl_sha256=job["manifest"]["rtl_sha256"],
                                    comparable_to_asic=False)
        self.save(job)
        return job["fpga"]["power"]

    def check(self):
        config = load_config()
        local = {name: Path(value).is_file() for name, value in {
            "td": str(Path(config["td_root"]) / "bin/td_commands_prompt.exe"),
            "license": str(Path(config["td_root"]) / "license/Anlogic.lic"),
            "iverilog": config["iverilog"], "vvp": config["vvp"]}.items()}
        try:
            remote = remote_execute(config, {"action": "check"})
        except Exception as exc:
            remote = {"ok": False, "error": str(exc)}
        return {"local": local, "remote": remote}
