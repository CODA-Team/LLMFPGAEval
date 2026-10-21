# ============================================================
# 实验4：reaction_timer_board TD 4.6 HDL2Bit 流程
#
# 运行（从仓库根目录）：
#   python anlogic/td_flow.py flow board/exp4_reaction_timer/flow.tcl
#
# 前置：
#   - TD license 可用
#   - reaction_timer_board.adc 的引脚已核对
#
# 注意：TD 4.6 的布局布线命令是 place / route；
#       optimize_place / optimize_route 是空操作。
# ============================================================

set TOP        "reaction_timer_board"
set DEVICE_DB  "eagle_s20.db"
set PACKAGE    "BG256"

# 1) 器件库
import_device $DEVICE_DB -package $PACKAGE

# 2) RTL（本文件已包含全部子模块、键盘扫描和板级封装）
read_verilog -file rtl/reaction_timer_board.v -top $TOP -verbose 1

# 3) 引脚约束
#    与 reaction_timer_board.adc 保持一致。
#    这里直接写 set_pin_assignment，避免不同 TD 版本 read_adc 的差异。
set_pin_assignment { clk }   { LOCATION = R7; IOSTANDARD = LVCMOS33; }
set_pin_assignment { rst_n } { LOCATION = A9; IOSTANDARD = LVCMOS33; }

set_pin_assignment { key_row[0] } { LOCATION = D9;  IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_row[1] } { LOCATION = F9;  IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_row[2] } { LOCATION = C10; IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_row[3] } { LOCATION = E10; IOSTANDARD = LVCMOS33; }

set_pin_assignment { key_col[0] } { LOCATION = E11; IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_col[1] } { LOCATION = D11; IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_col[2] } { LOCATION = C11; IOSTANDARD = LVCMOS33; }
set_pin_assignment { key_col[3] } { LOCATION = F10; IOSTANDARD = LVCMOS33; }

set_pin_assignment { ready_led } { LOCATION = B14; IOSTANDARD = LVCMOS33; }

set_pin_assignment { seg[0] } { LOCATION = A4; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[1] } { LOCATION = A6; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[2] } { LOCATION = B8; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[3] } { LOCATION = E8; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[4] } { LOCATION = A7; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[5] } { LOCATION = B5; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[6] } { LOCATION = A8; IOSTANDARD = LVCMOS33; }
set_pin_assignment { seg[7] } { LOCATION = C8; IOSTANDARD = LVCMOS33; }

set_pin_assignment { digit_sel[0] } { LOCATION = C9; IOSTANDARD = LVCMOS33; }
set_pin_assignment { digit_sel[1] } { LOCATION = B6; IOSTANDARD = LVCMOS33; }
set_pin_assignment { digit_sel[2] } { LOCATION = A5; IOSTANDARD = LVCMOS33; }
set_pin_assignment { digit_sel[3] } { LOCATION = A3; IOSTANDARD = LVCMOS33; }

# 4) HDL2Bit
optimize_rtl
read_sdc reaction_timer_board.sdc
optimize_gate
place
route

# 5) 时序报告（route 之后才有真实延迟）
report_clock_summary -file reaction_timer_board_clock_summary.rpt
report_timing -file reaction_timer_board_timing.rpt
catch {
    puts "TIMING WNS/WHS: [get_timing_stat -wns -whs]"
}

# 6) 比特流
bitgen -bit reaction_timer_board.bit

puts "FLOW FINISHED: reaction_timer_board.bit"
