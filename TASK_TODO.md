# TASK_TODO —— 保守决策与卡点记录

## 约束执行确认

| 约束 | 执行情况 |
|---|---|
| 不改 RTL / TB / 约束 / flow.tcl / program.tcl | ✅ 未改动。编译错误注入用的是 `work/mutation_compile/` 副本，测完已删除 |
| 不改 `work/exp4_top/` | ✅ 未触碰 |
| 不删除/重命名现有文件 | ✅ 仅新增文件；`td_flow.py` 原有函数未改 |
| 卡住 >15 分钟跳过并记录 | ✅ 无此类卡点（见下） |
| 阶段进度记录 | ✅ `TASK_PROGRESS.md` |

## 保守决策

1. **`td_flow.py` 的接入方式**
   - 阶段要求"只在文件末尾追加函数"，但 `if __name__ == "__main__":` 在文件中部，
     运行时末尾函数尚未定义，无法调用。
   - 采用方案：在 `action = sys.argv[1]` 之后插入 15 行 pre-dispatch，导入
     `tools/agent_cli.py`；未命中（无 `sim`、无 `--json`）时返回 `None`，
     继续执行原有代码。所有新逻辑在 `tools/agent_cli.py`，原有函数一行未改。
   - 已用无 `--json` 的 `flow` 回归验证：输出 `SUCCESS` / `FLOW FINISHED`，
     行为不变。

2. **`sim` 的粒度**
   - 现有 `run_sim.sh` 一次跑 7 个 TB，没有"单 TB"入口。
   - `sim.tb` 填脚本名 `run_sim.sh`，`sim.pass/fail` 是整轮汇总；
     逐 TB 明细放在扩展字段 `sim.testbenches`。
   - 若要单 TB 粒度，需要新增一个不修改现有脚本的包装脚本（本次未做）。

3. **资源占用字段的来源**
   - `lut`：`SYN-2581 : Mapping with K=5, #lut = 455`
   - `ff`：`SYN-4008 : Packed 474 LUT and 305 SEQ to BLE.`（取 SEQ）
   - `bram`：`RUN-1001 : ... 0 brams ...`
   - 未使用物理 slice 数（234 mslices），因为它不是任务契约里的 LUT/FF/BRAM 语义。
   - 三个值都来自真实日志；抓不到就是 `null` + tool warning。

4. **ARC-6008 的严重级别**
   - 日志原文：`ARC-6008 WARNING: Found duplicated IO adc_vref in PKG.`
   - 项目 `.adc` 里没有手写 `adc_vref`（此前已核查），这是器件包 DB 自带的提示。
   - 分类为 `constraint` + `severity=warning`，保留在 `errors[]` 里不吞掉，
     但不影响 `ok`。
   - 若某天真的出现 `duplicated pin` 且无 WARNING 字样，会按 `severity=error` 处理。

5. **ARC-1001 不能当 warning**
   - 初版 warning 正则用了 `ARC-\d+`，把 `ARC-1001 : Device Initialization.`
     整段初始化信息误报成 warning（9 条）。
   - 已改为只匹配 `\bWARNING\b`，复跑后仅剩 ARC-6008 一条 warning。

6. **`--reuse-report` 的资源来源**
   - 实验目录下最新的 `td_*.log` 可能是 `program` 日志（没有综合信息）。
   - 实现为：从新到旧找第一个包含 `#lut =` / `Packed ... SEQ` / `brams` 的日志。
   - 直接解析报告路径来自 `flow.tcl` 的 `report_timing -file` /
     `report_clock_summary -file` / `bitgen -bit`，不硬编码文件名。

7. **没有 bash 时的降级**
   - 解析 `run_sim.sh` 里的 `IVERILOG="${IVERILOG:-...}"`、`VVP="${VVP:-...}"`、
     `run_one <name> <tb>` 和 `"$IVERILOG"/"$VVP"` 调用模板，逐个直接执行。
   - 限制：假定脚本保持现有 `run_one` + 模板结构；脚本结构大改时需要同步调整。
   - 本机有 Git Bash（`/usr/bin/bash`），主路径已验证；降级路径只做了静态实现，
     未在无 bash 环境实测。

