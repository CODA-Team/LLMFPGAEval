#!/usr/bin/env bash
# ============================================================
# 实验4 仿真脚本
#
# 用法（Git Bash / WSL）：
#   bash board/exp4_reaction_timer/sim/run_sim.sh
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
        rtl/reaction_timer_board.v "$tb" || return 1
    "$VVP" "sim/build/$name.vvp" 2>&1 | tee "sim/build/$name.log"
    local rc=${PIPESTATUS[0]}
    if [ "$rc" -ne 0 ]; then
        echo "FAILED: $name (exit $rc)"
        return "$rc"
    fi
    echo "PASSED: $name"
    echo
}

run_one top         sim/tb_reaction_timer_top.v
rc_top=$?
run_one board       sim/tb_reaction_timer_board.v
rc_board=$?
run_one delay_width sim/tb_delay_width.v
rc_width=$?
run_one bcd         sim/tb_bcd.v
rc_bcd=$?
run_one max_ms      sim/tb_max_ms.v
rc_maxms=$?
run_one key_scan    sim/tb_key_scan_div.v
rc_keyscan=$?
run_one debounce    sim/tb_debounce_real.v
rc_debounce=$?

echo "============================================================"
if [ "$rc_top" -eq 0 ] && [ "$rc_board" -eq 0 ] && \
   [ "$rc_width" -eq 0 ] && [ "$rc_bcd" -eq 0 ] && \
   [ "$rc_maxms" -eq 0 ] && [ "$rc_keyscan" -eq 0 ] && \
   [ "$rc_debounce" -eq 0 ]; then
    echo "ALL SIMULATIONS PASSED"
    exit 0
else
    echo "SIMULATION FAILED (top=$rc_top board=$rc_board width=$rc_width bcd=$rc_bcd maxms=$rc_maxms keyscan=$rc_keyscan debounce=$rc_debounce)"
    exit 1
fi
