# 实验：4 位十进制计算器（EG4S20BG256）

> 源码发行说明：以下数值是原示例的历史验证记录，不代表新机器已经完成验证。源码包不含预生成比特流、波形或 work 目录；请配置自己的工具后重新运行。下方命令均从仓库根目录执行。

在 DMZ-EG4S20 上用 4x4 矩阵键盘和 4 位共阴数码管实现一个
0~9999 的十进制计算器，支持 `+ - * /`、`=`、`C`。

- 顶层：`rtl/calculator_top.v`
- 时钟：50 MHz（R7）
- 复位：低有效（A9）
- 仿真：5 个 TB，**81 PASS / 0 FAIL**
- 综合布线：**WNS = +8.950 ns**，0 setup/0 hold violation
- 资源：**689 LUT / 334 FF / 0 BRAM**

> 上板烧录尚未执行；本文档中的“验证”指仿真与 TD 时序/资源验证。

## 按键映射

物理键位按 `{key_row[1:0], key_col[1:0]}` 扫描，布局如下：

|            | Col0 | Col1 | Col2 | Col3 |
|------------|------|------|------|------|
| **Row0**   | 7    | 8    | 9    | /    |
| **Row1**   | 4    | 5    | 6    | *    |
| **Row2**   | 1    | 2    | 3    | -    |
| **Row3**   | 0    | =    | +    | C    |

- 行列均为低有效：扫描行输出 0，按键把对应列拉低。
- 板上丝印的键号与行列的对应关系以实际扫描顺序为准；实验 4 已确认
  `Row0/Col0 = key12`、`Row0/Col1 = key13`，可据此对照。
- 同一行多键同时按下时，列号小的优先；不同行同时按下时，行号小的优先。

支持的操作：

| 按键 | 功能 |
|---|---|
| 0~9 | 输入数字（最多 4 位有效数字，前导 0 不占位） |
| + - * / | 选择运算符；可从结果继续连续运算 |
| = | 计算并显示结果；只输入一个数时按 = 直接显示该数 |
| C | 全部清零，回到输入状态 |

## 显示规则

| 状态 | 显示 | 说明 |
|---|---|---|
| 输入 | `  12` | 右对齐，前导零消隐；单个 0 显示 `0` |
| 结果 | `  46` | 结果覆盖当前显示 |
| 负数 | `-  1` | 最左位显示 `-`，低 3 位显示绝对值 |
| 除零 | ` Err` | 除数为 0 |
| 溢出 | ` Err` | 结果 > 9999，或负数 < -999 |
| 清零 | `   0` | 按 C 后 |

说明：

- 输入上限 9999；已满 4 位后继续按数字键会被忽略。
- 乘法结果超过 9999 显示 `Err`，不做截断。
- 除法是整数除法，余数丢弃（`100 / 7 = 14`）。
- 负数只支持到 `-999`；`1000 - 9999` 会显示 `Err`（4 位显示放不下
  `-8999`）。
- `Err` 状态下按 C 清除；按数字键会直接开始新一轮输入。

## 操作示例

| 目标 | 按键序列 | 显示 |
|---|---|---|
| 12 + 34 | `1 2 + 3 4 =` | `  46` |
| 99 - 100 | `9 9 - 1 0 0 =` | `-  1` |
| 12 * 12 | `1 2 * 1 2 =` | ` 144` |
| 100 / 5 | `1 0 0 / 5 =` | `  20` |
| 除以 0 | `1 0 0 / 0 =` | ` Err` |
| 溢出 | `9 9 9 9 + 1 =` | ` Err` |
| 连续运算 | `1 2 + 3 4 = + 5 =` | `  51` |

## 模块划分

| 文件 | 作用 | 关键点 |
|---|---|---|
| `rtl/keypad_scanner.v` | 4x4 逐行扫描 | 每行 0.25 ms，整帧 1 ms；列输入两级同步 |
| `rtl/key_debounce.v` | 按键去抖 / 单次触发 | 连续 10 帧（约 10 ms）稳定才输出；释放后重新武装 |
| `rtl/calc_ctrl.v` | 输入与运算状态机 | 4 位输入、连续运算、溢出/错误状态 |
| `rtl/calc_alu.v` | 算术单元 | 加减 1 拍；乘除 14 拍移位迭代，避免宽组合乘法/除法 |
| `rtl/bin_to_bcd.v` | 二进制转 BCD | 14 拍 Double Dabble，避免组合除法/取模 |
| `rtl/seg_display.v` | 动态扫描显示 | 1 kHz 帧刷新；换位插入一拍消隐 |
| `rtl/calculator_top.v` | 顶层连线 | 参数化扫描/去抖分频，便于仿真提速 |

数据流：

```text
key_row/key_col
   -> keypad_scanner -> key_debounce -> calc_ctrl
                                          |  a/b/op
                                          v
                                       calc_alu (add/sub/mul/div)
                                          |
                                          v
                                       bin_to_bcd -> seg_display -> seg/digit_sel
```

