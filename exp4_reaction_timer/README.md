# 实验4：反应速度测试器（上板版）

> 源码发行说明：本文保留原示例的历史验证记录。源码包不附带预生成比特流、旧版 work 目录或复位检查工程；新机器需重新综合并验证。下方命令均从仓库根目录执行。

板卡：**DMZ-EG4S20 / EG4S20BG256**，50 MHz 晶振。

本目录是实验4 面向真实硬件的版本，源设计来自 `work/exp4_top/final_rtl.v`。
旧版本只做过仿真，且存在几个在真实硬件上必然暴露的问题；本版本已修复并用
新的自检 Testbench 验证。

## 当前状态（2026-10-21）

| 项目 | 状态 |
|---|---|
| RTL 仿真（7 个 TB：顶层/板级/位宽/BCD/封顶/扫描/真实去抖） | ✅ 全部 PASS |
| TD 4.6 综合 / 布局 / 布线 / bitgen | ✅ 通过 |
| 静态时序（50 MHz） | ✅ WNS **+14.652 ns**，0 setup/hold 错误 |
| 比特流 | ✅ `reaction_timer_board.bit`（629,606 字节） |
| 上板验证 | ✅ **通过**（2026-10-21，见下方实测结果） |

> 首次布线时 WNS 为 **-32.353 ns**（BCD 组合 double-dabble 和
> `lfsr % 5` 两条长路径）。已改为顺序 BCD 转换 + 两拍延迟计算，
> 见下方修复表第 5、6 条。

## 相对旧 RTL 的修复

| # | 问题 | 旧行为 | 修复 |
|---|---|---|---|
| 1 | 数码管接口不匹配 | 16 位静态输出，每个 7 段码只取低 4 位，段 G 丢失，0/3/8/9 等撞码 | `display_driver` 改为 8 位段码 + 4 位位选动态扫描 |
| 2 | 二进制值当 BCD 显示 | `ms_value` 是二进制计数，直接按 4 个 BCD 半字节送显示，1234 会显示成 04D2（D 还熄灭） | 增加 double-dabble `bin_to_bcd`，先转 BCD 再显示 |
| 3 | 重新启动不清屏 | 从结果态重新开始时，WAIT_DELAY 保留上一轮结果 | WAIT_DELAY 每拍清 `display_value` |
| 4 | 随机延迟范围失效 | 16 位 LFSR 对 `(MAX-MIN)×50MHz` 取模，模数 2 亿远大于 65535，实际延迟只有 1.0000~1.0013 秒 | 先随机整秒数，再乘 `CLK_FREQ`，真正覆盖 1~5 秒 |
| 5 | BCD 转换时序不收敛 | 组合 double-dabble 有 14 级，50 MHz 下路径 52 ns，WNS = -32 ns | 改为 `bin_to_bcd_seq` 顺序转换，14 拍完成 |
| 6 | 随机延迟计算时序不收敛 | `16 位 lfsr % 5` 综合成多级比较/减法链，与 32 位乘法串成 52 ns 路径 | 取 3 位随机数做条件减，分两拍锁存秒数、再乘 `CLK_FREQ` |
| 7 | 扫描换位鬼影 | 位选和段码同拍切换，引脚偏斜会造成串位 | 每位开始/结束各消隐 16 拍（320 ns） |
| 8 | 异步输入亚稳态 | `key_col` 直接进扫描器 | 板级加两级同步器 |

## 文件

| 文件 | 作用 |
|---|---|
| `rtl/reaction_timer_board.v` | 完整 RTL：去抖、延迟、ms 计数、扫描显示、FSM、矩阵键盘、板级封装 |
| `sim/tb_common.vh` | TB 公共参考模型（BCD、段码、扫描检查） |
| `sim/tb_reaction_timer_top.v` | 顶层自检 TB（12 组测试） |
| `sim/tb_reaction_timer_board.v` | 板级自检 TB（矩阵键盘、低有效复位、显示、双键同按） |
| `sim/tb_delay_width.v` | 真实 50 MHz 下的 `delay_target` 位宽/取值检查 |
| `sim/tb_bcd.v` | `bin_to_bcd_seq` 的 0~9999 边界值检查 |
| `sim/tb_max_ms.v` | MAX_MS=9999 封顶的端到端验证（计数器/结果/BCD） |
| `sim/tb_key_scan_div.v` | 矩阵扫描默认 12500 分频、整轮 50000 拍 |
| `sim/tb_debounce_real.v` | 真实 `DEBOUNCE_CNT=500000` / 50 MHz 去抖行为 |
| `sim/run_sim.sh` | 一键编译并运行七个 TB |
| `reaction_timer_board.adc` | 引脚约束（Tcl 格式，供 GUI 导入/对照） |
| `reaction_timer_board.sdc` | 50 MHz 时钟约束 |
| `flow.tcl` | TD 4.6 HDL2Bit 流程 |
| `program.tcl` | JTAG 烧录脚本 |
| `reaction_timer_board.bit` | 已生成的比特流 |

## 引脚约束

