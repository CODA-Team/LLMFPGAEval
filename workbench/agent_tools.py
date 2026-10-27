"""Domain tools for generated RTL. Evidence is outside the editable source tree."""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from .output_io import RESOURCE_DEFAULTS, log_tail, log_contains, text_page

RUN_LIMIT_FIELDS = {
    "functional": "max_functional_runs",
    "implementation": "max_implementation_runs",
    "post_route": "max_post_route_runs",
    "query": "max_query_runs",
}
RUN_COUNTERS = {
    "functional": "simulations", "implementation": "implementations",
    "post_route": "post_route_simulations", "query": "queries",
}
RUN_LABELS = {"functional": "功能仿真", "implementation": "TD 实现", "post_route": "后仿真", "query": "工具查询"}
_DEFAULT_TIMEOUT = object()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class ExperimentTools:
    def __init__(self, folder, config=None):
        self.folder = Path(folder).resolve()
        self.source = self.folder / "source"
        self.source.mkdir(parents=True, exist_ok=True)
        if config is not None:
            from .config import DEFAULTS
            self.config = {key: config.get(key, DEFAULTS[key]) for key in
                           ("iverilog", "vvp", "simulation_backend", "modelsim_root", "modelsim_license", "td_root")}
            self.config.update({key: config.get(key, default) for key, default in RESOURCE_DEFAULTS.items()})
            write_json(self.folder / "tool-config.json", self.config)
        else:
            self.config = read_json(self.folder / "tool-config.json")
        if not self.config:
            raise ValueError("仿真工具配置不存在")
        self.config = {**RESOURCE_DEFAULTS, **self.config}

    def event(self, kind, **data):
        item = {"time": timestamp(), "type": kind, **data}
        with (self.folder / "events.jsonl").open("a", encoding="utf-8") as out:
            out.write(json.dumps(item, ensure_ascii=False) + "\n")

    def begin_turn(self, turn_id, allow_testbench_changes=False, run_limits=None):
        limits = {stage: None for stage in RUN_LIMIT_FIELDS}
        if run_limits is not None:
            if not isinstance(run_limits, dict) or set(run_limits) - set(limits):
                raise ValueError("运行次数配置字段无效")
            for stage, value in run_limits.items():
                if value is not None and (type(value) is not int or not 1 <= value <= 2147483647):
                    raise ValueError(RUN_LABELS[stage] + "次数上限应留空或填写正整数")
            limits.update(run_limits)
        (self.folder / "cancel.flag").unlink(missing_ok=True)
        write_json(self.folder / "control.json", {"turn_id": turn_id, "run_limits": limits,
                                                 **{counter: 0 for counter in RUN_COUNTERS.values()}})
        # Generated tests are part of the editable design, not an independent oracle.
        # Retire the old automatic lock when an existing experiment continues.
        legacy_lock = self.folder / "locked-testbench.json"
        if legacy_lock.exists():
            legacy_lock.unlink()
            self.event("testbench_policy_updated", turn_id=turn_id)

    def run_budget(self):
        control = read_json(self.folder / "control.json", {})
        limits = control.get("run_limits", {})
        return {stage: {"limit": limits.get(stage), "used": control.get(RUN_COUNTERS[stage], 0),
                        "remaining": None if limits.get(stage) is None else
                        max(0, limits[stage] - control.get(RUN_COUNTERS[stage], 0))}
                for stage in RUN_LIMIT_FIELDS}

    def reserve_run(self, stage):
        """Reserve an actual attempt inside execution_lease, shared by all adapters.

        Read the turn snapshot every time: MCP may outlive the previous user turn.
        Missing snapshots from older experiments use the unlimited defaults.
        """
        self.active()
        if stage not in RUN_COUNTERS:
            raise ValueError("未知运行阶段")
        control = read_json(self.folder / "control.json", {"turn_id": "manual"})
        limit = control.get("run_limits", {}).get(stage)
        counter = RUN_COUNTERS[stage]
        used = control.get(counter, 0)
        if limit is not None and used >= limit:
            raise ValueError(f"本轮{RUN_LABELS[stage]}已达到 {limit} 次上限；请总结已有结果，可调整网页设置后发送新消息继续。")
        control[counter] = used + 1
        write_json(self.folder / "control.json", control)
        return control

    def cancel(self):
        (self.folder / "cancel.flag").touch()

    def active(self):
        if (self.folder / "cancel.flag").exists():
            raise ValueError("本轮已停止，请从网页发送新消息继续")

    def path(self, relative):
        if not isinstance(relative, str) or not re.fullmatch(r"(?:(?:rtl|tb)/[A-Za-z0-9_-]+\.(?:sv|v)|constraints/[A-Za-z0-9_-]+\.(?:sdc|tcl)|scripts/[A-Za-z0-9_-]+\.(?:tcl|do))", relative):
            raise ValueError("允许 rtl/、tb/ 下的 .v/.sv，constraints/ 下的 .sdc/.tcl，scripts/ 下的 .tcl/.do")
        path = self.source / relative
        if not path.resolve().is_relative_to(self.source) or any(p.is_symlink() for p in (path, path.parent, self.source)):
            raise ValueError("文件路径超出实验目录")
        return path

    def files(self):
        result = {}
        for group in ("rtl", "tb", "constraints", "scripts"):
            for candidate in sorted((self.source / group).glob("*")):
                path = self.path(candidate.relative_to(self.source).as_posix())
                if path.is_file():
                    result[path.relative_to(self.source).as_posix()] = path.read_text(encoding="utf-8")
        return result

    @staticmethod
    def digest(files):
        h = hashlib.sha256()
        for name, text in sorted(files.items()):
            h.update(name.encode() + b"\0" + text.encode() + b"\0")
        return h.hexdigest()

    def call(self, name, arguments):
        if name not in ("list_files", "read_file", "templates", "read_run", "board_info"):
            self.active()
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须为对象")
        if name == "list_files":
            return {"files": [{"path": name, "bytes": len(text.encode())} for name, text in self.files().items()],
                    "testbench_locked": False,
                    "simulation": self.summary()}
        if name == "read_file":
            path = self.path(arguments.get("path"))
            if 'start_line' in arguments or 'max_lines' in arguments:
                return {'path': arguments['path'], **text_page(path, arguments.get('start_line', 1), arguments.get('max_lines', 80))}
            return {"path": arguments["path"], "content": path.read_text(encoding="utf-8")}
        if name == "write_file":
            path = self.path(arguments.get("path"))
            content = arguments.get("content")
            if not isinstance(content, str) or "\0" in content:
                raise ValueError("源码必须为文本，不能包含 NUL")
            maximum = self.config['max_source_file_kib']
            if maximum is not None and len(content.encode()) > maximum * 1024:
                raise ValueError(f"文件超过已设置的 {maximum} KiB 上限，可在 TD 与仿真器设置中调整")
            files = self.files()
            maximum = self.config['max_source_files']
            if maximum is not None and arguments["path"] not in files and len(files) >= maximum:
                raise ValueError(f"实验已达到设置的 {maximum} 个源文件上限，可在 TD 与仿真器设置中调整")
            old = files.get(arguments["path"], "")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            diff = "".join(difflib.unified_diff(old.splitlines(True), content.splitlines(True),
                                             fromfile=arguments["path"], tofile=arguments["path"]))
            self.event("file_changed", path=arguments["path"], diff=diff[:24000],
                       sha256=hashlib.sha256(content.encode()).hexdigest())
            return {"saved": arguments["path"], "bytes": len(content.encode()), "simulation_required": True}
        if name == "simulate":
            return self.simulate()
        if name == "templates":
            from .eda_templates import templates
            return templates(**arguments)
        if name in ("run_tool", "read_run"):
            from .eda_scripts import EdaScripts
            return getattr(EdaScripts(self), name)(**arguments)
        if name in ('board_info', 'create_project', 'implement', 'post_route'):
            from .fpga_flow import FpgaFlow
            return getattr(FpgaFlow(self), name)(**arguments)
        raise ValueError("未知工具: " + str(name))

    def _validate_source(self, files):
        maximum = self.config['max_source_files']
        if maximum is not None and len(files) > maximum:
            raise ValueError(f"工程超过设置的 {maximum} 个源文件上限，可在网页中调整")
        maximum = self.config['max_source_file_kib']
        if maximum is not None:
            for name, content in files.items():
                if len(content.encode()) > maximum * 1024:
                    raise ValueError(f"{name} 超过设置的 {maximum} KiB 上限，可在网页中调整")
        # This is a local teaching runner, not an OS sandbox for hostile HDL.
        # Block file/shell system tasks; simulation needs only ordinary SV checks.
        allowed = set("display write fatal finish stop error warning info dumpfile dumpvars dumpon dumpoff dumpall dumplimit time realtime stime clog2 bits signed unsigned isunknown countones countbits onehot onehot0 random urandom urandom_range itor rtoi bitstoreal realtobits shortrealtobits bitstoshortreal sformatf ceil floor pow ln log10 sqrt exp sin cos tan asin acos atan atan2 hypot sinh cosh tanh asinh acosh atanh size low high left right dimensions unpacked_dimensions increment typename printtimescale timeformat test$plusargs value$plusargs".split())
        for name, content in files.items():
            if not name.startswith(('rtl/', 'tb/')):
                continue
            text = re.sub(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*[\s\S]*?\*/',
                          lambda m: m[0] if m[0].startswith('"') else " ", content)
            for match in re.finditer(r'"(?:\\.|[^"\\])*"|\$([A-Za-z_][A-Za-z0-9_$]*)', text):
                task = match[1]
                if task is None:
                    continue
                if task not in allowed:
                    raise ValueError(name + ": 不允许的系统调用 $" + task)
                # Require a literal call, including when the token is in a macro
                # definition. A bare alias could otherwise expand to an external path.
                if task == "dumpfile" and not re.match(r'\s*\(\s*"wave\.vcd"\s*\)', text[match.end():]):
                    raise ValueError(name + ': $dumpfile 只允许直接调用 $dumpfile("wave.vcd")')
            masked = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
            if re.search(r"`(?:include|line)\b|``", masked):
                raise ValueError(name + ": 当前实验不支持外部 include、line 或宏拼接")

    def _run(self, command, cwd, logfile, env=None, timeout=_DEFAULT_TIMEOUT):
        started = time.monotonic()
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        reason = None
        detail = None
        issue_file = cwd / 'execution_issue.json'
        issue_file.unlink(missing_ok=True)
        process_timeout = self.config['simulation_timeout_s'] if timeout is _DEFAULT_TIMEOUT else timeout
        child_env = {k: v for k, v in os.environ.items() if not any(x in k.upper() for x in ('API_KEY', 'OPENCODE_SERVER_PASSWORD', 'OPENCODE_CONFIG_CONTENT'))}
        child_env.update(env or {})
        def oversized():
            paths = [(p, 'log_limit_mib') for p in cwd.glob('*.log')] + [(cwd / 'commands.jsonl', 'log_limit_mib'),
                     (cwd / 'transcript', 'log_limit_mib'), (cwd / 'wave.vcd', 'waveform_limit_mib'),
                     (cwd / 'vsim.wlf', 'waveform_limit_mib')]
            for path, setting in paths:
                limit = self.config[setting]
                if limit is not None and path.is_file() and path.stat().st_size > limit * 1024 * 1024:
                    return {'kind': 'output_limit', 'file': path.name, 'actual_bytes': path.stat().st_size,
                            'limit_bytes': limit * 1024 * 1024, 'setting': setting}
            return None
        with logfile.open("wb") as output:
            process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, env=child_env, **options)
            try:
                while process.poll() is None:
                    if (self.folder / "cancel.flag").exists():
                        reason = "cancelled"
                    elif process_timeout is not None and time.monotonic() - started > process_timeout:
                        reason = "timeout"
                        detail = {'kind': 'timeout', 'timeout_s': process_timeout}
                    elif (detail := oversized()):
                        reason = "output_limit"
                    if reason:
                        if os.name == "nt":
                            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
                        else:
                            process.kill()
                        break
                    time.sleep(.05)
                process.wait(timeout=5)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        # Recheck after exit: a fast process can exceed the cap between polls.
        if (final_detail := oversized()):
            detail = final_detail if reason in (None, 'output_limit') else detail
            reason = reason or "output_limit"
        if detail:
            write_json(issue_file, detail)
        return process.returncode, log_tail(logfile), reason

    @staticmethod
    def execution_diagnostics(folder, result):
        detail = read_json(Path(folder) / 'execution_issue.json')
        if detail:
            result['execution_issue'] = detail
            if detail['kind'] == 'output_limit':
                result['explanation'] = (f"{detail['file']} 大小 {detail['actual_bytes']:,} bytes，"
                    f"超过已设置的 {detail['limit_bytes']:,} bytes；在 TD 与仿真器设置中修改 {detail['setting']}，留空不限制。")
            elif detail['kind'] == 'timeout':
                result['explanation'] = f"本次工具运行超过已设置的 {detail['timeout_s']} 秒；可在 TD 与仿真器设置中调整或清空。"

    def simulate(self):
        from .fpga_flow import execution_lease
        with execution_lease(self.folder):
            return self._simulate()

    def _simulate(self):
        from .fpga_flow import project_digest
        self.active()
        files = self.files()
        if not any(p.startswith("rtl/") for p in files) or not any(p.startswith("tb/") for p in files):
            raise ValueError("先写入 RTL 和 Testbench；Testbench 顶层模块必须命名为 tb")
        self._validate_source(files)
        control = self.reserve_run("functional")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:8]
        folder = self.folder / "evidence" / run_id
        source = folder / "source"
        for name, content in files.items():
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        result = {"id": run_id, "turn_id": control["turn_id"], "time": timestamp(), "ok": False,
                  "project_sha256": project_digest(self.folder),
                  "status": "running", "source_sha256": self.digest(files), "tests_passed": [],
                  "testbench_sha256": self.digest({p: c for p, c in files.items() if p.startswith("tb/")}),
                  "files": list(files), "round": control["simulations"], "verification": "generated_self_tests"}
        write_json(folder / "result.json", result)
        self.event("simulation_started", id=run_id, round=result["round"])
        start = time.monotonic()
        if self.config.get('simulation_backend', 'modelsim') == 'modelsim':
            from .simulation import run_modelsim
            try:
                result.update(run_modelsim(self.config, folder,
                    [source / n for n in files if n.startswith('rtl/')],
                    [source / n for n in files if n.startswith('tb/')], self._run))
            except OSError as error:
                result.update(status='tool_error', engine='modelsim', log_tail=str(error))
            return self._finish_simulation(folder, result, start)
        result.update(engine='icarus', stage='functional')
        try:
            command = [self.config["iverilog"], "-g2012", "-s", "tb", "-o", str(folder / "sim.vvp")]
            command += [str(source / name) for name in files if name.startswith(('rtl/', 'tb/'))]
            code, log, reason = self._run(command, folder, folder / "compile.log")
            result.update(compile_exit_code=code, log_tail=log[-16000:])
            if code != 0 or reason:
                result["status"] = reason or "compile_failed"
            else:
                code, log, reason = self._run([self.config["vvp"], "-n", str(folder / "sim.vvp")], folder, folder / "simulation.log")
                with (folder / 'simulation.log').open(encoding='utf-8', errors='replace') as source:
                    checks = [m[1] for line in source if (m := re.match(r'^TEST_PASS\s+([^\r\n]+)$', line))]
                marker = log_contains(folder / 'simulation.log', r'^ALL_TESTS_PASSED\s*$')
                failure = log_contains(folder / 'simulation.log', r'^\s*(ERROR|FATAL|FAIL|FAILED)\b', re.I)
                ok = code == 0 and bool(checks) and bool(marker) and not failure and not reason
                result.update(exit_code=code, log_tail=log[-16000:], tests_passed=checks, ok=ok,
                              status=reason or ("passed" if ok else "failed" if code != 0 or failure else "inconclusive"))
                if result["status"] == "inconclusive":
                    result["explanation"] = "需要实际自检后输出 TEST_PASS <名称>，全部通过后输出 ALL_TESTS_PASSED。"
        except OSError as error:
            result.update(status="tool_error", log_tail=str(error))
        return self._finish_simulation(folder, result, start)

    def _finish_simulation(self, folder, result, start):
        from .simulation import evidence_manifest
        from .fpga_flow import project_digest
        self.execution_diagnostics(folder, result)
        result['evidence_hashes'] = evidence_manifest(folder)
        from .run_inputs import input_state
        files = self.files()
        result.update(input_state(folder, result, 'functional', files, self.digest(files), project_digest(self.folder)))
        result['inputs_changed_during_run'] = not result['inputs_current']
        result["duration_s"] = round(time.monotonic() - start, 3)
        result["waveform"] = (folder / "wave.vcd").is_file()
        write_json(folder / "result.json", result)
        self.event("simulation_finished", id=result['id'], status=result["status"], ok=result["ok"])
        return result

    def summary(self):
        from .fpga_flow import project_digest
        from .simulation import evidence_valid
        runs = sorted((read_json(p) for p in (self.folder / "evidence").glob("*/result.json")), key=lambda r: r["time"])
        for run in runs:
            run['waveform'] = (self.folder / 'evidence' / run['id'] / 'wave.vcd').is_file()
        latest = runs[-1] if runs else None
        files = self.files()
        digest = self.digest(files)
        valid = not latest or 'evidence_hashes' not in latest or evidence_valid(self.folder / 'evidence' / latest['id'], latest['evidence_hashes'])
        if latest:
            from .run_inputs import input_state
            latest.update(input_state(self.folder / 'evidence' / latest['id'], latest, 'functional', files, digest, project_digest(self.folder)))
            latest['evidence_valid'] = bool(valid)
        return {"runs": runs, "latest": latest, "source_sha256": digest,
                "verified_current": bool(latest and latest["ok"] and latest['inputs_current'] and valid),
                "testbench_locked": False}


TOOL_SCHEMAS = [
    {"name": "list_files", "description": "List editable RTL, Testbench, constraints and scripts with current simulation status.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "read_file", "description": "Read an editable experiment file: RTL, Testbench, constraints or Tcl/DO scripts.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "minimum": 1}, "max_lines": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["path"], "additionalProperties": False}},
    {"name": "write_file", "description": "Create/replace rtl/*.v or .sv, tb/*.v or .sv, constraints/*.tcl or .sdc, scripts/*.tcl or .do. Templates never overwrite these files. Generated Testbenches, scripts and constraints stay editable; preserve meaningful assertions when fixing tests.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}},
    {"name": "simulate", "description": "Run actual configured simulator (default ModelSim) for RTL functional simulation. Testbench top tb, DUT instance dut. Returns real logs, checks and source/project hashes. Attempts default to unlimited; board_info.run_budget reports any configured per-turn limits. Use $fatal(1,...) on failures, TEST_PASS <name> after assertions and ALL_TESTS_PASSED after all checks; optional $dumpfile(\"wave.vcd\").",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
]