乘除法没有使用 `/`、`*` 运算符：

- 乘法：14 次“判断最低位 + 28 位加法 + 左右移位”；
- 除法：14 次“移位 + 15 位比较/减法”恢复余数法。

50 MHz（20 ns）下最差路径为 `+8.950 ns` 余量，最小周期 11.05 ns
（约 90.5 MHz）。

## 仿真

```bash
# 直接跑
bash exp_calculator/sim/run_sim.sh

# 结构化输出（Agent 用）
python anlogic/td_flow.py sim exp_calculator/sim/run_sim.sh --json
```

TB 覆盖：

| TB | 内容 | 断言数 |
|---|---|---|
| `tb_keypad.v` | 行扫描 one-hot、帧长度、单键/多键编码 | 7 |
| `tb_debounce.v` | 3/3 帧触发、按住不重复、释放重武装、换键/毛刺 | 21 |
| `tb_bcd.v` | 0/1/9/10/20/46/144/999/1234/5000/9999 | 11 |
| `tb_alu.v` | 加减乘除、除零、加乘溢出、负数范围 | 14 |
| `tb_calculator_top.v` | 真实矩阵按键驱动的全部需求场景 + 扫描段码/消隐 | 28 |

关键断言结果：

```text
12 + 34  -> digits=0046 neg=0
99 - 100 -> digits=0001 neg=1
12 * 12  -> digits=0144
100 / 5  -> digits=0020
100 / 0  -> Err
9999 + 1 -> Err
```

TB 里的按键由真实矩阵模型驱动：只有被扫描到的行才会把对应列拉低，
不是直接给内部信号塞键值。

## 综合 / 布局布线

```bash
python anlogic/td_flow.py flow exp_calculator/flow.tcl --json
```

实测（2026-10-21）：

```text
ok=true  exit=0  duration≈30.8s
WNS = +8.950 ns   WHS = +0.464 ns   TNS = 0
Minimum period = 11.05 ns
LUT = 689  FF = 334  BRAM = 0
bit = exp_calculator/calculator_top.bit
```

`errors` 中只有一条 TD 封装库的
`ARC-6008 WARNING: Found duplicated IO adc_vref in PKG.`，属于器件库
自带提示，不影响结果。

## 引脚

与实验 4 一致（`constraints/calculator.adc`、`flow.tcl` 内联）：

| 信号 | 引脚 | 说明 |
|---|---|---|
| clk | R7 | 50 MHz |
| rst_n | A9 | 低有效；若板上 A9 与 SW0 复用，按实际复位键调整 |
| key_row[3:0] | E10, C10, F9, D9 | 行输出，低有效 |
| key_col[3:0] | F10, C11, D11, E11 | 列输入，按下为低 |
| seg[7:0] | C8, A8, B5, A7, E8, B8, A6, A4 | dp,g,f,e,d,c,b,a，高有效 |
| digit_sel[3:0] | A3, A5, B6, C9 | 低有效，[0] 最右 |

## 上板步骤

1. 确认 TD license 可用：
   ```bash
   python anlogic/td_flow.py check
   ```
2. 生成比特流：
   ```bash
   python anlogic/td_flow.py flow exp_calculator/flow.tcl --json
   ```
   要求 `timing.wns_ns >= 0`；当前为 `+8.950`。
3. 烧录（会真实写入 FPGA）：
   ```bash
   python anlogic/td_flow.py program exp_calculator/program.tcl --json
   ```
   成功标志为日志中的 `Download success`。
4. 上电/复位后数码管应显示 `0`。
5. 按 `1 2 + 3 4 =`，应显示 `46`。
6. 按 `C`，再按 `9 9 - 1 0 0 =`，应显示 `-1`（最左位为 `-`）。
7. 按 `C`，再按 `1 0 0 / 0 =`，应显示 `Err`。
8. 按 `C` 回到 `0`。

若按键行列与预期不符，优先检查：

- 矩阵键盘是行扫描还是列扫描（本设计输出在 `key_row`）；
- 列输入是否需要内部上拉（板上无外部上拉时，在 TD 里配置 PULLUP）；
- 复位键是否确实是 A9（A9 与 SW0 复用时会一直被拉低）。

## 目录

```text
exp_calculator/
├── rtl/
│   ├── calculator_top.v
│   ├── keypad_scanner.v
│   ├── key_debounce.v
│   ├── calc_ctrl.v
│   ├── calc_alu.v
│   ├── bin_to_bcd.v
│   └── seg_display.v
├── sim/
│   ├── run_sim.sh
│   ├── tb_common.vh
│   ├── tb_keypad.v
│   ├── tb_debounce.v
│   ├── tb_bcd.v
│   ├── tb_alu.v
│   └── tb_calculator_top.v
├── constraints/
│   ├── calculator.adc
│   └── calculator.sdc
├── flow.tcl
├── program.tcl
├── calculator_top.bit
├── calculator_top_timing.rpt
└── calculator_top_clock_summary.rpt
```