| 信号 | FPGA 引脚 | 说明 |
|---|---|---|
| `clk` | **R7** | 50 MHz |
| `rst_n` | **A9** | 低有效；文档标注与 SW0 同脚，上板前请核对 |
| `key_row[0..3]` | **D9, F9, C10, E10** | 矩阵键盘行输出，低有效 |
| `key_col[0..3]` | **E11, D11, C11, F10** | 矩阵键盘列输入，按下为低 |
| `ready_led` | **B14** | LED0，高电平点亮 |
| `seg[0..7]` | **A4, A6, B8, E8, A7, B5, A8, C8** | a,b,c,d,e,f,g,dp；段高电平点亮 |
| `digit_sel[0..3]` | **C9, B6, A5, A3** | COM4..COM1；低电平选中，`[0]` 最右位 |

> 引脚来自大拇指 DMZ-EG4S20 板卡文档（表1.2/1.3/1.4）。
> **上板前请对照丝印或原理图再确认一次。**

### 矩阵键盘

板级封装把两个键映射为：

| 功能 | 板上丝印 | 行 | 列 |
|---|---|---|---|
| 开始 / 重新开始（start） | **key12** | Row0 | Col0 |
| 应答（response） | **key13** | Row0 | Col1 |

矩阵键盘是 4×4 行列结构，不是两个独立按键。若实际想用别的两个键，
只需修改 `rtl/reaction_timer_board.v` 中 `reaction_timer_board` 实例化
`matrix_key_scanner` 时的 `START_ROW/START_COL/RESP_ROW/RESP_COL` 四个参数。

列线依赖**外部上拉**。如果板上没有上拉，需要在 TD 中给 `key_col` 配置
内部上拉；否则列线悬空，读不到按键。

## 仿真

```bash
# 从仓库根目录
bash exp4_reaction_timer/sim/run_sim.sh
```

或手动：

```bash
cd exp4_reaction_timer
C:/iverilog/bin/iverilog.exe -g2012 -I sim \
    -o sim/build/top.vvp rtl/reaction_timer_board.v sim/tb_reaction_timer_top.v
C:/iverilog/bin/vvp.exe sim/build/top.vvp

C:/iverilog/bin/iverilog.exe -g2012 -I sim \
    -o sim/build/board.vvp rtl/reaction_timer_board.v sim/tb_reaction_timer_board.v
C:/iverilog/bin/vvp.exe sim/build/board.vvp
```

成功标志：日志最后出现 `ALL_TESTS_PASSED`。

### TB 覆盖

**顶层 `tb_reaction_timer_top.v`：**

1. 复位后 `ready_led=0`、显示 `0000`
2. 短于去抖窗口的启动毛刺被忽略
3. 启动后 `ready_led` 延迟落在 `[MIN_DELAY, MAX_DELAY]` 秒窗口
4. READY 后应答 → 显示值为实测 ms 的 BCD，且结果保持
5. 结果态重新启动 → 清屏并重新测量
6. READY 前应答 → `FAIL`（0xFFFF），`FAIL_TIME` 后回 IDLE
7. 长时间不应答 → ms 封顶 `MAX_MS`
8. READY 中途复位 → 恢复 IDLE
9. READY 中应答毛刺被忽略
10. 强制 0x1234/0x0000/0x9999/0xFFFF，检查 4 位扫描段码
11. 随机按键 2000 拍，输出无 X/Z
12. 延迟发生器 1~5 秒范围覆盖（独立 20 kHz 实例，40 轮）

**专项 TB：**

13. **真实 50 MHz** 下 `delay_target` 取 50M/100M/150M/200M/250M，验证 5 秒需要 28 位、32 位寄存器不溢出
14. BCD 转换器 0/1/9/10/99/100/999/1000/1234/9999 等 16 个边界值
15. MAX_MS=9999 封顶：计数器停在 9999 不回绕，`result_latch=9999`，显示 `0x9999`
16. 矩阵扫描默认 `KEY_SCAN_DIV=12500`，四行整轮 50000 拍
17. 同一行 start+response 同时按下 → start 优先，进入 WAIT_DELAY 而非 FALSE_START
18. **真实 `DEBOUNCE_CNT=500000`、50 MHz**：499999 拍毛刺被拒绝；
    500100 拍按下 → 两个通道各恰好一个脉冲；按住不重复触发；
    松开不产生脉冲；松开不足 10 ms 再按被忽略；松开 ≥10 ms 再按产生第二个脉冲

> 顶层 TB 和板级 TB 为仿真速度把 `DEBOUNCE_CNT` 覆盖成 5
> （板级 TB 还把 `KEY_SCAN_DIV` 覆盖成 4）。真实 10 ms 去抖由
> `tb_debounce_real.v` 单独覆盖，真实 12500 分频由
> `tb_key_scan_div.v` 单独覆盖。

### 变异测试（证明 TB 不是盲的）

> 这 4 组变异是在**时序修复前**的同功能 RTL 上执行的；修复后
> 七个 TB 仍全绿。变异针对的是功能（BCD、清屏、段码、延迟范围），
> 不涉及时序重构。

把以下缺陷逐个注回 RTL，TB 均按预期失败：

