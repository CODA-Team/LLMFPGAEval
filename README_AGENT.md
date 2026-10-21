# RTL-scout / Agent 工具链说明

面向大模型 Agent 的安路（Anlogic）TD 4.6 FPGA 工具链封装。
目标：让 Agent **只消费一份 JSON**，就能判断仿真是否通过、时序是否收敛、
资源占用多少，并据此修改 RTL，而不是去读几百行原始日志。

> 详细的 schema 与解析器说明见 [`tools/README.md`](tools/README.md)。
> 实验与上板说明见
> [`board/exp4_reaction_timer/README.md`](board/exp4_reaction_timer/README.md)
> （本文件不改动它）。

## 核心架构

```text
            ┌──────────────────────────────────────────────┐
            │  Agent / 人：生成或修改 RTL                   │
            └──────────────────────┬───────────────────────┘
                                   │
                    python anlogic/td_flow.py sim <run_sim.sh> --json
                                   │
            ┌──────────────────────▼───────────────────────┐
            │  Icarus 编译 + 自检 TB                        │
            │  → sim.pass / sim.fail / failed_checks        │
            │  → errors[type=compile|sim_fail] + file:line  │
            └──────────────────────┬───────────────────────┘
                                   │ 有问题：按 file:line 改 RTL
                    python anlogic/td_flow.py flow <flow.tcl> --json
                                   │
            ┌──────────────────────▼───────────────────────┐
            │  TD 4.6：综合 / 布局 / 布线 / bitgen          │
            │  → timing.wns_ns / whs_ns / critical_path     │
            │  → resources.lut / ff / bram                  │
            │  → errors[type=timing|constraint|tool]        │
            └──────────────────────┬───────────────────────┘
                                   │ 人工确认后
                    python anlogic/td_flow.py program <program.tcl> --json
                                   │
                           真实烧板（不可 dry-run）
```

| 阶段 | 命令 | 主要产物 |
|---|---|---|
| 仿真 | `td_flow.py sim ... --json` | 编译/自检错误、PASS/FAIL 计数、逐 TB 明细 |
| 综合布线 | `td_flow.py flow ... --json` | WNS/WHS/TNS/最小周期、关键路径、LUT/FF/BRAM |
| 快速回归 | `td_flow.py flow ... --json --reuse-report` | 不重跑 TD，直接解析上一轮报告 |
| 烧录 | `td_flow.py program ... --json` | 真实下载，成功标志 `Download success` |

所有结构化调用的 stdout **只有 JSON**；原始日志写 stderr，并落盘到
`work/logs/<stage>_<timestamp>.log` 和同名 `.json`。
退出码：`ok=true` → `0`，否则非 `0`。

## 快速开始

```bash
# 1) 仿真（示例：反应计时器，7 个 TB）
python anlogic/td_flow.py sim board/exp4_reaction_timer/sim/run_sim.sh --json

# 2) 综合 + 布局 + 布线 + 时序解析（约 30~60 秒）
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl --json

# 3) 只解析现成报告（秒级）
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl --json --reuse-report

# 4) 烧板（会真实写入 FPGA，确认后再执行）
python anlogic/td_flow.py program board/exp4_reaction_timer/program.tcl --json
```

## JSON 契约速览

```json
{
  "schema_version": "1.0",
  "stage": "flow",
  "ok": true,
  "exit_code": 0,
  "duration_s": 40.889,
  "errors": [
    {
      "type": "constraint",
      "severity": "warning",
      "file": null,
      "line": null,
      "message": "ARC-6008 WARNING: Found duplicated IO adc_vref in PKG.",
      "raw": "ARC-6008 WARNING: ..."
    }
  ],
  "sim": null,
  "timing": {
    "wns_ns": 14.652,
    "tns_ns": 0.0,
    "whs_ns": 0.278,
    "min_period_ns": 5.348,
    "critical_path": {
      "startpoint": "u_dut/u_reaction_fsm/reg2_b1|...clk",
      "endpoint": "u_dut/u_reaction_fsm/reg2_b12|...c[1]",
      "slack_ns": 14.652
    }
  },
  "resources": {"lut": 455, "ff": 305, "bram": 0},
  "artifacts": {
    "bit": "board/exp4_reaction_timer/reaction_timer_board.bit",
    "reports": ["board/exp4_reaction_timer/reaction_timer_board_timing.rpt"]
  },
  "log_tail": "...",
  "log_file": "work/logs/flow_20261021_142210_903.log"
}
```

关键字段：

- `errors[].type`：`compile` / `sim_fail` / `timing` / `constraint` / `tool`
- `errors[].severity`：`error` 影响 `ok`，`warning` 不影响
- `errors[].file`：相对设计工程根目录（如 `rtl/reaction_timer_board.v`），
  `line` 为 1-based 行号；按 `<工程目录>/<file>` 打开即可
- `sim.failed_checks`：失败的断言原文（来自 `FAIL:` / `ERROR:` / `FATAL:`）
- `timing.critical_path`：最差 setup slack 的起点/终点，直接用于定位长路径
- `resources`：解析不到时为 `null`，**不要自己猜**

## 给 LLM 的接入提示词示例

把下面这段作为系统提示或任务提示的一部分：

