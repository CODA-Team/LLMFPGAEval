# TASK_PROGRESS — td_flow.py 结构化改造

## 阶段 0：侦察

### 0.1 `anlogic/td_flow.py` 现状

| 子命令 | 位置 | 参数 | 行为 | 日志去向 |
|---|---|---|---|---|
| `check` | `td_flow.py:192` | 无 | 检查 TD 可执行文件、器件库、license | 直接 print 到 stdout |
| `flow <tcl>` | `td_flow.py:202` | Tcl 脚本路径 | `run_flow()` → `run_tcl()` 调 `td_commands_prompt.exe`，执行综合/布局布线/bitgen | 返回完整日志；`__main__` 打印到 stdout；TD 自身另写 `<脚本目录>/td_YYYYmmdd_HHMMSS.log` |
| `download <bit>` | `td_flow.py:204` | bit 路径 | 生成临时 Tcl，`download -bit` | 同上；临时 Tcl 默认删除 |
| `program <tcl>` | `td_flow.py:206` | Tcl 路径 | `program_board()`，判定标准含 `Download success` | 同上 |

现有失败判定：`run_flow` = 退出码 0 且日志无 `ERROR/FATAL`；`program_board` 额外要求 `Download success`。
现有代码没有任何 JSON 输出，也没有日志落盘逻辑（`td_*.log` 是 TD 自己写的）。

### 0.2 `reaction_timer_board_timing.rpt` 前 60 行

```text
=========================================================================================================
Auto created by the td v4.6.64949
@Copy Right: Shanghai Anlogic Infotech, 2011 - 2022.
Wed Oct 21 12:56:21 2026
=========================================================================================================


Top Model:                reaction_timer_board
Device:                   eagle_s20
Timing Constraint File:   reaction_timer_board.sdc
STA Level:                Detail
Process parameter::       TT  1.10v  85C

=========================================================================================================
Timing constraint:        clock: clk
Clock = clk, period 20ns, rising at 0ns, falling at 10ns

1438 endpoints analyzed totally, and 26662 paths analyzed
0 errors detected : 0 setup errors (TNS = 0.000), 0 hold errors (TNS = 0.000)
Minimum period is 5.348ns
---------------------------------------------------------------------------------------------------------

Paths for end point u_dut/u_reaction_fsm/reg2_b12|u_dut/u_reaction_fsm/reg2_b6 (56 paths)
---------------------------------------------------------------------------------------------------------
 Slack (setup check)      14.652 ns
 StartPoint:              u_dut/u_reaction_fsm/reg2_b1|u_dut/u_reaction_fsm/reg2_b27.clk (rising edge triggered by clock clk)
 EndPoint:                u_dut/u_reaction_fsm/reg2_b12|u_dut/u_reaction_fsm/reg2_b6.c[1] (rising edge triggered by clock clk)
 Clock group:             clk

 Point                                                       Type                     Incr
---------------------------------------------------------------------------------------------------------
 u_dut/u_reaction_fsm/reg2_b1|u_dut/u_reaction_fsm/reg2_b27.clk clock                   3.580
 launch clock edge                                           clock                   0.000
 u_dut/u_reaction_fsm/reg2_b1|u_dut/u_reaction_fsm/reg2_b27.q[1] cell                    0.146
 u_dut/u_reaction_fsm/lt0_2|u_dut/u_reaction_fsm/lt0_1.b[0]  net (fanout = 3)        0.640
 u_dut/u_reaction_fsm/lt0_2|u_dut/u_reaction_fsm/lt0_1.fco   cell                    0.627
 u_dut/u_reaction_fsm/lt0_4|u_dut/u_reaction_fsm/lt0_3.fci   net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_4|u_dut/u_reaction_fsm/lt0_3.fco   cell                    0.073
 u_dut/u_reaction_fsm/lt0_6|u_dut/u_reaction_fsm/lt0_5.fci   net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_6|u_dut/u_reaction_fsm/lt0_5.fco   cell                    0.073
 u_dut/u_reaction_fsm/lt0_8|u_dut/u_reaction_fsm/lt0_7.fci   net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_8|u_dut/u_reaction_fsm/lt0_7.fco   cell                    0.073
 u_dut/u_reaction_fsm/lt0_10|u_dut/u_reaction_fsm/lt0_9.fci  net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_10|u_dut/u_reaction_fsm/lt0_9.fco  cell                    0.073
 u_dut/u_reaction_fsm/lt0_12|u_dut/u_reaction_fsm/lt0_11.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_12|u_dut/u_reaction_fsm/lt0_11.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_14|u_dut/u_reaction_fsm/lt0_13.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_14|u_dut/u_reaction_fsm/lt0_13.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_16|u_dut/u_reaction_fsm/lt0_15.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_16|u_dut/u_reaction_fsm/lt0_15.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_18|u_dut/u_reaction_fsm/lt0_17.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_18|u_dut/u_reaction_fsm/lt0_17.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_20|u_dut/u_reaction_fsm/lt0_19.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_20|u_dut/u_reaction_fsm/lt0_19.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_22|u_dut/u_reaction_fsm/lt0_21.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_22|u_dut/u_reaction_fsm/lt0_21.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_24|u_dut/u_reaction_fsm/lt0_23.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_24|u_dut/u_reaction_fsm/lt0_23.fco cell                    0.073
 u_dut/u_reaction_fsm/lt0_26|u_dut/u_reaction_fsm/lt0_25.fci net (fanout = 1)        0.000
 u_dut/u_reaction_fsm/lt0_26|u_dut/u_reaction_fsm/lt0_25.fco cell                    0.073
```

