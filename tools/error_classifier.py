"""把 TD / Icarus 原始日志分类成 errors[]。

分类规则（任务阶段 5）：
  - iverilog 的 "error:" / "syntax error"  -> compile（带 file/line）
  - vvp 的 "FAIL:" / "ERROR:" / "FATAL:"   -> sim_fail
  - 时序报告 WNS < 0                        -> timing（带最差路径）
  - .adc 相关 duplicated/not found/invalid pin -> constraint
  - 其他非零退出                            -> tool

raw 字段最多保留 5 行原始日志。
"""
from __future__ import annotations

import re

try:
    from .result_schema import make_error
except ImportError:  # 直接执行 tools/ 下的脚本时
    from result_schema import make_error

# iverilog: "rtl/foo.v:12: error: ..." / "tb.v:9: syntax error"
_RE_FILE_LINE = re.compile(
    r"^(?P<file>.+?\.(?:v|sv|vh)):(?P<line>\d+):\s*(?P<msg>.*)$", re.I)
_RE_HAS_ERROR = re.compile(r"\b(error|syntax\s+error)\b", re.I)

# vvp / testbench 自检输出
_RE_SIM_FAIL = re.compile(r"^\s*(FAIL|ERROR|FATAL)\s*:\s*(?P<msg>.*)$", re.I)

# TD 日志错误/警告。注意 ARC-\d+ 不能单独当 warning 匹配：
# ARC-1001 是普通初始化信息，会把整段日志误报成 warning。
_RE_TD_ERROR = re.compile(r"\b(ERROR|FATAL)\b")
_RE_TD_WARNING = re.compile(r"\bWARNING\b", re.I)

# 约束相关
_RE_CONSTRAINT = re.compile(
    r"(duplicat\w*|invalid\s+pin|unknown\s+pin|not\s+found|illegal\s+\w+)", re.I)
_RE_CONSTRAINT_CTX = re.compile(
    r"(pin|adc|location|iostandard|constraint|port|arc-\d+)", re.I)


def _raw(lines, index, span=3):
    return "\n".join(line.rstrip() for line in lines[index:index + span])


def _dedup(errors):
    seen = set()
    out = []
    for err in errors:
        key = (err["type"], err["severity"], err["file"], err["line"],
               err["message"])
        if key in seen:
            continue
        seen.add(key)
        out.append(err)
    return out


