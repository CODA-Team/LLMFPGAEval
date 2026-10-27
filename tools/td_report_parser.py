"""安路 TD 4.6 报告解析器。

只用阶段 0 从真实报告 dump 出来的格式写正则，不凭想象。
所有函数在字段解析不到时返回 None（资源类）或 None 字段（时序类），
不猜、不填默认值。

真实格式样本（board/exp4_reaction_timer/）：

  Timing summary:
  Constraint path number: 26662 (STA coverage = 99.55%)
  Timing violations: 0 setup errors, and 0 hold errors.
  Minimal setup slack: 14.652, minimal hold slack: 0.278
    clk (50.000MHz)    5.348ns    186.986MHz    0.399ns    256    0.000ns

  Slack (setup check)      14.652 ns
   StartPoint:              u_dut/...clk (rising edge triggered by clock clk)
   EndPoint:                u_dut/...c[1] (rising edge triggered by clock clk)

  clock summary 是 XML：
  <clock name="clk" freq="50.000MHz" type="master">
      ...
      <loads num="256">
      <max_skew><max_rise_skew value="397ps"> ...
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# 通用数值/单位
# ---------------------------------------------------------------------------

_UNIT_TO_NS = {
    "ps": 1e-3,
    "ns": 1.0,
    "us": 1e3,
    "ms": 1e6,
}


def _to_ns(value, unit):
    """把带单位的数字统一成 ns。unit 为空按 ns。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    factor = _UNIT_TO_NS.get((unit or "ns").lower())
    if factor is None:
        return None
    return round(number * factor, 6)


def _to_ps(value, unit):
    """把带单位的数字统一成 ps。"""
    ns = _to_ns(value, unit)
    return None if ns is None else round(ns * 1000.0, 3)


# 允许 "14.652" / "14.652 ns" / "14652 ps" / "14.652ns"
_NUM_UNIT = r"(-?\d+(?:\.\d+)?)\s*(ps|ns|us|ms)?"

_RE_SETUP_SLACK = re.compile(
    r"Slack\s*\(\s*setup\s+check\s*\)\s*[:=]?\s*" + _NUM_UNIT, re.I)
_RE_HOLD_SLACK = re.compile(
    r"Slack\s*\(\s*hold\s+check\s*\)\s*[:=]?\s*" + _NUM_UNIT, re.I)
_RE_STARTPOINT = re.compile(r"Start\s*Point\s*:\s*(.+)", re.I)
_RE_ENDPOINT = re.compile(r"End\s*Point\s*:\s*(.+)", re.I)

_RE_MIN_SETUP_SLACK = re.compile(
    r"Minimal\s+setup\s+slack\s*:\s*" + _NUM_UNIT, re.I)
_RE_MIN_HOLD_SLACK = re.compile(
    r"minimal\s+hold\s+slack\s*:\s*" + _NUM_UNIT, re.I)
_RE_TNS = re.compile(r"\(\s*TNS\s*=\s*" + _NUM_UNIT + r"\s*\)", re.I)
_RE_MIN_PERIOD = re.compile(
    r"Minimum\s+period\s+is\s*" + _NUM_UNIT, re.I)
_RE_SETUP_ERRCOUNT = re.compile(r"(\d+)\s+setup\s+errors?", re.I)
_RE_HOLD_ERRCOUNT = re.compile(r"(\d+)\s+hold\s+errors?", re.I)

# 去掉 StartPoint/EndPoint 尾部的触发沿说明，保留纯层级路径
_RE_TRIGGER_SUFFIX = re.compile(
    r"\s*\(\s*(?:rising|falling)\s+edge[^)]*\)\s*$", re.I)


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _clean_point(text):
    text = (text or "").strip()
    text = _RE_TRIGGER_SUFFIX.sub("", text)
    return text or None


def _first_slack(regex, text, unit="ns"):
    m = regex.search(text)
    return None if not m else _to_ns(m.group(1), m.group(2))


def _collect_slack_blocks(text, setup=True):
    """收集所有 Slack (setup|hold check) 块，返回 [(slack_ns, start, end)]。"""
    regex = _RE_SETUP_SLACK if setup else _RE_HOLD_SLACK
    matches = list(regex.finditer(text))
    blocks = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[m.end():end]
        sp = _RE_STARTPOINT.search(chunk)
        ep = _RE_ENDPOINT.search(chunk)
        blocks.append((
            _to_ns(m.group(1), m.group(2)),
            _clean_point(sp.group(1) if sp else None),
            _clean_point(ep.group(1) if ep else None),
        ))
    return blocks


