"""安路 TD 4.6 命令行封装。

TD 4.6 的分工和旧版不同（**没有 td_cmd.exe**）：
  - td.exe                 ：GUI
  - td_commands_prompt.exe ：命令行 / Tcl，综合、布局布线、bitgen、下载全走它

流程（HDL2Bit）：
    read_verilog -> optimize_rtl -> read_sdc -> optimize_gate
                 -> place -> route -> bitgen

⚠️ 布局布线命令是 place / route，不是 optimize_place / optimize_route：
    后者在 TD 4.6 里是空操作，不报错也不干活，会让 bitgen 拿到
    未布局布线的数据并在 "Init instances" 处段错误（退出码 139）。

⚠️ 两个常见坑：
  1. TD CLI 解析参数时把路径里的空格当断句符，工程路径/文件名尽量别带空格。
  2. place/route 需要 license：<TD 安装目录>/license/Anlogic.lic。
     缺失时 optimize_rtl / optimize_gate / optimize_place / optimize_route
     会报 RUN-8418 / RUN-8008。先运行 `python anlogic/td_flow.py check`
     检查工具链和 license。

命令行用法：
    python anlogic/td_flow.py check
    python anlogic/td_flow.py flow    board/bringup_led/flow.tcl
    python anlogic/td_flow.py download board/bringup_led/led_blink.bit
    python anlogic/td_flow.py program board/bringup_led/program.tcl

结构化（LLM Agent）用法：
    python anlogic/td_flow.py sim   board/exp4_reaction_timer/sim/run_sim.sh --json
    python anlogic/td_flow.py flow  board/exp4_reaction_timer/flow.tcl --json
    python anlogic/td_flow.py flow  board/exp4_reaction_timer/flow.tcl --json --reuse-report
    python anlogic/td_flow.py program board/exp4_reaction_timer/program.tcl --json
JSON 契约见 tools/result_schema.py。
"""
import os
import re
import sys
import tempfile

# 直接执行 `python anlogic/td_flow.py` 时，sys.path[0] 是 anlogic/，
# 需要把项目根目录加进来才能 import config。
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def _quote(p):
    """TD 内部解析参数时，含空格的路径要加双引号。"""
    return f'"{p}"' if " " in p else p


def _run(cmd, cwd=None, timeout=900):
    """用列表方式调用子进程（Windows 下 subprocess 会自动做 OS 层加引号）。"""
    print("[TD]", " ".join(cmd))
    import subprocess
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )


def _check_exe(exe, what):
    if not os.path.exists(exe):
        raise FileNotFoundError(f"找不到 {what}：{exe}，请修改 config.py 里的路径。")


def _error_lines(log):
    """挑出 TD 日志里真正的错误行（RUN-xxxx ERROR / FATAL）。"""
    out = []
    for line in log.splitlines():
        if re.search(r"\b(ERROR|FATAL)\b", line):
            out.append(line.strip())
    return out


def run_tcl(tcl_file, extra_args=None, timeout=1800):
    """用 td_commands_prompt.exe 执行一个 Tcl 脚本。

    cwd 设为脚本所在目录，脚本里的相对路径才有确定含义。
    返回 CompletedProcess。
    """
    exe = config.TD_PROMPT
    _check_exe(exe, "TD Tcl 命令行")
    tcl_file = os.path.abspath(tcl_file)
    if not os.path.exists(tcl_file):
        raise FileNotFoundError(f"找不到 Tcl 脚本：{tcl_file}")
    cmd = [exe, _quote(tcl_file)]
    if extra_args:
        cmd += [_quote(a) for a in extra_args]
    return _run(cmd, cwd=os.path.dirname(tcl_file), timeout=timeout)


def run_flow(tcl_file, timeout=1800):
    """执行综合+布局布线+比特流 Tcl，返回 (成功与否, 完整日志)。

    成功判定：退出码为 0 且日志里没有 <ERROR>/<FATAL>。
    注意 license 缺失也会在日志里体现为 ERROR，因此这里会直接判失败。
    """
    r = run_tcl(tcl_file, timeout=timeout)
    log = (r.stdout or "") + (r.stderr or "")
    errors = _error_lines(log)
    ok = (r.returncode == 0) and not errors
    return ok, log


