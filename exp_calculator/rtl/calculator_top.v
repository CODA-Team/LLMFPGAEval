// ============================================================
// 4 位十进制计算器 —— 顶层
//
// 板卡：DMZ-EG4S20（EG4S20BG256，50 MHz）
//
// 模块划分：
//   keypad_scanner : 4x4 矩阵键盘逐行扫描
//   key_debounce   : 按键稳定采样 / 单次触发
//   calc_ctrl      : 输入与运算状态机
//   calc_alu       : 加/减/乘/除（乘除多周期）
//   bin_to_bcd     : 二进制转 4 位 BCD
//   seg_display    : 动态扫描与消隐
//
// 键盘布局（行 x 列）：
//   Row0: 7 8 9 /      Row1: 4 5 6 *
//   Row2: 1 2 3 -      Row3: 0 = + C
// ============================================================
module calculator_top #(
    parameter integer SCAN_DIV         = 12500,
    parameter integer DEBOUNCE_SAMPLES = 10,
    parameter integer DISP_SCAN_DIV    = 12500
) (
    input  wire       clk,
    input  wire       rst_n,
    output wire [3:0] key_row,
    input  wire [3:0] key_col,
    output wire [7:0] seg,
    output wire [3:0] digit_sel
);

    wire       sample_valid;
    wire [3:0] sample_code;
    wire       sample_pressed;

    wire       key_valid;
    wire [3:0] key_code;

    wire       alu_start;
    wire [1:0] alu_op;
    wire [13:0] alu_a;
    wire [13:0] alu_b;
    wire       alu_busy;
    wire       alu_done;
    wire [13:0] alu_res;
    wire       alu_neg;
    wire       alu_err;

    wire       bcd_start;
    wire [13:0] bcd_bin;
    wire       bcd_busy;
    wire       bcd_done;
    wire [15:0] bcd_digits;

    wire [15:0] disp_digits;
    wire        disp_neg;
    wire        disp_err;

    keypad_scanner #(
        .SCAN_DIV (SCAN_DIV)
    ) u_scan (
        .clk            (clk),
        .rst_n          (rst_n),
        .key_row        (key_row),
        .key_col        (key_col),
        .sample_valid   (sample_valid),
        .sample_code    (sample_code),
        .sample_pressed (sample_pressed)
    );

    key_debounce #(
        .SAMPLES (DEBOUNCE_SAMPLES)
    ) u_deb (
        .clk            (clk),
        .rst_n          (rst_n),
        .sample_valid   (sample_valid),
        .sample_code    (sample_code),
        .sample_pressed (sample_pressed),
        .key_valid      (key_valid),
        .key_code       (key_code)
    );

    calc_ctrl u_ctrl (
        .clk        (clk),
        .rst_n      (rst_n),
        .key_valid  (key_valid),
        .key_code   (key_code),
        .alu_start  (alu_start),
        .alu_op     (alu_op),
        .alu_a      (alu_a),
        .alu_b      (alu_b),
        .alu_busy   (alu_busy),
        .alu_done   (alu_done),
        .alu_res    (alu_res),
        .alu_neg    (alu_neg),
        .alu_err    (alu_err),
        .bcd_start  (bcd_start),
        .bcd_bin    (bcd_bin),
        .bcd_done   (bcd_done),
        .bcd_digits (bcd_digits),
        .digits     (disp_digits),
        .disp_neg   (disp_neg),
        .disp_err   (disp_err)
    );

    calc_alu u_alu (
        .clk   (clk),
        .rst_n (rst_n),
        .start (alu_start),
        .op    (alu_op),
        .a     (alu_a),
        .b     (alu_b),
        .busy  (alu_busy),
        .done  (alu_done),
        .res   (alu_res),
        .neg   (alu_neg),
        .err   (alu_err)
    );

    bin_to_bcd u_bcd (
        .clk   (clk),
        .rst_n (rst_n),
        .start (bcd_start),
        .bin   (bcd_bin),
        .busy  (bcd_busy),
        .done  (bcd_done),
        .bcd   (bcd_digits)
    );

    seg_display #(
        .SCAN_DIV (DISP_SCAN_DIV)
    ) u_disp (
        .clk      (clk),
        .rst_n    (rst_n),
        .digits   (disp_digits),
        .show_neg (disp_neg),
        .show_err (disp_err),
        .seg      (seg),
        .digit_sel(digit_sel)
    );

endmodule