8. **JSON 扩展字段**
   - 契约必需字段全部保留；额外增加：
     `schema_version`、`log_file`、`sim.testbenches`、
     `timing.clocks`、`timing.clock_groups`、`timing.setup_errors`、
     `timing.hold_errors`、`timing.parse_error`、`reuse_report`。
   - 上层 Agent 可以忽略这些字段，不影响契约。

9. **`check` / `download --json` 未实现**
   - 任务范围是 sim/flow/program。为避免破坏旧行为，这两个子命令即使带
     `--json` 也走原逻辑。记录在此，后续需要时再补。

10. **`program --json` 会真实烧板**
    - 没有 dry-run。本次未调用（避免无谓地重烧已验证的板子）。

11. **日志落盘**
    - JSON 模式 stdout 只放 JSON；原始日志写 stderr。
    - 同时写 `work/logs/<stage>_<timestamp>.log` 和同名 `.json`，
      `log_file` 字段指向前者。
    - 不再额外复制 `_raw.log`（早期实现有重复，已删除）。

12. **`_latest_td_log` 的选择**
    - 初版直接取 mtime 最新的 `td_*.log`，结果选中 `program` 日志，
      资源解析全为 null。已改为按内容筛选（决策 6）。

## 卡点

无超过 15 分钟的卡点。遇到并当场解决的两个问题：

| 问题 | 现象 | 解决 |
|---|---|---|
| ARC-6008 被判成 constraint error | `flow --json` ok=false | 含 WARNING 的 constraint 降为 warning |
| `_latest_td_log` 选中 program 日志 | reuse 模式 resources 全 null | 按内容筛选含综合信息的日志 |

## 闭环自测与文档阶段（2026-10-21）

13. **mock 副本目录固定为 `work/mock_agent/`**
    - 每次先安全删除（限制在 `work/` 内），成功后自动清理，失败保留现场。
    - 只复制 `rtl/`、`sim/`，不复制约束、bit、TD 日志，避免误用旧产物。

14. **注入点的选择**
    - 在副本 RTL 末尾追加缺分号的 `wire mock_agent_inject_broken`，
      属于纯语法错误，不改变任何功能语义；不改原文件。
    - 修复方式严格使用 JSON 返回的 `line`；只有行号失配时才退回按标记搜索。
      这验证了 Agent 能仅凭结构化字段定位问题。

15. **`MIN_PASS=37` 的阈值**
    - 取最强的单 TB 断言数（`top` 为 37），实际全量为 91。
    - 阈值只用于闭环防呆，不作为覆盖率指标。

16. **运行期日志中的绝对路径**
    - `mock_agent_loop.py` 源码无硬编码绝对路径；日志里的解释器/仓库路径
      是运行现场信息，便于复现，不参与任何逻辑判断。
    - `README_AGENT.md` 中的命令全部为仓库相对路径。

## 自测中发现、但按约束未处理的点

- `sim` 全量耗时 73~97 秒，主要来自 `tb_debounce_real.v` 的 50 万周期级
  去抖测试。Agent 侧若把超时设成 60 秒会误判失败；`agent_cli` 默认超时
  远大于此（1800 秒）。未改 TB 缩短时间。
- `run_sim.sh` 的编译输出没有 `tee` 到单 TB 日志，但编译错误仍会进入
  stderr，分类器可以正常抓取 `file:line`。未改脚本。
- ARC-6008 是 PKG 自带的重复 IO 提示，属于封装库问题，不是本次设计引入；
  保持 warning，不影响 `ok`。
- 其余未发现 RTL 功能或工具链逻辑问题：7 个 TB 全过、时序 WNS +14.652 ns、
  资源与历次 flow 一致。

## 未覆盖 / 后续可做

- `sim` 单 TB 粒度（需要不侵入现有脚本的包装入口）。
- 无 bash 降级路径的实机验证。
- `check` / `download` 的 JSON 化。
- 失败 flow 的 `timing` 字段：若 TD 在 report 生成前就失败，`timing=null`
  且只有 tool warning；这是"不猜"的保守结果。