def download_bit(bit_file, cable=None, timeout=300, keep_tcl=False):
    """通过 td_commands_prompt.exe 执行 `download -bit` 烧录到 FPGA（SRAM）。

    TD 4.6 的 download 命令没有 -verify 选项；确认成功看日志里的
    "Download success"。

    返回 (成功与否, 完整日志)。
    """
    bit_file = os.path.abspath(bit_file)
    if not os.path.exists(bit_file):
        raise FileNotFoundError(f"找不到 bit 文件：{bit_file}")

    cable = config.TD_CABLE if cable is None else cable
    # Tcl 双引号里反斜杠会被当转义符，统一换成正斜杠。
    tcl_bit = bit_file.replace("\\", "/")
    script = (
        f'set BIT_FILE "{tcl_bit}"\n'
        f'set CABLE {int(cable)}\n'
        'puts "DOWNLOAD: $BIT_FILE (cable $CABLE)"\n'
        'download -bit $BIT_FILE -cable $CABLE\n'
        'puts "program Finished"\n'
    )

    tcl_dir = os.path.dirname(bit_file)
    fd, tcl_path = tempfile.mkstemp(prefix="_download_", suffix=".tcl", dir=tcl_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(script)
        r = run_tcl(tcl_path, timeout=timeout)
        log = (r.stdout or "") + (r.stderr or "")
        errors = _error_lines(log)
        ok = (r.returncode == 0) and not errors and ("download success" in log.lower())
        return ok, log
    finally:
        if not keep_tcl:
            try:
                os.remove(tcl_path)
            except OSError:
                pass


def program_board(program_tcl, timeout=300):
    """执行烧录 Tcl（例如 board/bringup_led/program.tcl），返回 (成功, 日志)。"""
    r = run_tcl(program_tcl, timeout=timeout)
    log = (r.stdout or "") + (r.stderr or "")
    errors = _error_lines(log)
    ok = (r.returncode == 0) and not errors and ("download success" in log.lower())
    return ok, log


def check_environment():
    """检查 TD 可执行文件、器件库、license，返回 (ok, 报告文本)。"""
    lines = []
    ok = True

    lines.append(f"TD_PROMPT : {config.TD_PROMPT}")
    if os.path.exists(config.TD_PROMPT):
        lines.append("  [OK] 存在")
    else:
        lines.append("  [FAIL] 不存在")
        ok = False

    db = os.path.join(config.TD_ARCH_DIR, config.TD_DEVICE_DB)
    lines.append(f"器件库    : {db}")
    if os.path.exists(db):
        lines.append("  [OK] 存在")
    else:
        lines.append("  [FAIL] 不存在")
        ok = False

    lines.append(f"License   : {config.TD_LICENSE}")
    if os.path.exists(config.TD_LICENSE):
        lines.append("  [OK] 存在")
    else:
        lines.append("  [FAIL] 不存在 —— place/route 会被拦住")
        ok = False

    return ok, "\n".join(lines)


if __name__ == "__main__":
    # Windows 控制台默认 GBK，中文报告会乱码；统一切到 UTF-8。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(1)

    action = sys.argv[1]

    # ---- LLM Agent 结构化接口（新增）--------------------------------
    # sim / flow --json / program --json 交给 tools/agent_cli.py；
    # 未命中时返回 None，下面完全按原有逻辑执行，原有行为不变。
    try:
        from tools.agent_cli import maybe_handle_agent_argv
        _agent_rc = maybe_handle_agent_argv(sys.argv, {
            "flow": run_flow,
            "program": program_board,
        })
    except Exception as _exc:
        if action == "sim" or "--json" in sys.argv[1:]:
            import json as _json
            print(f"结构化接口加载失败: {_exc}", file=sys.stderr)
            print(_json.dumps({
                "schema_version": "1.0",
                "stage": action if action in ("sim", "flow", "program")
                         else "sim",
                "ok": False,
                "exit_code": 2,
                "duration_s": 0.0,
                "errors": [{
                    "type": "tool", "severity": "error", "file": None,
                    "line": None,
                    "message": f"结构化接口加载失败: {_exc}", "raw": "",
                }],
                "sim": None, "timing": None,
                "resources": {"lut": None, "ff": None, "bram": None},
                "artifacts": {"bit": None, "reports": []},
                "log_tail": "",
            }, ensure_ascii=False))
            sys.exit(2)
        _agent_rc = None
    if _agent_rc is not None:
        sys.exit(_agent_rc)
    # -----------------------------------------------------------------

    if action == "check":
        ok, report = check_environment()
        print(report)
        sys.exit(0 if ok else 1)

    if len(sys.argv) < 3:
        print(f"用法：python anlogic/td_flow.py {action} <文件>")
        sys.exit(1)

    target = sys.argv[2]
    if action == "flow":
        ok, log = run_flow(target)
    elif action == "download":
        ok, log = download_bit(target)
    elif action == "program":
        ok, log = program_board(target)
    else:
        print(f"未知动作：{action}")
        sys.exit(1)

    print("SUCCESS" if ok else "FAILED")
    print(log)
    sys.exit(0 if ok else 1)