| 注入的缺陷 | TB 报错 |
|---|---|
| 去掉 binary→BCD | `T3_display_is_bcd_of_result expected=0010 actual=000a` |
| WAIT_DELAY 不清屏 | `T4_restart_clears_display expected=0000 actual=0010` |
| 段码截断成低 4 位 | `seg mismatch idx=0 ... expected=0111111 actual=1111000` |
| 恢复旧 LFSR 随机公式 | `never observed a ~5s delay (max=83842)` |

## 上板顺序（重要）

先核对 A9 复位电平和实际板卡连线，再运行完整设计。原开发过程使用过独立的最小复位检查工程，该工程不在本源码包中；需要时可在网页新建复位/LED 实验验证。

```bash
# 完整实验4；program 会实际写入已连接的 FPGA。
python anlogic/td_flow.py flow    exp4_reaction_timer/flow.tcl
python anlogic/td_flow.py program exp4_reaction_timer/program.tcl
```

烧录成功时日志出现 `Download success`。

## 综合 / 烧录

```bash
# 从仓库根目录
python anlogic/td_flow.py check
python anlogic/td_flow.py flow exp4_reaction_timer/flow.tcl
python anlogic/td_flow.py program exp4_reaction_timer/program.tcl
```

运行后将在本目录生成 `reaction_timer_board.bit`（源码包不包含该文件）；
`flow.tcl` 在 route 之后会生成时序报告并打印 `TIMING WNS/WHS`。

## 上板实测结果（2026-10-21）

| 项目 | 结果 |
|---|---|
| 键映射 | start = **key12**（Row0/Col0），response = **key13**（Row0/Col1） |
| 实测反应时间 | **233 / 172 / 216 / 181 ms**，BCD 显示正常，多次测量稳定 |
| 超时封顶 | 等待超过 10 s 再按 response，显示 **9999** |
| 抢跑提示 | READY 前按 response，显示 **FA11**（`0xFFFF` 的 F/A/1/1 段码），约 2 s 后回 `0000` |
| 重开清屏 | 结果态按 key12，显示清回 `0000`，B14 熄灭，新一轮正常 |
| 动态扫描 | 四位清晰、无鬼影 |

> 反应时间读数包含约 10~11 ms 的固定误差：10 ms 去抖窗口 +
> 1 ms 矩阵扫描周期，与设计取舍一致（见"注意事项"）。

## 上板预期现象

1. 复位后：数码管显示 `0000`，LED 熄灭。
2. 按 start 键：数码管清为 `0000`，进入随机等待。
3. 1~5 秒后：LED 点亮，开始计时。
4. 按 response 键：LED 熄灭，数码管显示反应时间（ms，4 位十进制）。
5. 在 LED 点亮前按 response：数码管显示 `FA11`（`0xFFFF`，即 "FAIL"），
   约 2 秒后回到 IDLE（显示 `0000`）。
6. 显示结果时再按 start：开始新一轮。

### 操作注意（实测按这个来）

- **两个键要分先后按**：先按 start，松开后再按 response。
  “同一拍双键按下时 start 优先”只是逻辑上的理想情况；实际按键
  几乎不可能严格同拍，response 稍晚一点就会被判成抢跑（FAIL）。
- **response 是边沿触发**：按住不放不会触发测量。从等待阶段一直
  按到 LED 亮，也必须**先松开、再按一次**才会记录反应时间。
- **同一个键松开后要稳定 ≥10 ms 再按才会被识别**：去抖对释放同样
  生效（`tb_debounce_real.v` 已验证），快速连点同一个键时后面的
  按下会被忽略。正常人手速度远大于 10 ms，不影响使用。

## 注意事项 / 已知取舍

- `rst_n = A9` 已用 `exp4_reset_check` 实测确认：松开时 A9 为高、
  B14 LED 恒亮，板上 SW0 未把 A9 拉低，可正常复位。
- 矩阵键盘列线依赖外部上拉。若原理图上行/列方向与本文相反，
  需要把 `key_row` / `key_col` 的方向和扫描逻辑对调。
- 数码管每位点亮 1 ms（`SCAN_DIV=50000`），整帧 4 ms（250 Hz）。
  若觉得偏暗或有闪烁，可减小 `SCAN_DIV` 提高刷新率。
- 去抖窗口是 **10 ms**（`DEBOUNCE_CNT=500000`）。矩阵扫描已改为
  `KEY_SCAN_DIV=12500`（每行 0.25 ms、整轮 1 ms），所以显示的反应
  时间额外误差约 **10~11 ms**。
- 随机延迟按整秒取值（1、2、3、4、5 秒），符合"1~5 秒"的需求；
  3 位随机数的分布略不均匀（1/2/3 秒概率略高），对等待时间无影响。
- 时序报告：`reaction_timer_board_timing.rpt`（最差路径）和
  `reaction_timer_board_clock_summary.rpt`（时钟树）。
- 旧的 `work/exp4_top/` 仍保留，用于追溯旧版 16 位接口和旧 TB；
  不要再把它的 `final_rtl.v` 当作上板版本。
