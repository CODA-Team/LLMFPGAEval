# ============================================================
# 安路 TD 4.6 HDL2Bit 通用 Tcl 模板
#
# 旧版模板里的 project -new / set_device / synth /
# place_route 在 TD 4.6 中并不存在，已按实测命令重写。
#
# 运行：
#   D:\strange\bin\td_commands_prompt.exe create_project.tcl
#   或 python anlogic/td_flow.py flow anlogic/create_project.tcl
#
# 前置：
#   1. license 位于 <TD 安装目录>/license/Anlogic.lic
#   2. 路径尽量不含空格/中文
# ============================================================

# ---------------- 参数区 ----------------
set TOP       "top"
set RTL_FILE  "rtl/top.v"
set SDC_FILE  "constraint/top.sdc"
set BIT_FILE  "runs/top.bit"
set DEVICE_DB "eagle_s20.db"     ;# EG4S20BG256
set PACKAGE   "BG256"
set CABLE     0                   ;# 仅烧录时使用

set BASE_DIR [file normalize [file dirname [info script]]]
set RTL_FILE [file normalize [file join $BASE_DIR $RTL_FILE]]
set SDC_FILE [file normalize [file join $BASE_DIR $SDC_FILE]]
set BIT_FILE [file normalize [file join $BASE_DIR $BIT_FILE]]

# ---------------- 1. 器件库 ----------------
import_device $DEVICE_DB -package $PACKAGE

# ---------------- 2. 读入 RTL ----------------
read_verilog -file $RTL_FILE -top $TOP -verbose 1

# ---------------- 3. 引脚约束 ----------------
# TD 4.6 的 .adc 文件格式随版本有差异，命令行流程建议直接写
# set_pin_assignment，或使用 GUI 导出的 Tcl。示例：
# set_pin_assignment { clk } { LOCATION = R7; IOSTANDARD = LVCMOS33; }
# set_pin_assignment { led[0] } { LOCATION = B14; IOSTANDARD = LVCMOS33; }
#
# 如果你的 .adc 文件是标准格式，也可以尝试：
# read_adc $ADC_FILE

# ---------------- 4. HDL2Bit ----------------
# SDC 要求先有 netlist，因此放在 optimize_rtl 之后。
optimize_rtl
if {[file exists $SDC_FILE]} {
    read_sdc $SDC_FILE
}
optimize_gate
# 布局布线必须用 place / route；optimize_place / optimize_route
# 在 TD 4.6 中是空操作，会导致 bitgen 段错误。
place
route

# ---------------- 5. 比特流 ----------------
file mkdir [file dirname $BIT_FILE]
bitgen -bit $BIT_FILE
puts "FLOW FINISHED: $BIT_FILE"

# ---------------- 6. 可选：立即烧录 ----------------
# download -bit $BIT_FILE -cable $CABLE
