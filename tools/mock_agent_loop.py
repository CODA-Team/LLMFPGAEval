"""模拟上层 LLM Agent 的结构化闭环。

流程：
  1. 把 board/exp4_reaction_timer 的 rtl/ 和 sim/ 复制到 work/mock_agent/
     （原文件不动）。
  2. 在副本 RTL 末尾注入 1 个语法错误：`wire mock_agent_inject_broken`
     （缺少分号）。
  3. 调用 `python anlogic/td_flow.py sim <副本>/sim/run_sim.sh --json`。
  4. 从 JSON 读取 errors[0].type / file / line。
  5. 按 file + line 定位并删除注入行（模拟 Agent 的自动修复）。
  6. 再跑一次 sim --json，要求 ok=true、fail=0、pass>=37。
  7. 全过程写到 work/logs/mock_agent_loop.log；中间 JSON 存
     work/logs/mock_agent_before.json / mock_agent_after.json。

成功时删除 work/mock_agent/；失败时保留现场。
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "board", "exp4_reaction_timer")
WORK = os.path.join(ROOT, "work", "mock_agent")
LOG_DIR = os.path.join(ROOT, "work", "logs")
LOG_PATH = os.path.join(LOG_DIR, "mock_agent_loop.log")
TD_FLOW = os.path.join(ROOT, "anlogic", "td_flow.py")

MARKER = "mock_agent_inject_broken"
INJECT_TEXT = (
    "\n// mock agent: injected syntax error below\n"
    f"wire {MARKER}\n"
)

TIMEOUT_FIRST = 600
TIMEOUT_SECOND = 1800
MIN_PASS = 37


def log(message):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _safe_rmtree(path):
    """只删除 work/ 下的临时目录，防止手滑。"""
    target = os.path.abspath(path)
    work_root = os.path.abspath(os.path.join(ROOT, "work"))
    if os.path.commonpath([target, work_root]) != work_root:
        raise RuntimeError(f"拒绝删除 work/ 之外的目录: {target}")
    if os.path.exists(target):
        shutil.rmtree(target)


def prepare_workspace():
    _safe_rmtree(WORK)
    os.makedirs(os.path.join(WORK, "rtl"), exist_ok=True)
    os.makedirs(os.path.join(WORK, "sim"), exist_ok=True)

    shutil.copy2(
        os.path.join(SOURCE, "rtl", "reaction_timer_board.v"),
        os.path.join(WORK, "rtl", "reaction_timer_board.v"),
    )
    shutil.copy2(
        os.path.join(SOURCE, "sim", "run_sim.sh"),
        os.path.join(WORK, "sim", "run_sim.sh"),
    )
    shutil.copy2(
        os.path.join(SOURCE, "sim", "tb_common.vh"),
        os.path.join(WORK, "sim", "tb_common.vh"),
    )
    for tb in glob.glob(os.path.join(SOURCE, "sim", "tb_*.v")):
        shutil.copy2(tb, os.path.join(WORK, "sim", os.path.basename(tb)))

    script = os.path.join(WORK, "sim", "run_sim.sh")
    log(f"准备副本完成: {os.path.relpath(WORK, ROOT)}")
    return script


def inject_error(rtl_path):
    with open(rtl_path, "a", encoding="utf-8") as f:
        f.write(INJECT_TEXT)
    with open(rtl_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    injected_line = None
    for i, line in enumerate(lines, 1):
        if MARKER in line:
            injected_line = i
    log(f"注入语法错误: {os.path.relpath(rtl_path, ROOT)}:{injected_line}  "
        f"内容={lines[injected_line - 1]!r}")
    return injected_line


def run_sim(script, tag, timeout):
    cmd = [sys.executable, TD_FLOW, "sim", script, "--json"]
    log(f"[{tag}] 执行: {' '.join(cmd)}")
    started = time.time()
    cp = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    duration = time.time() - started

    # 原始日志（stderr）和 JSON 各存一份，便于复盘
    stderr_path = os.path.join(LOG_DIR, f"mock_agent_{tag}.stderr.log")
    with open(stderr_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(cp.stderr or "")
    json_path = os.path.join(LOG_DIR, f"mock_agent_{tag}.json")
    with open(json_path, "w", encoding="utf-8", errors="replace") as f:
        f.write(cp.stdout or "")

    if not cp.stdout.strip():
        raise RuntimeError(f"[{tag}] stdout 为空，退出码 {cp.returncode}；"
                           f"见 {os.path.relpath(stderr_path, ROOT)}")
    try:
        data = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"[{tag}] stdout 不是合法 JSON: {exc}")

    log(f"[{tag}] 完成: exit={cp.returncode} ok={data.get('ok')} "
        f"duration={duration:.1f}s "
        f"sim.pass={data.get('sim', {}).get('pass') if data.get('sim') else None} "
        f"sim.fail={data.get('sim', {}).get('fail') if data.get('sim') else None} "
        f"errors={[e.get('type') for e in data.get('errors', [])]}")
    return data, duration


def extract_compile_error(data, project_dir):
    errors = data.get("errors") or []
    compile_errors = [e for e in errors if e.get("type") == "compile"]
    if not compile_errors:
        raise RuntimeError(
            f"JSON 中没有 compile 错误，实际: {[e.get('type') for e in errors]}")

    err = compile_errors[0]
    rel_file = err.get("file")
    line = err.get("line")
    if not rel_file:
        raise RuntimeError(f"compile 错误缺少 file 字段: {err}")

    file_path = rel_file if os.path.isabs(rel_file) \
        else os.path.join(project_dir, rel_file)
    file_path = os.path.abspath(file_path)
    log(f"从 JSON 提取: type={err.get('type')} file={rel_file} line={line} "
        f"message={err.get('message')!r}")
    return file_path, line, err


def repair_error(file_path, reported_line):
    """按 JSON 给出的行号删除注入行；行号对不上时退回按标记查找。"""
    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)

    removed = None
    if isinstance(reported_line, int) and 1 <= reported_line <= len(lines):
        candidate = lines[reported_line - 1]
        if MARKER in candidate:
            removed = lines.pop(reported_line - 1)
            log(f"按 JSON 行号 {reported_line} 删除注入行")

    if removed is None:
        for i, line in enumerate(lines):
            if MARKER in line:
                removed = lines.pop(i)
                log(f"JSON 行号未命中，按标记找到并删除第 {i + 1} 行")
                break

    if removed is None:
        raise RuntimeError(f"在 {file_path} 中找不到注入标记 {MARKER}")

    # 顺带删掉我们注入的说明注释
    lines = [ln for ln in lines if "mock agent: injected syntax error" not in ln]

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    log(f"修复完成: 删除内容={removed.strip()!r}")


def main():
    # Windows 控制台默认 GBK，中文会乱码；日志文件本身始终是 UTF-8。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    os.makedirs(LOG_DIR, exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        f.write("")

    log("=== mock agent loop 开始 ===")
    log(f"仓库根目录: {ROOT}")
    log(f"源设计: {os.path.relpath(SOURCE, ROOT)}")

    script = prepare_workspace()
    project_dir = os.path.dirname(os.path.dirname(script))
    rtl_path = os.path.join(project_dir, "rtl", "reaction_timer_board.v")
    inject_error(rtl_path)

    before, _ = run_sim(script, "before", TIMEOUT_FIRST)
    if before.get("ok") is not False:
        raise RuntimeError("注入错误后 ok 仍为 true，闭环测试无效")

    file_path, line, err = extract_compile_error(before, project_dir)
    repair_error(file_path, line)

    after, _ = run_sim(script, "after", TIMEOUT_SECOND)
    sim = after.get("sim") or {}
    pass_count = sim.get("pass") or 0
    fail_count = sim.get("fail") or 0

    if after.get("ok") is not True:
        raise RuntimeError(f"修复后 ok != true: {after.get('errors')}")
    if fail_count != 0:
        raise RuntimeError(f"修复后仍有失败断言: {sim.get('failed_checks')}")
    if pass_count < MIN_PASS:
        raise RuntimeError(f"修复后 PASS 数 {pass_count} < {MIN_PASS}")

    log(f"闭环成功: 修复后 ok=true, pass={pass_count}, fail=0")
    log(f"证据文件: work/logs/mock_agent_before.json, "
        f"work/logs/mock_agent_after.json, "
        f"work/logs/mock_agent_loop.log")

    _safe_rmtree(WORK)
    log("已清理临时目录 work/mock_agent/")
    log("=== mock agent loop 结束: SUCCESS ===")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        log(f"FAILED: {exc}")
        log(f"临时目录保留在 {os.path.relpath(WORK, ROOT)} 以便排查")
        log("=== mock agent loop 结束: FAILED ===")
        sys.exit(1)