### 0.3 `reaction_timer_board_timing.rpt` 关键行（grep 结果）

```text
18:1438 endpoints analyzed totally, and 26662 paths analyzed
19:0 errors detected : 0 setup errors (TNS = 0.000), 0 hold errors (TNS = 0.000)
20:Minimum period is 5.348ns
23:Paths for end point u_dut/u_reaction_fsm/reg2_b12|u_dut/u_reaction_fsm/reg2_b6 (56 paths)
25: Slack (setup check)      14.652 ns
26: StartPoint:              u_dut/u_reaction_fsm/reg2_b1|u_dut/u_reaction_fsm/reg2_b27.clk (rising edge triggered by clock clk)
27: EndPoint:                u_dut/u_reaction_fsm/reg2_b12|u_dut/u_reaction_fsm/reg2_b6.c[1] (rising edge triggered by clock clk)
81: Slack                                                                           14.652 ns
...
731:Constraint path number: 26662 (STA coverage = 99.55%)
732:Timing violations: 0 setup errors, and 0 hold errors.
733:Minimal setup slack: 14.652, minimal hold slack: 0.278
737:	  Clock Name                                  Min Period     Max Freq           Skew      Fanout            TNS
738:	  clk (50.000MHz)                                5.348ns     186.986MHz        0.399ns       256        0.000ns
```

结论：WNS 取 `Minimal setup slack`（14.652），WHS 取 `minimal hold slack`（0.278），
TNS 取 `TNS = 0.000`，最小周期取 `Minimum period is 5.348ns`。
关键路径取 `Slack (setup check)` 数值最小的那一块的 StartPoint/EndPoint。

### 0.4 `reaction_timer_board_clock_summary.rpt` 关键行

```text
<?xml version="1.0" encoding="utf-8"?>
<clock_summary>
    <groups size="1">
        <clock_group name="clk" size="1"/>
    </groups>
    <clock_group name="clk">
        <clock name="clk" freq="50.000MHz" type="master">
            <topology>
                <source num="1">
                    <pin name="clk">
                        <property net="clk" source_latency="max_rise:0ps, min_rise:0ps, max_fall:0ps, min_fall:0ps"/>
                    </pin>
                </source>
                ...
                <loads num="256">
                    <global_network num="256">
                        <iol num="2"> ... </iol>
                        <lslice num="162"> ... </lslice>
                        <mslice num="90"> ... </mslice>
                        <mult18 num="2"> ... </mult18>
```

