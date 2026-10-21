# ============================================================
# 4 位计算器：烧录 calculator_top.bit 到 FPGA SRAM（掉电丢失）
#
# 运行（从仓库根目录）：
#   python anlogic/td_flow.py program board/exp_calculator/program.tcl
#
# 成功标志：日志出现 "Download success"
# ============================================================

set SCRIPT_DIR [file normalize [file dirname [info script]]]
set BIT_FILE   [file join $SCRIPT_DIR calculator_top.bit]
set CABLE      0

if {![file exists $BIT_FILE]} {
    puts "ERROR: bit file not found: $BIT_FILE"
    exit 1
}

puts "DOWNLOAD: $BIT_FILE (cable $CABLE)"
download -bit $BIT_FILE -cable $CABLE
puts "program Finished"