def parse_timing(rpt_path):
    """解析 TD *_timing.rpt，返回 timing 字段 dict。

    必需键：wns_ns / tns_ns / whs_ns / min_period_ns / critical_path
    解析不到的字段为 None；文件不可读时额外给 parse_error。
    """
    result = {
        "wns_ns": None,
        "tns_ns": None,
        "whs_ns": None,
        "min_period_ns": None,
        "critical_path": None,
        "setup_errors": None,
        "hold_errors": None,
        "parse_error": None,
    }
    try:
        text = _read_text(rpt_path)
    except OSError as exc:
        result["parse_error"] = f"无法读取时序报告: {exc}"
        return result

    setup_blocks = _collect_slack_blocks(text, setup=True)
    hold_blocks = _collect_slack_blocks(text, setup=False)
    setup_slacks = [b[0] for b in setup_blocks if b[0] is not None]
    hold_slacks = [b[0] for b in hold_blocks if b[0] is not None]

    # WNS：优先用结尾 summary 的 Minimal setup slack，其次取所有 setup 块最小值
    result["wns_ns"] = _first_slack(_RE_MIN_SETUP_SLACK, text)
    if result["wns_ns"] is None and setup_slacks:
        result["wns_ns"] = min(setup_slacks)

    # WHS
    result["whs_ns"] = _first_slack(_RE_MIN_HOLD_SLACK, text)
    if result["whs_ns"] is None and hold_slacks:
        result["whs_ns"] = min(hold_slacks)

    # TNS（report 在 setup errors 后面）
    result["tns_ns"] = _first_slack(_RE_TNS, text)

    # 最小周期
    m = _RE_MIN_PERIOD.search(text)
    if m:
        result["min_period_ns"] = _to_ns(m.group(1), m.group(2))

    # 错误计数
    m = _RE_SETUP_ERRCOUNT.search(text)
    if m:
        result["setup_errors"] = int(m.group(1))
    m = _RE_HOLD_ERRCOUNT.search(text)
    if m:
        result["hold_errors"] = int(m.group(1))

    # 关键路径：取 setup slack 最小的块
    if setup_blocks:
        worst = min(
            (b for b in setup_blocks if b[0] is not None),
            key=lambda b: b[0],
            default=None,
        )
        if worst is not None:
            result["critical_path"] = {
                "startpoint": worst[1],
                "endpoint": worst[2],
                "slack_ns": worst[0],
            }

    if result["wns_ns"] is None and result["parse_error"] is None:
        result["parse_error"] = "未找到 Minimal setup slack / Slack (setup check)"
    return result


def parse_clock_summary(path):
    """解析 *_clock_summary.rpt（XML）。

    返回 {"clocks": [...], "groups": [...], "parse_error": ...}
    clock 项字段：name / freq_mhz / type / loads / max_rise_skew_ps /
                  max_fall_skew_ps / source_latency
    """
    out = {"clocks": [], "groups": [], "parse_error": None}
    try:
        tree = ET.parse(path)
    except (OSError, ET.ParseError) as exc:
        out["parse_error"] = f"无法解析时钟摘要: {exc}"
        return out

    root = tree.getroot()

    for group in root.iter("clock_group"):
        name = group.get("name")
        # XML 里 groups 索引和真正的 clock_group 会各出现一次，去重保序
        if name and name not in out["groups"]:
            out["groups"].append(name)

    for clk in root.iter("clock"):
        name = clk.get("name")
        freq_raw = clk.get("freq") or ""
        freq_mhz = None
        fm = re.search(r"(\d+(?:\.\d+)?)\s*MHz", freq_raw, re.I)
        if fm:
            freq_mhz = float(fm.group(1))
        else:
            fg = re.search(r"(\d+(?:\.\d+)?)\s*GHz", freq_raw, re.I)
            if fg:
                freq_mhz = float(fg.group(1)) * 1000.0

        loads = None
        loads_el = clk.find(".//loads")
        if loads_el is not None and (loads_el.get("num") or "").isdigit():
            loads = int(loads_el.get("num"))

        rise_el = clk.find(".//max_skew/max_rise_skew")
        fall_el = clk.find(".//max_skew/max_fall_skew")
        rise_ps = fall_ps = None
        if rise_el is not None:
            vm = re.match(r"(-?\d+(?:\.\d+)?)\s*(ps|ns)?", rise_el.get("value", ""), re.I)
            if vm:
                rise_ps = _to_ps(vm.group(1), vm.group(2))
        if fall_el is not None:
            vm = re.match(r"(-?\d+(?:\.\d+)?)\s*(ps|ns)?", fall_el.get("value", ""), re.I)
            if vm:
                fall_ps = _to_ps(vm.group(1), vm.group(2))

        source_latency = None
        for prop in clk.iter("property"):
            if prop.get("source_latency"):
                source_latency = prop.get("source_latency")
                break

        out["clocks"].append({
            "name": name,
            "freq_mhz": freq_mhz,
            "type": clk.get("type"),
            "loads": loads,
            "max_rise_skew_ps": rise_ps,
            "max_fall_skew_ps": fall_ps,
            "source_latency": source_latency,
        })

    if not out["clocks"] and out["parse_error"] is None:
        out["parse_error"] = "XML 中没有找到 <clock> 元素"
    return out


