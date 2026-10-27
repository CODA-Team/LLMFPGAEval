"""Sent over SSH as source; standard-library only, no installation on the server."""
import base64
import hashlib
import io
import json
import os
import re
import shlex
import signal
import subprocess
import time
import traceback
import zipfile
from pathlib import Path, PurePosixPath


def extract_snapshot(content, destination):
    destination = Path(destination).resolve()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 50 * 1024 * 1024:
            raise ValueError("Snapshot is too large")
        for info in archive.infolist():
            name = PurePosixPath(info.filename)
            if name.is_absolute() or ".." in name.parts or "\\" in info.filename or ":" in info.filename:
                raise ValueError("Unsafe archive path")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Archive symlinks are not allowed")
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError("Archive escapes destination")
        destination.mkdir(parents=True, exist_ok=False)
        archive.extractall(destination)


def checked_path(value):
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value.split("/") or len(Path(value).parts) < 4:
        raise ValueError("Invalid remote path")
    return Path(value)


def verify_manifest(src):
    manifest = json.loads((src / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        path = (src / name).resolve()
        if not path.is_relative_to(src.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Snapshot checksum mismatch: " + name)
    digest = hashlib.sha256()
    for name in sorted(manifest["rtl_files"]):
        digest.update(name.encode() + b"\0" + (src / name).read_bytes() + b"\0")
    if digest.hexdigest() != manifest["rtl_sha256"]:
        raise ValueError("RTL checksum mismatch")
    return manifest


def run(payload):
    config = payload["config"]
    orfs = checked_path(config["orfs_root"])
    env_script = orfs / "env.sh"
    if not env_script.is_file():
        raise ValueError("ORFS env.sh not found: " + str(env_script))
    libraries = [str(orfs / "tools/install/kepler-formal/lib64")]
    libraries.extend(p for p in config.get("remote_library_path", "").split(":") if p)
    environment = ("set -e; source " + shlex.quote(str(env_script)) + " >/dev/null; "
                   + "export LD_LIBRARY_PATH=" + shlex.quote(":".join(libraries)) + ':"$LD_LIBRARY_PATH"; ')
    if payload["action"] == "check":
        script = environment + "command -v openroad; command -v yosys; command -v klayout; openroad -version; yosys -V"
        result = subprocess.run(["bash", "-lc", script], capture_output=True, text=True, timeout=30)
        return {"ok": result.returncode == 0, "log": result.stdout + result.stderr}
    job_id = payload["job_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id):
        raise ValueError("Invalid job ID")
    root = checked_path(config["remote_work_root"])
    root.mkdir(parents=True, exist_ok=True)
    job = root / job_id
    job.mkdir(exist_ok=False)
    src = job / "source"
    extract_snapshot(base64.b64decode(payload["archive"], validate=True), src)
    manifest = verify_manifest(src)
    project = payload["project"]
    top = project["top"]
    platform = config["platform"]
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top) or not re.fullmatch(r"[a-z0-9_-]+", platform):
        raise ValueError("Invalid design/platform")
    if not (orfs / "flow/platforms" / platform / "config.mk").is_file():
        raise ValueError("PDK platform is unavailable: " + platform)
    period = float(project["clock_period_ns"])
    clock = project["clock_port"]
    if not 0.01 <= period <= 100000 or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", clock):
        raise ValueError("Invalid clock")
    # Share the project's SDC when present, rather than inventing a second timing contract.
    sdc = src / project["sdc"]
    if not sdc.resolve().is_relative_to(src.resolve()) or not sdc.is_file():
        raise ValueError("SDC missing")
    cfg = [
        "export DESIGN_NAME = " + top, "export PLATFORM = " + platform,
        "export VERILOG_FILES = " + " ".join(str(src / p) for p in manifest["rtl_files"]),
        "export SDC_FILE = " + str(sdc), "export CLOCK_PERIOD = " + str(period),
        "export CORE_UTILIZATION = 40", "export PLACE_DENSITY_LB_ADDON = 0.20",
        "export TNS_END_PERCENT = 100", "export SYNTH_REPEATABLE_BUILD = 1",
    ]
    if platform == "nangate45":
        cfg.append("export PDN_TCL = " + str(orfs / "flow/designs/nangate45/gcd/grid_strategy-M1-M4-M7.tcl"))
    (job / "config.mk").write_text("\n".join(cfg) + "\n")
    (job / "manifest.json").write_text(json.dumps(manifest, indent=2))
    version = subprocess.run(["git", "-C", str(orfs), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    command = ["make", "-C", str(orfs / "flow"), "DESIGN_CONFIG=" + str(job / "config.mk"),
               "WORK_HOME=" + str(job / "work"), "NUM_CORES=" + str(config["threads"])]
    log_path = job / "orfs.log"
    started = time.monotonic()
    timed_out = False
    with log_path.open("w") as log:
        process = subprocess.Popen(["bash", "-lc", environment + "exec " + shlex.join(command)],
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            rc = process.wait(timeout=config["timeout_s"])
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            rc = 124
    out = job / "work"
    metric_path = out / "logs" / platform / top / "base/6_report.json"
    gds = out / "results" / platform / top / "base/6_final.gds"
    metrics = json.loads(metric_path.read_text()) if metric_path.is_file() else {}
    route_metrics = out / "logs" / platform / top / "base/5_2_route.json"
    if route_metrics.is_file():
        metrics.update(json.loads(route_metrics.read_text()))
    raw_log = log_path.read_text(errors="replace")
    good = rc == 0 and gds.is_file() and gds.stat().st_size > 0 and metric_path.is_file()
    if metrics.get("finish__flow__errors__count", 0) != 0:
        good = False
    # Only download final products and reports; keep large intermediate ODB files on the server.
    packed = io.BytesIO()
    with zipfile.ZipFile(packed, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(log_path, "orfs.log")
        z.write(job / "config.mk", "config.mk")
        for p in out.rglob("*"):
            if p.is_file() and (p.suffix in (".json", ".rpt", ".sdc") or p.name in ("6_final.gds", "6_final.v", "1_2_yosys.v")):
                if p.stat().st_size <= 20 * 1024 * 1024:
                    z.write(p, p.relative_to(out).as_posix())
    return {"ok": good, "exit_code": rc, "timed_out": timed_out,
            "duration_s": round(time.monotonic() - started, 3),
            "remote_directory": str(job), "rtl_sha256": manifest["rtl_sha256"],
            "orfs_commit": version, "platform": platform, "metrics": metrics,
            "log_tail": "\n".join(raw_log.splitlines()[-30:]),
            "artifact_archive": base64.b64encode(packed.getvalue()).decode(),
            "functional": {"post_synthesis": "not_run", "post_route": "not_run", "silicon": "not_tested"}}


def entry(payload):
    try:
        result = run(payload)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "log_tail": traceback.format_exc()}
    # This marker separates structured output from any login banners.
    print("@@WB_RESULT@@" + json.dumps(result, allow_nan=True), flush=True)