def classify_log(raw_log, stage, exit_code=0, timing=None):
    """返回 errors[] 列表。

    raw_log: 原始日志文本（不是路径）。
    stage:   "sim" | "flow" | "program"
    timing:  parse_timing() 的结果；为 None 时不做 WNS 判定。
    exit_code: 子进程退出码，用于兜底 tool 错误。
    """
    text = raw_log or ""
    lines = text.splitlines()
    errors = []

    # 1) 编译错误
    compile_line_idx = set()
    for i, line in enumerate(lines):
        m = _RE_FILE_LINE.match(line.strip())
        if not m:
            continue
        msg = m.group("msg").strip()
        if not _RE_HAS_ERROR.search(msg):
            continue
        # 排除 Testbench 自检里的 "FATAL: file:line: TEST_FAILED"：
        # 那种行不是以 file:line 开头，不会走到这里。
        compile_line_idx.add(i)
        errors.append(make_error(
            "compile", msg or "compile error", file=m.group("file"),
            line=m.group("line"), raw=_raw(lines, i),
        ))

    # 2) 仿真失败
    sim_line_idx = set()
    for i, line in enumerate(lines):
        if i in compile_line_idx:
            continue
        m = _RE_SIM_FAIL.match(line)
        if not m:
            continue
        sim_line_idx.add(i)
        errors.append(make_error(
            "sim_fail", m.group("msg") or line.strip(), raw=_raw(lines, i),
        ))

    # 3) 时序违例
    if timing:
        wns = timing.get("wns_ns")
        setup_errors = timing.get("setup_errors")
        hold_errors = timing.get("hold_errors")
        bad_wns = wns is not None and wns < 0
        bad_count = bool(setup_errors) or bool(hold_errors)
        if bad_wns or bad_count:
            path = timing.get("critical_path") or {}
            sp = path.get("startpoint") or "?"
            ep = path.get("endpoint") or "?"
            slack = path.get("slack_ns")
            msg = f"时序不收敛: WNS={wns} ns"
            if setup_errors is not None or hold_errors is not None:
                msg += f", setup errors={setup_errors}, hold errors={hold_errors}"
            if slack is not None:
                msg += f"; 最差路径 slack={slack} ns"
            msg += f"; {sp} -> {ep}"
            errors.append(make_error("timing", msg, raw=""))

    # 4) 约束错误。TD 的 ARC-6008 是 WARNING（PKG 自带的 adc_vref），
    #    这类只记 warning；真正的 duplicated pin / invalid pin 才是 error。
    constraint_line_idx = set()
    for i, line in enumerate(lines):
        if i in compile_line_idx or i in sim_line_idx:
            continue
        if _RE_CONSTRAINT.search(line) and _RE_CONSTRAINT_CTX.search(line):
            constraint_line_idx.add(i)
            severity = "warning" if re.search(r"\bwarning\b", line, re.I) \
                else "error"
            errors.append(make_error(
                "constraint", line.strip(), severity=severity,
                raw=_raw(lines, i),
            ))

    # 5) TD 的其他 ERROR/FATAL -> tool
    already = compile_line_idx | sim_line_idx
    for i, line in enumerate(lines):
        if i in already:
            continue
        if _RE_TD_ERROR.search(line):
            # 避免把 "0 errors detected"、"0 setup errors" 当成错误（小写不匹配）
            if re.search(r"\b0\s+(?:setup\s+|hold\s+)?errors?\b", line, re.I):
                continue
            errors.append(make_error(
                "tool", line.strip(), raw=_raw(lines, i),
            ))

    # 6) TD WARNING（保留为 warning，最多 10 条，防止刷屏）
    warnings = 0
    for i, line in enumerate(lines):
        if i in compile_line_idx or i in sim_line_idx or \
                i in constraint_line_idx:
            continue
        if warnings >= 10:
            break
        if _RE_TD_WARNING.search(line) and not _RE_TD_ERROR.search(line):
            errors.append(make_error(
                "tool", line.strip(), severity="warning", raw=_raw(lines, i),
            ))
            warnings += 1

    # 7) 非零退出兜底
    if exit_code != 0 and not any(e["severity"] == "error" for e in errors):
        errors.append(make_error(
            "tool",
            f"命令退出码 {exit_code}，日志中未识别到更具体的错误",
            raw="\n".join(lines[-5:]),
        ))

    return _dedup(errors)


# ---------------------------------------------------------------------------
# 仿真输出计数（sim 子命令共用）
# ---------------------------------------------------------------------------

_RE_PASS_LINE = re.compile(r"^\s*PASS\s*:\s*(?P<msg>.*)$")
_RE_FAIL_LINE = re.compile(r"^\s*(?:FAIL|ERROR|FATAL)\s*:\s*(?P<msg>.*)$")


def parse_pass_fail(log_text):
    """统计 "PASS: xxx" / "FAIL:|ERROR:|FATAL: xxx"。

    返回 (pass_count, fail_count, failed_checks)。
    failed_checks 最多保留 50 条。
    """
    pass_count = 0
    fail_count = 0
    failed = []
    for line in (log_text or "").splitlines():
        if _RE_PASS_LINE.match(line):
            pass_count += 1
        else:
            m = _RE_FAIL_LINE.match(line)
            if m:
                fail_count += 1
                if len(failed) < 50:
                    failed.append(m.group("msg").strip() or line.strip())
    return pass_count, fail_count, failed


_RE_SECTION_SEP = re.compile(r"^=+$")


def parse_sim_sections(log_text):
    """解析 run_sim.sh 的 "=== / name / ===" 分段，统计每个 TB 的 PASS/FAIL。

    run_sim.sh 的格式：
        ============================================================
          top
        ============================================================
        PASS: ...
        PASSED: top
    """
    lines = (log_text or "").splitlines()
    sections = []
    current = None
    i = 0
    while i < len(lines):
        if (_RE_SECTION_SEP.match(lines[i].strip())
                and i + 2 < len(lines)
                and _RE_SECTION_SEP.match(lines[i + 2].strip())):
            name = lines[i + 1].strip()
            current = {"name": name, "pass": 0, "fail": 0}
            sections.append(current)
            i += 3
            continue
        if current is not None:
            if _RE_PASS_LINE.match(lines[i]):
                current["pass"] += 1
            elif _RE_FAIL_LINE.match(lines[i]):
                current["fail"] += 1
        i += 1
    return sections
