// ============================================================
// 计算器整机测试（通过 4x4 矩阵键盘真实按键序列驱动）
//
// 覆盖：
//   12 + 34 = 46
//   99 - 100 = -1
//   12 * 12 = 144
//   100 / 5 = 20
//   100 / 0 = Err
//   9999 + 1 = Err
//   输入前导零消隐 / 第 5 位数字忽略 / C 清零
//   动态扫描段码与消隐
//
// 为加快仿真，SCAN_DIV / 去抖帧数 / 显示扫描都调小；
// 默认参数（12500 / 10 / 12500）留给上板。
// ============================================================
`timescale 1ns/1ps

module tb_calculator_top;

    reg        clk;
    reg        rst_n;
    reg  [15:0] pressed;      // 16 个物理键的按下状态
    reg  [3:0]  key_col_r;
    wire [3:0]  key_col;
    wire [3:0] key_row;
    wire [7:0] seg;
    wire [3:0] digit_sel;

    localparam integer SCAN = 8;
    localparam integer DEB  = 3;
    localparam integer DISP = 8;

    `include "tb_common.vh"

    calculator_top #(
        .SCAN_DIV         (SCAN),
        .DEBOUNCE_SAMPLES (DEB),
        .DISP_SCAN_DIV    (DISP)
    ) dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .key_row   (key_row),
        .key_col   (key_col),
        .seg       (seg),
        .digit_sel (digit_sel)
    );

    integer i;

    // ---------------- 键盘矩阵模型 ----------------
    // 按键只在被扫描到的那一行把对应列拉低，和真实 4x4 矩阵一致。
    always @* begin
        key_col_r = 4'hF;
        for (i = 0; i < 16; i = i + 1) begin
            if (pressed[i] && key_row[i / 4] === 1'b0) begin
                key_col_r[i % 4] = 1'b0;
            end
        end
    end
    assign key_col = key_col_r;

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    // ---------------- 键盘驱动 ----------------
    task press;
        input integer r;
        input integer c;
        integer code;
        begin
            code = r * 4 + c;
            pressed[code] = 1'b1;
            repeat ((DEB + 2) * 4 * SCAN) @(posedge clk);
            pressed[code] = 1'b0;
            repeat ((DEB + 2) * 4 * SCAN) @(posedge clk);
        end
    endtask

    // fn: 0..9 数字, 10 +, 11 -, 12 *, 13 /, 14 =, 15 C
    task press_key;
        input [3:0] fn;
        begin
            case (fn)
                4'd0:  press(3, 0);
                4'd1:  press(2, 0);
                4'd2:  press(2, 1);
                4'd3:  press(2, 2);
                4'd4:  press(1, 0);
                4'd5:  press(1, 1);
                4'd6:  press(1, 2);
                4'd7:  press(0, 0);
                4'd8:  press(0, 1);
                4'd9:  press(0, 2);
                4'd10: press(3, 2);   // +
                4'd11: press(2, 3);   // -
                4'd12: press(1, 3);   // *
                4'd13: press(0, 3);   // /
                4'd14: press(3, 1);   // =
                default: press(3, 3); // C
            endcase
        end
    endtask

    // ---------------- 结果检查 ----------------
    task expect_result;
        input [255:0] name;
        input [15:0]  exp_digits;
        input         exp_neg;
        begin
            wait (dut.u_ctrl.state === 3'd3);
            repeat (40) @(posedge clk);

            if (dut.u_ctrl.digits !== exp_digits) begin
                $display("ERROR: %0s digits expected=%h actual=%h",
                         name, exp_digits, dut.u_ctrl.digits);
                $fatal(1, "TEST_FAILED");
            end
            if (dut.u_ctrl.disp_neg !== exp_neg) begin
                $display("ERROR: %0s neg expected=%b actual=%b",
                         name, exp_neg, dut.u_ctrl.disp_neg);
                $fatal(1, "TEST_FAILED");
            end
            if (dut.u_ctrl.disp_err !== 1'b0) begin
                $display("ERROR: %0s unexpected error flag", name);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: %0s digits=%h neg=%b", name,
                     dut.u_ctrl.digits, dut.u_ctrl.disp_neg);
        end
    endtask

    task expect_error;
        input [255:0] name;
        begin
            wait (dut.u_ctrl.state === 3'd4);
            repeat (10) @(posedge clk);
            if (dut.u_ctrl.disp_err !== 1'b1) begin
                $display("ERROR: %0s disp_err not set", name);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: %0s -> Err", name);
        end
    endtask

    task check_input;
        input [255:0] name;
        input [15:0]  exp_digits;
        begin
            repeat (20) @(posedge clk);
            if (dut.u_ctrl.state !== 3'd0) begin
                $display("ERROR: %0s not in INPUT_A, state=%0d",
                         name, dut.u_ctrl.state);
                $fatal(1, "TEST_FAILED");
            end
            if (dut.u_ctrl.digits !== exp_digits ||
                dut.u_ctrl.disp_neg !== 1'b0 ||
                dut.u_ctrl.disp_err !== 1'b0) begin
                $display("ERROR: %0s expected digits=%h, actual digits=%h neg=%b err=%b",
                         name, exp_digits, dut.u_ctrl.digits,
                         dut.u_ctrl.disp_neg, dut.u_ctrl.disp_err);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: %0s input shows %h", name, dut.u_ctrl.digits);
        end
    endtask

    // ---------------- 显示参考模型 ----------------
    function [7:0] expected_seg;
        input integer   idx;
        input [15:0]    v;
        input           neg;
        input           err;
        reg [3:0] d;
        reg       blank;
        begin
            d = 4'h0;
            blank = 1'b0;

            if (err) begin
                case (idx)
                    0: d = 4'hC;              // r
                    1: d = 4'hC;              // r
                    2: d = 4'hB;              // E
                    default: begin d = 4'h0; blank = 1'b1; end
                endcase
            end else if (neg) begin
                if (idx == 3) begin
                    d = 4'hA;                 // -
                end else begin
                    case (idx)
                        0: d = v[3:0];
                        1: d = v[7:4];
                        default: d = v[11:8];
                    endcase
                    if (v[11:8] != 4'd0)      blank = 1'b0;
                    else if (v[7:4] != 4'd0)  blank = (idx == 2);
                    else                      blank = (idx == 2) || (idx == 1);
                end
            end else begin
                case (idx)
                    0: d = v[3:0];
                    1: d = v[7:4];
                    2: d = v[11:8];
                    default: d = v[15:12];
                endcase
                if (v[15:12] != 4'd0)      blank = 1'b0;
                else if (v[11:8] != 4'd0)  blank = (idx == 3);
                else if (v[7:4] != 4'd0)   blank = (idx == 3) || (idx == 2);
                else                       blank = (idx != 0);
            end

            expected_seg = blank ? 8'h00 : {1'b0, ref_seg(d)};
        end
    endfunction

    task check_frame;
        input [255:0] name;
        input [15:0]  v;
        input         neg;
        input         err;
        integer k;
        integer idx;
        reg [3:0] seen;
        reg [3:0] good;
        reg [7:0] exp;
        reg bad;
        begin
            seen = 4'b0000;
            good = 4'b0000;
            bad  = 1'b0;

            for (k = 0; k < 4 * DISP + 32; k = k + 1) begin
                @(negedge clk);

                case (digit_sel)
                    4'b1110: idx = 0;
                    4'b1101: idx = 1;
                    4'b1011: idx = 2;
                    4'b0111: idx = 3;
                    default: begin
                        $display("ERROR: %0s digit_sel=%b not one-hot-low",
                                 name, digit_sel);
                        bad = 1'b1;
                        idx = 0;
                    end
                endcase

                if (!bad) begin
                    seen[idx] = 1'b1;
                    exp = expected_seg(idx, v, neg, err);

                    if (seg[7] !== 1'b0) bad = 1'b1;
                    if (seg !== 8'h00 && seg !== exp) bad = 1'b1;
                    if (seg === exp) good[idx] = 1'b1;
                end
            end

            if (bad) begin
                $display("ERROR: %0s segment mismatch/glitch", name);
                $fatal(1, "TEST_FAILED");
            end
            if (seen !== 4'b1111 || good !== 4'b1111) begin
                $display("ERROR: %0s scan incomplete seen=%b good=%b",
                         name, seen, good);
                $fatal(1, "TEST_FAILED");
            end

            $display("PASS: %0s display scan", name);
        end
    endtask

    // ---------------- 主流程 ----------------
    initial begin
        rst_n   = 1'b0;
        pressed = 16'h0000;
        repeat (5) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        repeat (50) @(posedge clk);
        check_frame("reset 0", 16'h0000, 1'b0, 1'b0);

        // 12 + 34 = 46
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd10);
        press_key(4'd3);
        press_key(4'd4);
        press_key(4'd14);
        expect_result("12 + 34", 16'h0046, 1'b0);
        check_frame("12 + 34", 16'h0046, 1'b0, 1'b0);

        // 99 - 100 = -1
        press_key(4'd15);
        press_key(4'd9);
        press_key(4'd9);
        press_key(4'd11);
        press_key(4'd1);
        press_key(4'd0);
        press_key(4'd0);
        press_key(4'd14);
        expect_result("99 - 100", 16'h0001, 1'b1);
        check_frame("99 - 100", 16'h0001, 1'b1, 1'b0);

        // 12 * 12 = 144
        press_key(4'd15);
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd12);
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd14);
        expect_result("12 * 12", 16'h0144, 1'b0);
        check_frame("12 * 12", 16'h0144, 1'b0, 1'b0);

        // 100 / 5 = 20
        press_key(4'd15);
        press_key(4'd1);
        press_key(4'd0);
        press_key(4'd0);
        press_key(4'd13);
        press_key(4'd5);
        press_key(4'd14);
        expect_result("100 / 5", 16'h0020, 1'b0);
        check_frame("100 / 5", 16'h0020, 1'b0, 1'b0);

        // 100 / 0 = Err
        press_key(4'd15);
        press_key(4'd1);
        press_key(4'd0);
        press_key(4'd0);
        press_key(4'd13);
        press_key(4'd0);
        press_key(4'd14);
        expect_error("100 / 0");
        check_frame("100 / 0", 16'h0000, 1'b0, 1'b1);

        // 9999 + 1 = Err
        press_key(4'd15);
        press_key(4'd9);
        press_key(4'd9);
        press_key(4'd9);
        press_key(4'd9);
        press_key(4'd10);
        press_key(4'd1);
        press_key(4'd14);
        expect_error("9999 + 1");
        check_frame("9999 + 1", 16'h0000, 1'b0, 1'b1);

        // C 清零
        press_key(4'd15);
        check_input("C clears", 16'h0000);
        check_frame("C clears", 16'h0000, 1'b0, 1'b0);

        // 输入显示：0 / 1 / 12，前导零消隐
        press_key(4'd0);
        check_input("input 0", 16'h0000);
        check_frame("input 0", 16'h0000, 1'b0, 1'b0);

        press_key(4'd1);
        check_input("input 01", 16'h0001);
        check_frame("input 01", 16'h0001, 1'b0, 1'b0);

        press_key(4'd2);
        check_input("input 012", 16'h0012);
        check_frame("input 012", 16'h0012, 1'b0, 1'b0);

        // 第 5 位数字应被忽略：1234 5 -> 1234
        press_key(4'd15);
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd3);
        press_key(4'd4);
        press_key(4'd5);
        check_input("5th digit ignored", 16'h1234);
        check_frame("5th digit ignored", 16'h1234, 1'b0, 1'b0);

        // 前导零不占用输入位数：0 1 2 3 4 -> 1234
        press_key(4'd15);
        press_key(4'd0);
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd3);
        press_key(4'd4);
        check_input("leading zero does not consume digit", 16'h1234);

        // 连续运算：12 + 34 = 46，再 + 5 = 51
        press_key(4'd15);
        press_key(4'd1);
        press_key(4'd2);
        press_key(4'd10);
        press_key(4'd3);
        press_key(4'd4);
        press_key(4'd14);
        expect_result("chain 12 + 34", 16'h0046, 1'b0);
        press_key(4'd10);
        press_key(4'd5);
        press_key(4'd14);
        expect_result("chain 46 + 5", 16'h0051, 1'b0);

        // 单个数字后直接按等号
        press_key(4'd15);
        press_key(4'd7);
        press_key(4'd14);
        expect_result("7 = 7", 16'h0007, 1'b0);
        check_frame("7 = 7", 16'h0007, 1'b0, 1'b0);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #5000000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
