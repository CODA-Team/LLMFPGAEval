from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from .output_io import RESOURCE_DEFAULTS

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work"
CONFIG_FILE = ROOT / "workbench.local.json"


def local_path(value):
    """Local paths are relative to the source root, never a tool's working dir."""
    path = Path(os.path.expandvars(value)).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def bundled_tool_path(relative):
    preferred = ROOT / '.tools' / relative
    legacy = ROOT.parent / '.tools' / relative
    # Preserve an existing development installation; a clean release uses .tools
    # inside its own root. Users can always override these paths in the web UI.
    return str(legacy if not preferred.exists() and legacy.exists() else preferred)


DEFAULTS = {
    **RESOURCE_DEFAULTS,
    "simulation_backend": "modelsim",
    "modelsim_root": bundled_tool_path('modelsim2020.4'),
    "modelsim_license": "",
    "td_root": bundled_tool_path('td467'),
    "iverilog": bundled_tool_path('iverilog/bin/iverilog.exe'),
    "vvp": bundled_tool_path('iverilog/bin/vvp.exe'),
    "ssh_host": "",
    "ssh_port": 22,
    "ssh_identity": "",
    "orfs_root": "",
    "remote_work_root": "",
    "remote_library_path": "",
    "platform": "nangate45",
    "threads": 4,
    "timeout_s": None,
}
PROJECTS = {
    "reaction_timer": {
        "id": "reaction_timer", "name": "反应计时器", "path": str(ROOT / "exp4_reaction_timer"),
        "top": "reaction_timer_board", "rtl": ["rtl/reaction_timer_board.v"],
        "clock_port": "clk", "clock_period_ns": 20.0, "flow": "flow.tcl",
        "bit": "reaction_timer_board.bit", "device": "EG4S20BG256", "sdc": "reaction_timer_board.sdc",
        "testbenches": [
            "sim/tb_reaction_timer_top.v", "sim/tb_reaction_timer_board.v",
            "sim/tb_delay_width.v", "sim/tb_bcd.v", "sim/tb_max_ms.v",
            "sim/tb_key_scan_div.v", "sim/tb_debounce_real.v",
        ],
    },
    "calculator": {
        "id": "calculator", "name": "四位计算器", "path": str(ROOT / "exp_calculator"),
        "top": "calculator_top", "rtl": ["rtl/*.v"],
        "clock_port": "clk", "clock_period_ns": 20.0, "flow": "flow.tcl",
        "bit": "calculator_top.bit", "device": "EG4S20BG256", "sdc": "constraints/calculator.sdc",
        "testbenches": ["sim/tb_keypad.v", "sim/tb_debounce.v", "sim/tb_bcd.v", "sim/tb_alu.v", "sim/tb_calculator_top.v"],
    },
}


def validate_config(config):
    if config["simulation_backend"] not in ("modelsim", "icarus"):
        raise ValueError("仿真器只支持 modelsim 或 icarus")
    if not isinstance(config['ssh_host'], str) or (config['ssh_host'] and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@:-]*", config["ssh_host"])):
        raise ValueError("SSH 主机应为 SSH 配置别名或 user@host")
    for key in ("orfs_root", "remote_work_root"):
        value = config[key]
        if value == '':
            continue  # Remote ASIC execution is optional until configured.
        if not isinstance(value, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or ".." in value.split("/"):
            raise ValueError(key + " 必须为不含空格的 Linux 绝对路径")
        if len(Path(value).parts) < 4:
            raise ValueError(key + " 需要使用专用子目录")
    if config["platform"] != "nangate45":
        raise ValueError("当前版本已校验的 ASIC 平台为 nangate45；其他平台需先核对时序/功耗单位")
    for path in config["remote_library_path"].split(":"):
        if path and (not re.fullmatch(r"/[A-Za-z0-9_./-]+", path) or ".." in path.split("/")):
            raise ValueError("远程动态库路径无效")
    for key, low, high in (("ssh_port", 1, 65535), ("threads", 1, 2147483647)):
        value = config[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(key + " 超出范围")
    for key in (*RESOURCE_DEFAULTS, 'timeout_s'):
        value = config.get(key)
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(key + ' 应留空或填写正整数')
    for key in ("td_root", "modelsim_root", "modelsim_license", "iverilog", "vvp", "ssh_identity"):
        if not isinstance(config[key], str) or any(c in config[key] for c in "\x00\r\n"):
            raise ValueError(key + " 路径无效")
    return config


def require_remote_config(config):
    validate_config(config)
    missing = [key for key in ('ssh_host', 'orfs_root', 'remote_work_root') if not config[key]]
    if missing:
        raise ValueError('ORFS 尚未配置：请在“实现与对比”的环境配置中填写 ' + '、'.join(missing))
    return config


def load_config():
    config = copy.deepcopy(DEFAULTS)
    if CONFIG_FILE.exists():
        config.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    validate_config(config)
    for key in ('td_root', 'modelsim_root', 'iverilog', 'vvp', 'ssh_identity', 'modelsim_license'):
        if config[key] and not (key == 'modelsim_license' and '@' in config[key]):
            value = os.path.expandvars(config[key])
            if key in ('iverilog', 'vvp') and Path(value).name == value:
                config[key] = shutil.which(value) or value
            else:
                config[key] = str(local_path(value))
    return config


def save_config(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError("配置字段无效")
    config = load_config()
    config.update(values)
    validate_config(config)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    return config


def snapshot(project, destination):
    src = Path(project["path"]).resolve()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    paths = set()
    rtl = set()
    for pattern in project["rtl"]:
        for p in src.glob(pattern):
            if not p.is_file() or not p.resolve().is_relative_to(src):
                raise ValueError("RTL 文件超出工程目录")
            rtl.add(p.relative_to(src).as_posix())
    if not rtl:
        raise ValueError("未找到 RTL")
    for p in src.rglob("*"):
        relative = p.relative_to(src)
        if any(part in ("build", "work", ".git", "__pycache__") for part in relative.parts):
            continue
        if p.is_file() and p.suffix.lower() in (".v", ".sv", ".vh", ".svh", ".tcl", ".sdc", ".adc"):
            if not p.resolve().is_relative_to(src) or p.is_symlink():
                raise ValueError("源码快照不接受外部链接")
            paths.add(relative.as_posix())
    files = {}
    digest = hashlib.sha256()
    for rel in sorted(paths):
        content = (src / rel).read_bytes()
        out = destination / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)
        files[rel] = hashlib.sha256(content).hexdigest()
        if rel in rtl:
            digest.update(rel.encode() + b"\0" + content + b"\0")
    result = {"rtl_sha256": digest.hexdigest(), "files": files, "rtl_files": sorted(rtl),
              "top": project["top"], "clock_period_ns": project["clock_period_ns"]}
    (destination / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
