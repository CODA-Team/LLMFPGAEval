#!/usr/bin/env bash
# ============================================================
# 实验：4 位十进制计算器 仿真脚本
#
# 用法（Git Bash / WSL，从仓库根目录）：
#   bash board/exp_calculator/sim/run_sim.sh
#
# 可用环境变量覆盖工具路径：
#   IVERILOG=/path/to/iverilog VVP=/path/to/vvp bash ...
# ============================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$ROOT_DIR"

IVERILOG="${IVERILOG:-C:/iverilog/bin/iverilog.exe}"
VVP="${VVP:-C:/iverilog/bin/vvp.exe}"

if [ ! -x "$IVERILOG" ] && ! command -v "$IVERILOG" >/dev/null 2>&1; then
    echo "ERROR: iverilog not found: $IVERILOG"
    exit 1
fi
if [ ! -x "$VVP" ] && ! command -v "$VVP" >/dev/null 2>&1; then
    echo "ERROR: vvp not found: $VVP"
    exit 1
fi

mkdir -p sim/build

run_one() {
    local name="$1"
    local tb="$2"
    echo "============================================================"
    echo "  $name"
    echo "============================================================"
    "$IVERILOG" -g2012 -I sim -o "sim/build/$name.vvp" \
        rtl/*.v "$tb" || return 1
    "$VVP" "sim/build/$name.vvp" 2>&1 | tee "sim/build/$name.log"
    local rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
        echo "FAILED: $name (exit $rc)"
        return "$rc"
    fi
    echo "PASSED: $name"
    echo
}

run_one keypad     sim/tb_keypad.v
rc_keypad=$?
run_one debounce   sim/tb_debounce.v
rc_debounce=$?
run_one bcd        sim/tb_bcd.v
rc_bcd=$?
run_one alu        sim/tb_alu.v
rc_alu=$?
run_one top        sim/tb_calculator_top.v
rc_top=$?

echo "============================================================"
if [ "$rc_keypad" -eq 0 ] && [ "$rc_debounce" -eq 0 ] && \
   [ "$rc_bcd" -eq 0 ] && [ "$rc_alu" -eq 0 ] && \
   [ "$rc_top" -eq 0 ]; then
    echo "ALL SIMULATIONS PASSED"
    exit 0
else
    echo "SIMULATION FAILED (keypad=$rc_keypad debounce=$rc_debounce bcd=$rc_bcd alu=$rc_alu top=$rc_top)"
    exit 1
fi
