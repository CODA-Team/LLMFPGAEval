# ============================================================
# 实验4：烧录 reaction_timer_board.bit 到 FPGA SRAM（掉电丢失）
#
# 运行（从仓库根目录）：
#   python anlogic/td_flow.py program board/exp4_reaction_timer/program.tcl
#
# 成功标志：日志出现 "Download success"
# ============================================================

set SCRIPT_DIR [file normalize [file dirname [info script]]]
set BIT_FILE   [file join $SCRIPT_DIR reaction_timer_board.bit]
set CABLE      0

if {![file exists $BIT_FILE]} {
    puts "ERROR: bit file not found: $BIT_FILE"
    exit 1
}

puts "DOWNLOAD: $BIT_FILE (cable $CABLE)"
download -bit $BIT_FILE -cable $CABLE
puts "program Finished"