```text
810:						<max_skew>
811:								<max_rise_skew value="397ps">
...
1073:								<max_fall_skew value="399ps">
```

结论：clock summary 是 XML。可解析字段：clock name / freq / type / loads num /
max_rise_skew / max_fall_skew / source_latency。用 `xml.etree.ElementTree`，不用正则。

### 0.5 `sim/run_sim.sh` 侦察

- 调用方式：`bash board/exp4_reaction_timer/sim/run_sim.sh`
- 工具路径：`IVERILOG="${IVERILOG:-C:/iverilog/bin/iverilog.exe}"`，`VVP="${VVP:-C:/iverilog/bin/vvp.exe}"`
- 编译：`"$IVERILOG" -g2012 -I sim -o "sim/build/$name.vvp" rtl/reaction_timer_board.v "$tb"`
- 运行：`"$VVP" "sim/build/$name.vvp"`，输出经 `tee` 写到 `sim/build/<name>.log`
- 失败判定：任一 TB 非零退出 → 脚本最后退出 1；成功标志 `ALL SIMULATIONS PASSED`
- 工作目录：脚本自己 `cd "$ROOT_DIR"`（`sim/..`，即实验目录）

实测 iverilog 路径：

```text
C:/iverilog/bin/iverilog.exe  (存在, 88064 字节)
C:/iverilog/bin/vvp.exe       (存在, 945664 字节)
Icarus Verilog version 12.0 (devel) (s20150603-1539-g2693dd32b)
```

与之前记录一致。

---

## 进度记录

| 阶段 | 时间 | 状态 | 关键产物 |
|---|---|---|---|
| 0 侦察 | 2026-10-21 14:06 | ✅ 完成 | `TASK_PROGRESS.md`（本节） |
| 1 JSON 契约 | 2026-10-21 14:10 | ✅ 完成 | `tools/result_schema.py` |
| 2 sim 子命令 | 2026-10-21 14:15 | ✅ 完成 | `tools/agent_cli.py`、`anlogic/td_flow.py`（dispatch） |
| 3 flow --json | 2026-10-21 14:17 | ✅ 完成 | `tools/agent_cli.py`（handle_flow） |
| 4 报告解析器 | 2026-10-21 14:09 | ✅ 完成 | `tools/td_report_parser.py`、`tools/test_parser.py` |
| 5 错误分类器 | 2026-10-21 14:12 | ✅ 完成 | `tools/error_classifier.py` |
| 6 回归自测 | 2026-10-21 14:20 | ✅ 全部通过 | 见下节 |

## 阶段 6：回归自测结果（硬验收）

### 6.1 `sim --json`

命令：

```bash
python anlogic/td_flow.py sim board/exp4_reaction_timer/sim/run_sim.sh --json
```

结果：

```text
ok=true  exit_code=0  duration_s=79.038
sim.pass=91  sim.fail=0
sim.testbenches:
  top 37 / board 26 / delay_width 1 / bcd 16 / max_ms 3 / key_scan 2 / debounce 6
errors=[]
log_file=work/logs/sim_20261021_142125_719.log
```

断言：`ok=true`、`sim.pass>=37`、`sim.fail==0` —— **通过**。

### 6.2 `flow --json`

命令：

```bash
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl --json
```

结果：

```text
ok=true  exit_code=0  duration_s=40.889
timing.wns_ns=14.652  whs_ns=0.278  min_period_ns=5.348
resources={lut:455, ff:305, bram:0}
errors=[{type:constraint, severity:warning,
         message:"ARC-6008 WARNING: Found duplicated IO adc_vref in PKG."}]
type=timing 的错误数量：0
log_file=work/logs/flow_20261021_142210_903.log
```

断言：`|wns_ns-14.652|<=0.05`、`errors` 无 `type=timing` —— **通过**。

> 本次共完整重跑 flow 4 次（含兼容性回归），WNS 每次都是 14.652 ns、
> min period 5.348 ns、LUT/FF/BRAM = 455/305/0，bit 均为 629,606 字节，
> 布局布线结果稳定。

