"""LLM Agent 结构化 CLI（被 anlogic/td_flow.py 调用）。

提供：
    td_flow.py sim   <run_sim.sh> [--json]
    td_flow.py flow  <flow.tcl>   [--json] [--reuse-report] [--timeout N]
    td_flow.py program <program.tcl> [--json]

--json 时 stdout 只输出一个 JSON（契约见 tools/result_schema.py），
人类可读日志写 stderr，同时原始日志和 JSON 结果各落盘一份到 work/logs/。

没有 --json 时：
  - sim 输出人类可读摘要（旧版没有 sim 子命令，不存在兼容问题）
  - flow / program 仍走 td_flow.py 原有逻辑（本模块直接返回 None）
"""
from __future__ import annotations

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime

try:
    from . import error_classifier as classifier
    from . import result_schema as schema
    from . import td_report_parser as parser
except ImportError:  # 允许直接跑 tools/ 下的脚本
    import error_classifier as classifier
    import result_schema as schema
    import td_report_parser as parser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, "work", "logs")

DEFAULT_TIMEOUT = 1800


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]


def _rel(path):
    if not path:
        return None
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return path


def _write_outputs(kind, raw_log, result):
    """写 work/logs/<kind>_<timestamp>.log 和同名 .json，返回 (log_path, json_path)。"""
    os.makedirs(LOG_DIR, exist_ok=True)
    base = f"{kind}_{_now_stamp()}"
    log_path = os.path.join(LOG_DIR, base + ".log")
    json_path = os.path.join(LOG_DIR, base + ".json")
    with open(log_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(raw_log or "")
    result["log_file"] = _rel(log_path)
    with open(json_path, "w", encoding="utf-8") as f:
        f.write(schema.dumps(result) + "\n")
    return log_path, json_path


def _run(cmd, cwd, timeout):
    """执行子进程，返回 (returncode, combined_output, duration_s, error_text)。"""
    started = time.time()
    try:
        cp = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return cp.returncode, (cp.stdout or "") + (cp.stderr or ""), \
            time.time() - started, None
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout or ""
        err = exc.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return -9, out + err, time.time() - started, f"超时（{timeout}s）"
    except OSError as exc:
        return -1, "", time.time() - started, str(exc)


def _find_bash():
    """优先 Git Bash；显式排除 System32 下的 WSL bash。"""
    candidates = []
    which = shutil.which("bash")
    if which:
        candidates.append(which)
    candidates += [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
        r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
    ]
    for cand in candidates:
        if cand and os.path.exists(cand) and "system32" not in cand.lower():
            return cand
    return which  # 只剩 WSL 时返回它，调用方会检测失败并降级


def _decode_maybe(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else (value or "")


# ---------------------------------------------------------------------------
# sim
# ---------------------------------------------------------------------------

def _run_sim_direct(script, timeout):
    """没有可用 bash 时，按 run_sim.sh 内容直接调用 iverilog/vvp。

    只解析脚本里真实存在的模板行，不硬编码任何工具路径或文件名：
      IVERILOG="${IVERILOG:-<path>}"
      VVP="${VVP:-<path>}"
      run_one <name> <tb>
      "<exe>" <args...>   （含 $name / $tb）
    """
    try:
        with open(script, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as exc:
        return -1, "", 0.0, f"无法读取仿真脚本: {exc}"

    def _var(name):
        m = re.search(
            name + r'="\$\{' + name + r':-([^}]+)\}"', text)
        return m.group(1) if m else None

    iverilog = _var("IVERILOG")
    vvp = _var("VVP")
    if not iverilog or not vvp:
        return -1, "", 0.0, "脚本中找不到 IVERILOG/VVP 默认路径"

    # 处理 shell 续行：模板行在脚本里是 "\" 换行的
    joined = re.sub(r"\\\r?\n", " ", text)
    lines = joined.splitlines()
    comp_tpl = next((ln.strip() for ln in lines if '"$IVERILOG"' in ln), None)
    run_tpl = next((ln.strip() for ln in lines if '"$VVP"' in ln), None)
    if not comp_tpl or not run_tpl:
        return -1, "", 0.0, "脚本中找不到 iverilog/vvp 调用模板"

    runs = re.findall(r"^\s*run_one\s+(\S+)\s+(\S+)\s*$", text, re.M)
    if not runs:
        return -1, "", 0.0, "脚本中找不到 run_one 调用"

    exp_dir = os.path.abspath(os.path.join(os.path.dirname(script), ".."))

    _SHELL_STOP = {"|", "||", "&&", ";", ">", ">>", "<", "&"}

    def _expand(tpl, name, tb):
        tokens = shlex.split(tpl, posix=True)
        exe = tokens[0]
        args = []
        for tok in tokens[1:]:
            # 截掉 shell 控制符/重定向，只保留真正的工具参数
            if tok in _SHELL_STOP or re.match(r"^\d?>>?&?1?$", tok) \
                    or tok.startswith("2>"):
                break
            tok = tok.replace("$name", name).replace("$tb", tb)
            args.append(tok)
        return exe, args

    # 输出目录从脚本的 -o 参数解析，不写死 sim/build
    _, probe_args = _expand(comp_tpl, "__probe__", "__probe__.v")
    for i, arg in enumerate(probe_args):
        if arg == "-o" and i + 1 < len(probe_args):
            out_dir = os.path.dirname(probe_args[i + 1])
            if out_dir:
                os.makedirs(os.path.join(exp_dir, out_dir), exist_ok=True)
            break

    log_parts = []
    overall_rc = 0
    started = time.time()

    for name, tb in runs:
        remaining = max(1, int(timeout - (time.time() - started)))
        if time.time() - started > timeout:
            log_parts.append(f"\n[agent_cli] 总超时 {timeout}s，停止后续仿真\n")
            overall_rc = -9
            break

        _, comp_args = _expand(comp_tpl, name, tb)
        cmd = [iverilog] + comp_args
        rc, out, _, err = _run(cmd, exp_dir, remaining)
        log_parts.append(out)
        if err:
            log_parts.append(f"[agent_cli] {err}\n")
        if rc != 0:
            log_parts.append(f"[agent_cli] 编译失败: {name} (exit {rc})\n")
            overall_rc = rc or 1
            continue

        _, run_args = _expand(run_tpl, name, tb)
        cmd = [vvp] + run_args
        rc, out, _, err = _run(cmd, exp_dir, remaining)
        log_parts.append(out)
        if err:
            log_parts.append(f"[agent_cli] {err}\n")
        log_parts.append("\n" + ("PASSED" if rc == 0 else f"FAILED: {name} (exit {rc})") + ": " + name + "\n")
        if rc != 0:
            overall_rc = rc or 1

    return overall_rc, "".join(log_parts), time.time() - started, None


def handle_sim(script, timeout=DEFAULT_TIMEOUT, json_mode=False):
    script = os.path.abspath(script)
    if not os.path.exists(script):
        return _emit_fatal("sim", 2, f"找不到仿真脚本: {script}", json_mode)

    bash = _find_bash()
    run_error = None
    if bash:
        rc, log, duration, run_error = _run([bash, script], ROOT, timeout)
        # WSL bash 在这台机器上会报 "Windows Subsystem for Linux..." / execvpe
        if run_error or (rc != 0 and re.search(
                r"(Windows Subsystem for Linux|\bWSL\b|execvpe|/bin/bash: No such file)",
                log, re.I)):
            rc2, log2, dur2, err2 = _run_sim_direct(script, timeout)
            if err2 is None and (rc2 == 0 or "error:" in log2.lower()
                                 or "FAIL" in log2 or "ERROR" in log2):
                rc, log, duration = rc2, log2, dur2
                run_error = None
    else:
        rc, log, duration, run_error = _run_sim_direct(script, timeout)

    if run_error and not log:
        log = run_error

    pass_count, fail_count, failed = classifier.parse_pass_fail(log)
    sections = [s for s in classifier.parse_sim_sections(log)
                if s["pass"] or s["fail"]]
    errors = classifier.classify_log(log, "sim", rc)

    # 兜底：脚本自身非零退出但没识别出具体错误
    if rc != 0 and not any(e["severity"] == "error" for e in errors):
        errors.append(schema.make_error(
            "sim_fail", f"仿真脚本退出码 {rc}",
            raw="\n".join(log.splitlines()[-5:])))

    ok = (rc == 0) and (fail_count == 0) and \
        not any(e["severity"] == "error" for e in errors)

    sim = {
        "tb": os.path.basename(script),
        "pass": pass_count,
        "fail": fail_count,
        "failed_checks": failed,
        "script": _rel(script),
        "testbenches": sections,
    }

    result = schema.make_result(
        stage="sim", ok=ok, exit_code=rc, duration_s=duration,
        errors=errors, sim=sim, timing=None,
        resources=None, artifacts={"bit": None, "reports": []},
        log_tail=schema.tail(log, 20),
    )
    _write_outputs("sim", log, result)

    if json_mode:
        sys.stderr.write(log if log.endswith("\n") else log + "\n")
        print(schema.dumps(result))
    else:
        print(f"SIM {'PASS' if ok else 'FAIL'}: {sim['tb']}  "
              f"pass={pass_count} fail={fail_count} "
              f"exit={rc} time={duration:.2f}s")
        for err in errors:
            print(f"  [{err['type']}/{err['severity']}] {err['message']}")
        print(f"log: {result['log_file']}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------

def _latest_td_log(directory, require_resource=False):
    """找最新的 td_*.log。

    program 日志也是 td_*.log，但没有综合资源信息。--reuse-report 时
    传 require_resource=True，跳过这类日志。
    """
    try:
        names = [n for n in os.listdir(directory)
                 if re.match(r"td_\d{8}_\d{6}\.log$", n)]
    except OSError:
        return None
    if not names:
        return None
    names.sort(key=lambda n: os.path.getmtime(os.path.join(directory, n)),
               reverse=True)
    first = os.path.join(directory, names[0])
    if not require_resource:
        return first
    for name in names:
        path = os.path.join(directory, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if re.search(r"#lut\s*=|Packed\s+\d+\s+LUT\s+and\s+\d+\s+SEQ|"
                     r"\d+\s+brams?\b", text, re.I):
            return path
    return first


def handle_flow(tcl_file, flow_runner, timeout=DEFAULT_TIMEOUT,
                json_mode=False, reuse_report=False):
    tcl_file = os.path.abspath(tcl_file)
    if not os.path.exists(tcl_file):
        return _emit_fatal("flow", 2, f"找不到 flow.tcl: {tcl_file}", json_mode)

    tcl_dir = os.path.dirname(tcl_file)
    paths = parser.parse_report_paths(tcl_file)

    duration = 0.0
    legacy_ok = True
    raw_log = ""
    rc = 0
    log_for_classify = ""

    if not reuse_report:
        started = time.time()
        # run_flow() 会 print("[TD] ...")，JSON 模式下必须改道 stderr
        with contextlib.redirect_stdout(sys.stderr):
            legacy_ok, raw_log = flow_runner(tcl_file, timeout=timeout)
        duration = time.time() - started
        log_for_classify = raw_log
        # flow_runner 不返回退出码；失败时统一记 1（具体原因由分类器从日志抓）
        rc = 0 if legacy_ok else 1

    # 资源占用：本轮日志；--reuse-report 时退回到最新的、含综合信息的 TD 日志。
    # 不额外复制一份 raw log —— _write_outputs() 会统一落盘。
    resource_text = raw_log
    if not resource_text:
        td_log = _latest_td_log(tcl_dir, require_resource=True)
        if td_log:
            try:
                with open(td_log, "r", encoding="utf-8", errors="replace") as f:
                    raw_log = f.read()
            except OSError:
                raw_log = ""
            resource_text = raw_log

    errors = []
    timing = None
    timing_path = paths.get("timing")
    if timing_path and os.path.exists(timing_path):
        timing = parser.parse_timing(timing_path)
        if timing.get("wns_ns") is None:
            errors.append(schema.make_error(
                "tool", f"未能从时序报告解析 WNS: {_rel(timing_path)}",
                severity="warning"))
    else:
        errors.append(schema.make_error(
            "tool", f"找不到时序报告: {_rel(timing_path)}", severity="warning"))

    clock_path = paths.get("clock_summary")
    if clock_path and os.path.exists(clock_path):
        clock = parser.parse_clock_summary(clock_path)
        if timing is not None:
            timing["clocks"] = clock.get("clocks", [])
            timing["clock_groups"] = clock.get("groups", [])
        if clock.get("parse_error"):
            errors.append(schema.make_error(
                "tool", clock["parse_error"], severity="warning"))
    else:
        errors.append(schema.make_error(
            "tool", f"找不到时钟摘要: {_rel(clock_path)}", severity="warning"))

    resources = parser.parse_resource_text(resource_text)
    if all(v is None for v in resources.values()):
        errors.append(schema.make_error(
            "tool", "未能从综合日志解析 LUT/FF/BRAM，保持 null", severity="warning"))

    # 日志分类放在 timing 解析之后，WNS < 0 时一并产出 timing 错误。
    # 注意：用 += 而不是 =，保留上面报告中缺字段的 warning。
    errors += classifier.classify_log(log_for_classify, "flow", rc,
                                      timing=timing)

    ok = legacy_ok and not any(e["severity"] == "error" for e in errors)

    reports = [p for p in (timing_path, clock_path) if p and os.path.exists(p)]
    bit_path = paths.get("bit")
    artifacts = {
        "bit": _rel(bit_path) if bit_path and os.path.exists(bit_path) else None,
        "reports": [_rel(p) for p in reports],
    }

    result = schema.make_result(
        stage="flow", ok=ok, exit_code=rc, duration_s=duration,
        errors=errors, sim=None, timing=timing, resources=resources,
        artifacts=artifacts, log_tail=schema.tail(raw_log, 20),
    )
    result["reuse_report"] = bool(reuse_report)
    _write_outputs("flow", raw_log, result)

    if json_mode:
        if raw_log:
            sys.stderr.write(raw_log if raw_log.endswith("\n") else raw_log + "\n")
        print(schema.dumps(result))
    else:
        print("SUCCESS" if ok else "FAILED")
        if timing:
            print(f"WNS={timing['wns_ns']} ns  WHS={timing['whs_ns']} ns  "
                  f"min_period={timing['min_period_ns']} ns")
        print(f"log: {result['log_file']}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# program
# ---------------------------------------------------------------------------

def _program_bit_path(tcl_file):
    try:
        with open(tcl_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    m = re.search(r'set\s+BIT_FILE\s+"([^"]+)"', text)
    if not m:
        m = re.search(r"set\s+BIT_FILE\s+(\S+)", text)
    if not m:
        return None
    bit = m.group(1).strip().strip("{}")
    if not os.path.isabs(bit):
        bit = os.path.join(os.path.dirname(os.path.abspath(tcl_file)), bit)
    return os.path.abspath(bit)


def handle_program(tcl_file, program_runner, timeout=DEFAULT_TIMEOUT,
                   json_mode=False):
    tcl_file = os.path.abspath(tcl_file)
    if not os.path.exists(tcl_file):
        return _emit_fatal("program", 2, f"找不到 program.tcl: {tcl_file}",
                           json_mode)

    started = time.time()
    with contextlib.redirect_stdout(sys.stderr):
        legacy_ok, raw_log = program_runner(tcl_file, timeout=timeout)
    duration = time.time() - started
    rc = 0 if legacy_ok else 1

    errors = classifier.classify_log(raw_log, "program", rc)
    if rc != 0 and not any(e["severity"] == "error" for e in errors):
        errors.append(schema.make_error(
            "tool", f"烧录失败（退出码 {rc}）",
            raw="\n".join(raw_log.splitlines()[-5:])))

    bit = _program_bit_path(tcl_file)
    artifacts = {
        "bit": _rel(bit) if bit and os.path.exists(bit) else None,
        "reports": [],
    }
    ok = legacy_ok and not any(e["severity"] == "error" for e in errors)

    result = schema.make_result(
        stage="program", ok=ok, exit_code=rc, duration_s=duration,
        errors=errors, sim=None, timing=None, resources=None,
        artifacts=artifacts, log_tail=schema.tail(raw_log, 20),
    )
    _write_outputs("program", raw_log, result)

    if json_mode:
        sys.stderr.write(raw_log if raw_log.endswith("\n") else raw_log + "\n")
        print(schema.dumps(result))
    else:
        print("SUCCESS" if ok else "FAILED")
        print(f"log: {result['log_file']}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# 参数解析与入口
# ---------------------------------------------------------------------------

def _parse_args(args):
    opts = {
        "json": False,
        "reuse_report": False,
        "timeout": DEFAULT_TIMEOUT,
        "positionals": [],
        "unknown": [],
    }
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--json":
            opts["json"] = True
        elif arg == "--reuse-report":
            opts["reuse_report"] = True
        elif arg == "--timeout":
            if i + 1 >= len(args):
                opts["unknown"].append("--timeout 缺少参数")
                i += 1
                continue
            try:
                opts["timeout"] = int(args[i + 1])
            except ValueError:
                opts["unknown"].append(f"--timeout 非法: {args[i + 1]}")
            i += 1
        elif arg.startswith("--timeout="):
            try:
                opts["timeout"] = int(arg.split("=", 1)[1])
            except ValueError:
                opts["unknown"].append(f"非法参数: {arg}")
        elif arg in ("-h", "--help"):
            opts["unknown"].append("help")
        elif arg.startswith("-"):
            opts["unknown"].append(arg)
        else:
            opts["positionals"].append(arg)
        i += 1
    return opts


def _emit_fatal(stage, code, message, json_mode):
    result = schema.make_result(
        stage=stage if stage in schema.STAGES else "sim",
        ok=False, exit_code=code, duration_s=0.0,
        errors=[schema.make_error("tool", message)],
        sim=None, timing=None, resources=None,
        artifacts={"bit": None, "reports": []}, log_tail=message,
    )
    if json_mode:
        print(schema.dumps(result))
    else:
        print(f"[{stage}] {message}")
    return code


def maybe_handle_agent_argv(argv, runners=None):
    """若 argv 属于结构化接口，执行并返回退出码；否则返回 None（走旧逻辑）。

    runners: {"flow": run_flow, "program": program_board}
    """
    runners = runners or {}
    if len(argv) < 2:
        return None

    opts = _parse_args(argv[1:])
    if not opts["positionals"]:
        if "--json" in argv[1:]:
            return _emit_fatal("sim", 2, "缺少目标文件参数", True)
        return None

    stage = opts["positionals"][0]
    target = opts["positionals"][1] if len(opts["positionals"]) > 1 else None

    if stage == "sim":
        if opts["unknown"]:
            return _emit_fatal(
                "sim", 2, "未知参数: " + ", ".join(opts["unknown"]),
                opts["json"])
        if not target:
            return _emit_fatal("sim", 2, "缺少 run_sim.sh 路径", opts["json"])
        try:
            return handle_sim(target, timeout=opts["timeout"],
                              json_mode=opts["json"])
        except Exception as exc:  # 不把异常吞掉，转成结构化 tool 错误
            return _emit_fatal("sim", 2, f"内部异常: {exc}", opts["json"])

    if stage in ("flow", "program"):
        # 没有 --json：完全不拦截，保持 td_flow.py 原有输出
        if not opts["json"]:
            return None
        if opts["unknown"]:
            return _emit_fatal(
                stage, 2, "未知参数: " + ", ".join(opts["unknown"]), True)
        if not target:
            return _emit_fatal(stage, 2, "缺少目标文件参数", True)
        try:
            if stage == "flow":
                runner = runners.get("flow")
                if runner is None:
                    return _emit_fatal("flow", 2, "内部错误: 未注入 flow runner", True)
                return handle_flow(target, runner, timeout=opts["timeout"],
                                   json_mode=True,
                                   reuse_report=opts["reuse_report"])
            runner = runners.get("program")
            if runner is None:
                return _emit_fatal("program", 2, "内部错误: 未注入 program runner", True)
            return handle_program(target, runner, timeout=opts["timeout"],
                                  json_mode=True)
        except Exception as exc:
            return _emit_fatal(stage, 2, f"内部异常: {exc}", True)

    # check / download 等：保持原有行为
    return None