```text
你是一个 FPGA RTL 修复 Agent。每次运行后你会收到一个 JSON（schema 见
tools/result_schema.py），stdout 只有这一份 JSON，完整日志路径在 log_file。

处理规则：
1. 先看 ok。ok=true 表示本次阶段通过，可以进入下一阶段。
2. ok=false 时，只看 errors 中 severity="error" 的条目，按优先级处理：
   - compile：用 file + line 打开对应源文件，只修复该行的语法/端口/位宽错误。
     不要修改 Testbench、约束文件或 flow.tcl 来绕过编译错误。
   - sim_fail：读 sim.failed_checks 找到失败的断言，回到 RTL 修功能。
   - timing：wns_ns < 0 时读 timing.critical_path 的 startpoint/endpoint，
     定位组合逻辑长路径；优先考虑插入流水线寄存器、拆分运算、把组合
     BCD/除法/取模改成多周期实现。
   - constraint：检查引脚约束是否重复、缺失或与封装不匹配。
   - tool：说明是工具/环境问题，不要改 RTL，先读 log_file 并报告。
3. timing 或 resources 为 null 且伴随 type="tool" 的 warning 时，说明解析器
   没识别出格式，不代表设计失败；不要编造数值，先读 log_file。
4. warning（例如 ARC-6008）不影响 ok，但要在报告里保留，不要静默忽略。
5. 每次修改后重新运行完全相同的命令，直到 ok=true 且没有 severity=error。
6. 禁止调用 program --json 烧板，除非人类明确确认。它会真实写入 FPGA。
7. 只改 RTL；不要为了让测试通过而改 TB、.adc、.sdc、flow.tcl。
```

### Agent 决策表

| JSON 现象 | Agent 动作 |
|---|---|
| `errors[].type=compile` 且带 `file/line` | 打开该文件该行，修语法；重跑 `sim` |
| `errors[].type=sim_fail` | 看 `sim.failed_checks`，修 RTL 功能；重跑 `sim` |
| `timing.wns_ns < 0` | 看 `critical_path`，改流水线/拆分组合逻辑；重跑 `flow` |
| `errors[].type=constraint` | 检查 `.adc` / `set_pin_assignment` |
| `resources.lut/ff/bram` 为 `null` | 读 `log_file` 确认日志格式，不要估算 |
| `ok=false` 且仅 `type=tool` | 环境/工具问题，不要改 RTL |
| `ok=true` | 进入下一阶段；记录 `artifacts.bit` / `timing` 指标 |

## 闭环自测

`tools/mock_agent_loop.py` 模拟上面这套流程：

1. 把示例设计的 `rtl/`、`sim/` 复制到 `work/mock_agent/`；
2. 在副本 RTL 末尾注入 `wire mock_agent_inject_broken`（缺分号）；
3. 调 `sim --json`，从 `errors[0]` 拿 `file` / `line`；
4. 按行号删除注入行；
5. 再跑 `sim --json`，要求 `ok=true`、`fail=0`、`pass>=37`；
6. 全过程写入 `work/logs/mock_agent_loop.log`，中间 JSON 保存为
   `work/logs/mock_agent_before.json` / `mock_agent_after.json`。

```bash
python tools/mock_agent_loop.py
```

实测结果：第一次 `ok=false`、`errors[0].type=compile`、定位到第 781 行；
修复后 `ok=true`、`pass=91`、`fail=0`。原设计文件全程未被修改。

## 文件地图

| 路径 | 作用 |
|---|---|
| `anlogic/td_flow.py` | 统一入口：check / flow / program / download / sim |
| `tools/result_schema.py` | JSON 契约与校验 |
| `tools/td_report_parser.py` | 时序/时钟/资源报告解析 |
| `tools/error_classifier.py` | 日志 → 结构化 errors |
| `tools/agent_cli.py` | 结构化子命令实现、日志落盘、bash 降级 |
| `tools/test_parser.py` | 解析器单元测试（真实报告） |
| `tools/mock_agent_loop.py` | Agent 闭环模拟 |
| `work/logs/` | 结构化日志样例与自测证据 |

## 已知限制

- **`program --json` 会真实烧板**：没有 dry-run，Agent 不应自行调用。
- `check` / `download` 暂不支持 `--json`，仍走原有输出。
- `sim` 以整个 `run_sim.sh` 为单位：`sim.tb` 是脚本名，逐 TB 明细在
  扩展字段 `sim.testbenches`。
- 没有 Bash 时，`sim` 会解析脚本里的工具路径和调用模板逐条执行；
  该降级路径假定脚本保持 `run_one` + `IVERILOG`/`VVP` 模板结构。
- 所有解析不到的报告字段一律 `null` + tool warning，不做估算。
- `flow --json` 默认会重新综合布线（约几十秒）；只想快速回归时用
  `--reuse-report`。
- ARC-6008（PKG 自带 `adc_vref` 重复提示）会作为 constraint/warning
  保留在 `errors[]` 中，不影响 `ok`。
- 本仓库的示例设计已上板验证；JSON 工具链本身的回归基准是
  WNS **+14.652 ns**、LUT/FF/BRAM **455/305/0**。

## 红线

1. 不修改 Testbench、`.adc`/`.sdc`、`flow.tcl`/`program.tcl` 来“让测试过”。
2. 不修改 `work/exp4_top/` 下的历史文件。
3. 不删除或重命名现有文件；新增能力优先放在 `tools/`。
4. 烧板前必须有人确认。
