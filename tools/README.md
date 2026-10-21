# tools/ —— LLM Agent 结构化工具

把安路 TD 4.6 的 flow/program 和 Icarus 仿真包装成**一份 JSON**，
Agent 不需要读几百行原始日志。

## 调用方式

从仓库根目录：

```bash
# 仿真（整个 run_sim.sh，7 个 TB）
python anlogic/td_flow.py sim board/exp4_reaction_timer/sim/run_sim.sh --json

# 重新综合 + 布局布线 + bitgen，然后解析报告
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl --json

# 不重跑，直接解析上一轮留下的 rpt（快速回归）
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl --json --reuse-report

# 烧录
python anlogic/td_flow.py program board/exp4_reaction_timer/program.tcl --json

# 解析器单元测试
python tools/test_parser.py
```

- **stdout 只有 JSON**；原始日志写 stderr，并同时落盘到 `work/logs/`。
- 每次运行生成一对文件：
  - `work/logs/<stage>_<timestamp>.log`：完整原始日志
  - `work/logs/<stage>_<timestamp>.json`：本次 JSON 结果（含 `log_file` 字段）
- 退出码：`ok=true` → `0`，否则非 `0`。
- `--timeout N`：子进程超时秒数，默认 1800。
- 不带 `--json` 时 `flow` / `program` **完全保持原行为**（人类可读输出），
  `sim` 输出一行人类可读摘要。
- `--reuse-report` 只对 `flow` 生效：不执行 TD，直接解析 `flow.tcl` 里
  `report_* -file` 指定的报告；资源占用取实验目录下最新一个含综合信息的
  `td_*.log`。

## JSON 契约

定义在 `tools/result_schema.py`，所有 stage 共用：

| 字段 | 类型 | 说明 |
|---|---|---|
| `stage` | `"sim" │ "flow" │ "program"` | 子命令 |
| `ok` | bool | 整体是否通过 |
| `exit_code` | int | 子进程/脚本退出码（reuse 模式为 0） |
| `duration_s` | float | 墙钟耗时 |
| `errors` | array | 结构化错误/警告，见下 |
| `sim` | object │ null | `{tb, pass, fail, failed_checks, script, testbenches}` |
| `timing` | object │ null | `{wns_ns, tns_ns, whs_ns, min_period_ns, critical_path, clocks, ...}` |
| `resources` | object | `{lut, ff, bram}`，解析不到为 `null` |
| `artifacts` | object | `{bit, reports[]}`，路径相对仓库根目录 |
| `log_tail` | string | 最后 20 行原始日志 |
| `log_file` | string | **扩展字段**：本次原始日志路径 |
| `schema_version` | string | 契约版本 |

`errors[]`：

```json
{
  "type": "compile | sim_fail | timing | constraint | tool",
  "severity": "error | warning",
  "file": "rtl/foo.v | null",
  "line": 12,
  "message": "一行描述",
  "raw": "原始日志片段（≤5 行）"
}
```

分类规则（`tools/error_classifier.py`）：

| 来源 | type | severity |
|---|---|---|
| iverilog `file:line: error:` / `syntax error` | `compile` | error |
| vvp `FAIL:` / `ERROR:` / `FATAL:` | `sim_fail` | error |
| 时序 WNS < 0 或 setup/hold errors > 0 | `timing` | error |
| duplicated / invalid pin / not found（带 pin/adc 上下文） | `constraint` | 含 WARNING 则 warning |
| TD 其他 ERROR/FATAL、未知非零退出 | `tool` | error |
| TD WARNING | `tool` | warning |
| 报告字段解析不到 | `tool` | warning（字段保持 null） |

`timing` 解析自真实报告：
`Minimal setup slack` → `wns_ns`，`minimal hold slack` → `whs_ns`，
`TNS = ...` → `tns_ns`，`Minimum period is ...` → `min_period_ns`；
`critical_path` 取所有 `Slack (setup check)` 块里最差的一块。
单位支持 ns/ps/us/ms。

`resources` 解析自 TD 综合日志：
`#lut = N` → `lut`，`Packed ... N SEQ` → `ff`，`N brams` → `bram`。
抓不到就是 `null`，不估算。

## 上层 Agent 接入示例（伪代码）

```python
import json, subprocess, sys

def run_stage(stage: str, target: str, *extra: str) -> dict:
    cmd = [sys.executable, "anlogic/td_flow.py", stage, target, "--json", *extra]
    cp = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    result = json.loads(cp.stdout)          # stdout 只有 JSON
    if not result["ok"]:
        for e in result["errors"]:
            if e["severity"] == "error":
                print(f"[{e['type']}] {e['message']}  ({e['file']}:{e['line']})")
    return result

sim = run_stage("sim", "board/exp4_reaction_timer/sim/run_sim.sh")
assert sim["sim"]["fail"] == 0 and sim["sim"]["pass"] >= 37

flow = run_stage("flow", "board/exp4_reaction_timer/flow.tcl",
                 "--reuse-report")      # 或去掉 --reuse-report 重新综合
assert flow["timing"]["wns_ns"] >= 0
```

Agent 判断策略建议：
1. `ok == true` → 通过。
2. `errors` 里 `severity=="error"` 优先看 `compile` / `sim_fail`，再看 `timing`。
3. `resources` / `timing` 为 `null` 且伴随 `type=="tool"` 的 warning，
   说明解析器没认出来（不是设计失败），需要人工看 `log_file`。

## 已知限制

- `sim` 以整个 `run_sim.sh` 为单位，`sim.tb` 是脚本名；
  逐 TB 结果在扩展字段 `sim.testbenches`。
- 没有 bash 时，`sim` 会解析脚本里的 `IVERILOG`/`VVP` 默认路径和
  `run_one`/调用模板直接执行。当前机器有 Git Bash，该分支只做了语法级实现。
- `program --json` 会真的烧板；不提供 dry-run。
- `check` / `download` 暂不支持 `--json`，仍走原逻辑。
