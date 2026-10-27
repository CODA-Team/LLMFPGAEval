# ============================================================
# TD 4.6 烧录脚本（下载到 FPGA SRAM，掉电丢失）
#
# 用法：
#   D:\strange\bin\td_commands_prompt.exe program.tcl
# 或由 Python 调用：
#   python anlogic/td_flow.py program anlogic/program.tcl
#
# 指定 bit 文件（可选）：
#   设置环境变量 BIT_FILE 后再运行，例如：
#   set BIT_FILE=C:\path\to\design.bit
#
# 注意：TD 4.6 没有 jtag_configure 命令，也没有 download -file；
#      正确的写法是 download -bit <文件> -cable <序号>。
# 成功标志：日志里出现 "Download success"
# ============================================================

set SCRIPT_DIR [file normalize [file dirname [info script]]]

if {[info exists env(BIT_FILE)] && $env(BIT_FILE) ne ""} {
    set BIT_FILE [file normalize $env(BIT_FILE)]
} else {
    # 默认指向 LED bring-up 工程
    set BIT_FILE [file normalize [file join $SCRIPT_DIR .. board bringup_led led_blink.bit]]
}
set CABLE 0

if {![file exists $BIT_FILE]} {
    puts "ERROR: bit file not found: $BIT_FILE"
    exit 1
}

puts "DOWNLOAD: $BIT_FILE (cable $CABLE)"
download -bit $BIT_FILE -cable $CABLE
puts "program Finished"