另跑了 `--reuse-report`（不重综合）：

```text
ok=true  wns=14.652  resources={lut:455, ff:305, bram:0}  errors=[]
log_file=work/logs/flow_20261021_142232_644.log
```

### 6.3 解析器单元测试

```text
python tools/test_parser.py
PASS: 1/14 ... PASS: 14/14
ALL TESTS PASSED (14)
```

硬验收 `parse_timing().wns_ns == 14.652 ±0.001` 通过。
另覆盖 WHS=0.278、TNS=0、min_period=5.348、关键路径、ps 单位、
时钟摘要 XML、资源 455/305/0、缺失文件返回 null。

### 6.4 编译错误注入

方法：把 `rtl/`、`sim/` 复制到 `work/mutation_compile/`，在副本 RTL
末尾追加一行 `syntax error injected for classifier test`，不改原文件；
测试完删除副本。

```bash
python anlogic/td_flow.py sim work/mutation_compile/sim/run_sim.sh --json
```

结果：

```text
ok=false  exit_code=1  sim.pass=0  sim.fail=0
errors[0] = {
  "type": "compile",
  "severity": "error",
  "file": "rtl/reaction_timer_board.v",
  "line": 780,
  "message": "syntax error"
}
log_file=work/logs/sim_20261021_142224_848.log
```

断言：`errors[0].type=="compile"` 且带 file/line —— **通过**。

### 6.5 向后兼容

```bash
python anlogic/td_flow.py check          # EXIT=0
python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl
# 输出首行 SUCCESS，末尾 FLOW FINISHED，TIMING WNS/WHS: 14.652...
# EXIT=0
```

旧调用路径未改变。

---

## 附加任务：闭环自测与文档

| 阶段 | 时间 | 状态 | 关键产物 |
|---|---|---|---|
| A 闭环自测 | 2026-10-21 14:35 | ✅ 通过 | `tools/mock_agent_loop.py`、`work/logs/mock_agent_loop.log` |
| B Agent 文档 | 2026-10-21 14:37 | ✅ 完成 | `README_AGENT.md` |
| C 路径检查 + 回归 | 2026-10-21 14:38 | ✅ 通过 | `python tools/test_parser.py` → 14/14 |

### A. `mock_agent_loop.py` 实跑结果

副本位于 `work/mock_agent/`，原设计文件未修改；成功后副本已自动清理。

```text
[before] exit=1 ok=False duration=3.8s pass=0 fail=0 errors=['compile']
         errors[0] = {type: compile, file: rtl/reaction_timer_board.v,
                      line: 781, message: "syntax error"}
         按 JSON 行号 781 删除注入行 'wire mock_agent_inject_broken'
[after]  exit=0 ok=True  duration=73.4s pass=91 fail=0 errors=[]
         逐 TB: top 37 / board 26 / delay_width 1 / bcd 16 / max_ms 3 /
                key_scan 2 / debounce 6
=== mock agent loop 结束: SUCCESS ===
```

证据：`work/logs/mock_agent_before.json`、`work/logs/mock_agent_after.json`、
`work/logs/mock_agent_before.stderr.log`、`work/logs/mock_agent_after.stderr.log`。
控制台输出已切 UTF-8，无乱码。

### B. `README_AGENT.md`

包含：项目定位、数据流架构图、四个阶段的命令、JSON 契约速览与示例、
可直接投喂给 LLM 的提示词、Agent 决策表、闭环自测说明、文件地图、
已知限制、红线。未改动原有 `README.md`。

### C. 绝对路径与回归

- `tools/mock_agent_loop.py` 与 `README_AGENT.md` 源码中无硬编码绝对路径
  （全部由 `__file__` 推导或使用仓库相对路径）。
- 运行期日志会记录实际解释器/仓库绝对路径，仅用于排查，不影响可移植性。
- `python tools/test_parser.py` → `ALL TESTS PASSED (14)`，硬验收
  `wns_ns == 14.652 ±0.001` 仍通过。