_RE_LUT = re.compile(r"Mapping\s+with\s+K=\d+\s*,\s*#lut\s*=\s*(\d+)", re.I)
_RE_FF = re.compile(r"Packing\s+(\d+)\s+DFF/LATCH\s+to\s+SEQ", re.I)
_RE_BRAM = re.compile(r"(\d+)\s+brams?\b", re.I)


def parse_resource_text(text):
    """从 TD 流程日志文本抓 LUT / FF / BRAM（抓不到 = None）。"""
    out = {"lut": None, "ff": None, "bram": None}
    text = text or ""

    m = _RE_LUT.search(text)
    if m:
        out["lut"] = int(m.group(1))
    # SEQ packed with LUTs is only one subset; others share adders or stay
    # standalone. The packing input count includes all sequential cells.
    seq_counts = _RE_FF.findall(text)
    if len(seq_counts) == 1:
        out["ff"] = int(seq_counts[0])
    m = _RE_BRAM.search(text)
    if m:
        out["bram"] = int(m.group(1))
    return out


def parse_resource(flow_log_path):
    """从 TD 流程日志文件抓 LUT / FF / BRAM。

    真实格式（阶段 0 看到的）：
      SYN-2581 : Mapping with K=5, #lut = 455 (3.99), #lev = 5 (2.34)
      SYN-4002 : Packing 399 DFF/LATCH to SEQ ...
      RUN-1001 : 234 mslices, 234 lslices, 21 pads, 0 brams, 2 dsps

    FF 取进入 SEQ 打包的 DFF/LATCH 总数（包含 LUT、adder 和独立 SEQ）；
    多个打包阶段不猜测总量。BRAM 取 RUN-1001 的 brams。
    抓不到就 None，不做估算。
    """
    try:
        # Retain only resource records; TD logs can be much larger than RAM.
        with open(flow_log_path, encoding='utf-8', errors='replace') as source:
            text = ''.join(line for line in source if any(pattern.search(line) for pattern in (_RE_LUT, _RE_FF, _RE_BRAM)))
    except OSError:
        return {"lut": None, "ff": None, "bram": None}
    return parse_resource_text(text)


def parse_report_paths(tcl_path):
    """从 flow.tcl 的 report_* -file 参数解析报告路径（相对 tcl 所在目录）。

    返回 {"timing": abs_path|None, "clock_summary": abs_path|None,
          "bit": abs_path|None}
    """
    out = {"timing": None, "clock_summary": None, "bit": None}
    try:
        text = _read_text(tcl_path)
    except OSError:
        return out

    base = os.path.dirname(os.path.abspath(tcl_path))

    def _resolve(p):
        p = p.strip().strip('"').strip("'")
        return p if os.path.isabs(p) else os.path.abspath(os.path.join(base, p))

    m = re.search(r"report_timing\s+-file\s+(\S+)", text, re.I)
    if m:
        out["timing"] = _resolve(m.group(1))
    m = re.search(r"report_clock_summary\s+-file\s+(\S+)", text, re.I)
    if m:
        out["clock_summary"] = _resolve(m.group(1))
    m = re.search(r"bitgen\s+-bit\s+(\S+)", text, re.I)
    if m:
        out["bit"] = _resolve(m.group(1))
    return out
