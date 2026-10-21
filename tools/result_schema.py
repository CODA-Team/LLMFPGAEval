"""统一 JSON 返回契约。

所有结构化子命令（sim / flow / program --json）都输出这里的结构。
本模块只定义结构、构造函数和校验，不做任何流程逻辑。

契约（必需字段）：

{
  "stage": "sim" | "flow" | "program",
  "ok": true | false,
  "exit_code": 0,
  "duration_s": 12.3,
  "errors": [ {type, severity, file, line, message, raw} ],
  "sim": {tb, pass, fail, failed_checks} | null,
  "timing": {wns_ns, tns_ns, whs_ns, min_period_ns,
             critical_path: {startpoint, endpoint, slack_ns}} | null,
  "resources": {"lut": int|null, "ff": int|null, "bram": int|null},
  "artifacts": {"bit": str|null, "reports": [str]},
  "log_tail": "最后 20 行"
}

允许的扩展字段（上层 Agent 可忽略）：
  - schema_version：契约版本
  - sim.testbenches / sim.script：多 TB 脚本的逐项结果
  - timing.clocks / timing.setup_errors / timing.hold_errors：时钟摘要与错误计数
"""
from __future__ import annotations

import json

SCHEMA_VERSION = "1.0"

STAGES = ("sim", "flow", "program")
ERROR_TYPES = ("compile", "sim_fail", "timing", "constraint", "tool")
SEVERITIES = ("error", "warning")


def make_error(type_, message, severity="error", file=None, line=None, raw=""):
    """构造一条 errors[] 记录。raw 最多保留 5 行。"""
    if type_ not in ERROR_TYPES:
        type_ = "tool"
    if severity not in SEVERITIES:
        severity = "error"

    if isinstance(line, str) and line.isdigit():
        line = int(line)
    if not isinstance(line, int):
        line = None

    msg = " ".join(str(message or "").split())
    if not msg:
        msg = "unknown error"
    raw_lines = str(raw or "").strip().splitlines()[:5]

    return {
        "type": type_,
        "severity": severity,
        "file": file or None,
        "line": line,
        "message": msg[:500],
        "raw": "\n".join(raw_lines),
    }


def tail(text, n=20):
    """日志最后 n 行。"""
    return "\n".join(str(text or "").splitlines()[-n:])


def make_result(stage, ok, exit_code, duration_s, errors=None, sim=None,
                timing=None, resources=None, artifacts=None, log_tail=""):
    """按契约构造结果。缺失的资源字段显式置 null，不猜。"""
    if stage not in STAGES:
        stage = "tool"
    resources = resources or {}
    artifacts = artifacts or {}
    return {
        "schema_version": SCHEMA_VERSION,
        "stage": stage,
        "ok": bool(ok),
        "exit_code": int(exit_code),
        "duration_s": round(float(duration_s), 3),
        "errors": list(errors or []),
        "sim": sim,
        "timing": timing,
        "resources": {
            "lut": resources.get("lut"),
            "ff": resources.get("ff"),
            "bram": resources.get("bram"),
        },
        "artifacts": {
            "bit": artifacts.get("bit"),
            "reports": list(artifacts.get("reports") or []),
        },
        "log_tail": tail(log_tail, 20),
    }


def validate_result(result):
    """校验结果是否满足契约。返回违规描述列表（空列表 = 合法）。"""
    problems = []
    if not isinstance(result, dict):
        return ["result 不是 dict"]

    required = ("stage", "ok", "exit_code", "duration_s", "errors",
                "sim", "timing", "resources", "artifacts", "log_tail")
    for key in required:
        if key not in result:
            problems.append(f"缺少字段: {key}")
    if problems:
        return problems

    if result["stage"] not in STAGES:
        problems.append(f"stage 非法: {result['stage']!r}")
    if not isinstance(result["ok"], bool):
        problems.append("ok 不是 bool")
    if not isinstance(result["exit_code"], int):
        problems.append("exit_code 不是 int")
    if not isinstance(result["errors"], list):
        problems.append("errors 不是 list")
    else:
        for i, err in enumerate(result["errors"]):
            if not isinstance(err, dict):
                problems.append(f"errors[{i}] 不是 dict")
                continue
            for key in ("type", "severity", "file", "line", "message", "raw"):
                if key not in err:
                    problems.append(f"errors[{i}] 缺少 {key}")
            if err.get("type") not in ERROR_TYPES:
                problems.append(f"errors[{i}].type 非法: {err.get('type')!r}")
            if err.get("severity") not in SEVERITIES:
                problems.append(f"errors[{i}].severity 非法: {err.get('severity')!r}")

    resources = result["resources"]
    if not isinstance(resources, dict):
        problems.append("resources 不是 dict")
    else:
        for key in ("lut", "ff", "bram"):
            if key not in resources:
                problems.append(f"resources 缺少 {key}")
            elif resources[key] is not None and not isinstance(resources[key], int):
                problems.append(f"resources.{key} 不是 int/null")

    artifacts = result["artifacts"]
    if not isinstance(artifacts, dict):
        problems.append("artifacts 不是 dict")
    elif "bit" not in artifacts or "reports" not in artifacts:
        problems.append("artifacts 缺少 bit/reports")

    if result["sim"] is not None:
        sim = result["sim"]
        if not isinstance(sim, dict):
            problems.append("sim 不是 dict/null")
        else:
            for key in ("tb", "pass", "fail", "failed_checks"):
                if key not in sim:
                    problems.append(f"sim 缺少 {key}")

    if result["timing"] is not None:
        timing = result["timing"]
        if not isinstance(timing, dict):
            problems.append("timing 不是 dict/null")
        else:
            for key in ("wns_ns", "tns_ns", "whs_ns", "min_period_ns",
                        "critical_path"):
                if key not in timing:
                    problems.append(f"timing 缺少 {key}")

    return problems


def dumps(result, indent=2):
    """稳定的 UTF-8 JSON 文本。"""
    return json.dumps(result, ensure_ascii=False, indent=indent, sort_keys=False)
