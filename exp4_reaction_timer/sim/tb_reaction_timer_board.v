// ============================================================
// 实验4：reaction_timer_board 板级 Testbench
//
// 覆盖板级封装引入的硬件相关逻辑：
//   - rst_n 低有效复位 + 同步释放
//   - 4x4 矩阵键盘扫描：行线 one-hot-low 轮转
//   - Row0/Col0 = start，Row0/Col1 = response；未映射键无效
//   - 按键低有效（按下列线为低）
//   - 结果经由封装层正常显示
//
// 矩阵键盘模型：板上列线外部上拉，某键按下时，只有对应行被拉低
// 的那个行槽里列线为低。
// ============================================================

`timescale 1ns/1ps

module tb_reaction_timer_board;

    localparam CLK_FREQ     = 1000;
    localparam DEBOUNCE_CNT = 5;
    localparam MIN_DELAY    = 1;
    localparam MAX_DELAY    = 2;
    localparam MS_DIV       = 10;
    localparam MAX_MS       = 200;
    localparam FAIL_TIME    = 1;
    localparam SCAN_DIV     = 4;
    localparam KEY_SCAN_DIV = 4;
    localparam CLK_PERIOD   = 10;

    reg         clk;
    reg         rst_n;
    wire [3:0]  key_row;
    reg  [3:0]  key_col;
    wire        ready_led;
    wire [7:0]  seg;
    wire [3:0]  digit_sel;

    // 按键模型
    reg start_pressed;
    reg response_pressed;
    reg unmapped_pressed;

    // 外部上拉 + 按下键所在行拉低时列线为低
    always @(*) begin
        key_col = 4'b1111;
        if (start_pressed    && !key_row[0]) key_col[0] = 1'b0;
        if (response_pressed && !key_row[0]) key_col[1] = 1'b0;
        if (unmapped_pressed && !key_row[1]) key_col[0] = 1'b0;
    end

    reaction_timer_board #(
        .CLK_FREQ     (CLK_FREQ),
        .DEBOUNCE_CNT (DEBOUNCE_CNT),
        .MIN_DELAY    (MIN_DELAY),
        .MAX_DELAY    (MAX_DELAY),
        .MS_DIV       (MS_DIV),
        .MAX_MS       (MAX_MS),
        .FAIL_TIME    (FAIL_TIME),
        .SCAN_DIV     (SCAN_DIV),
        .KEY_SCAN_DIV (KEY_SCAN_DIV)
    ) dut (
        .clk       (clk),
        .rst_n     (rst_n),
        .key_row   (key_row),
        .key_col   (key_col),
        .ready_led (ready_led),
        .seg       (seg),
        .digit_sel (digit_sel)
    );

    `include "tb_common.vh"

    // ---------------- 时钟 ----------------
    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD/2) clk = ~clk;
    end

    // ---------------- 看门狗 ----------------
    initial begin
        #5000000;
        $display("ERROR: TIMEOUT");
        $fatal(1, "TEST_FAILED");
    end

    // ---------------- 任务 ----------------
    task do_reset;
        begin
            @(negedge clk);
            rst_n            = 1'b0;
            start_pressed    = 1'b0;
            response_pressed = 1'b0;
            unmapped_pressed = 1'b0;
            repeat (5) @(posedge clk);
            @(negedge clk);
            rst_n = 1'b1;
            repeat (4) @(posedge clk);
        end
    endtask

    task wait_ready;
        input integer timeout;
        integer n;
        begin
            n = 0;
            while (ready_led !== 1'b1 && n < timeout) begin
                @(posedge clk);
                n = n + 1;
            end
            if (ready_led !== 1'b1) begin
                $display("ERROR: ready_led timeout after %0d cycles", timeout);
                $fatal(1, "TEST_FAILED");
            end
        end
    endtask

    task wait_state;
        input [2:0] target;
        input integer timeout;
        integer n;
        begin
            n = 0;
            while (dut.u_dut.u_reaction_fsm.state !== target && n < timeout) begin
                @(posedge clk);
                n = n + 1;
            end
            if (dut.u_dut.u_reaction_fsm.state !== target) begin
                $display("ERROR: state timeout, target=%b actual=%b",
                         target, dut.u_dut.u_reaction_fsm.state);
                $fatal(1, "TEST_FAILED");
            end
        end
    endtask

    task check_key_scan;
        input integer cycles;
        integer k;
        reg [3:0] seen;
        begin
            seen = 4'b0000;
            for (k = 0; k < cycles; k = k + 1) begin
                @(negedge clk);
                case (key_row)
                    4'b1110: seen[0] = 1'b1;
                    4'b1101: seen[1] = 1'b1;
                    4'b1011: seen[2] = 1'b1;
                    4'b0111: seen[3] = 1'b1;
                    default: begin
                        $display("ERROR: key_row not one-hot-low: %b", key_row);
                        $fatal(1, "TEST_FAILED");
                    end
                endcase
            end
            if (seen !== 4'b1111) begin
                $display("ERROR: key scan did not visit all rows, seen=%b", seen);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: key_scan rows");
        end
    endtask

    // ---------------- 主流程 ----------------
    integer i;
    integer timeout;

    initial begin
        rst_n            = 1'b0;
        start_pressed    = 1'b0;
        response_pressed = 1'b0;
        unmapped_pressed = 1'b0;

        // =========================================================
        // T0: 低有效复位 + 扫描启动
        // =========================================================
        do_reset;
        check1("T0_ready_low_after_reset", 1'b0, ready_led);
        check16("T0_display_zero", 16'h0000, dut.u_dut.display_value);
        check_key_scan(4*KEY_SCAN_DIV + 8);
        check_display_scan(16'h0000, 4*SCAN_DIV + 8);

        // =========================================================
        // T1: 未映射键（Row1/Col0）无效
        // =========================================================
        unmapped_pressed = 1'b1;
        repeat (150) @(posedge clk);
        check1("T1_unmapped_key_no_start", 1'b0, ready_led);
        if (dut.u_dut.u_reaction_fsm.state !== 3'b000) begin
            $display("ERROR: unmapped key changed state: %b",
                     dut.u_dut.u_reaction_fsm.state);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T1_unmapped_key_ignored");
        unmapped_pressed = 1'b0;
        repeat (30) @(posedge clk);

        // =========================================================
        // T2: Row0/Col0 启动 -> ready
        // =========================================================
        start_pressed = 1'b1;
        wait_ready(MAX_DELAY*CLK_FREQ + 500);
        check1("T2_matrix_start_ready", 1'b1, ready_led);
        check16("T2_wait_display_zero", 16'h0000, dut.u_dut.display_value);
        start_pressed = 1'b0;
        repeat (40) @(posedge clk);

        // =========================================================
        // T3: Row0/Col1 响应 -> 结果 BCD
        // =========================================================
        repeat (5*MS_DIV) @(posedge clk);
        response_pressed = 1'b1;
        wait_state(3'b011, 500);   // DISPLAY_RESULT
        @(posedge clk);
        response_pressed = 1'b0;
        // BCD 顺序转换需要 ~15 拍
        repeat (30) @(posedge clk);

        check1("T3_ready_low_after_response", 1'b0, ready_led);
        if (dut.u_dut.u_reaction_fsm.result_latch == 0) begin
            $display("ERROR: measured result is 0");
            $fatal(1, "TEST_FAILED");
        end
        if (dut.u_dut.u_reaction_fsm.result_latch > 30) begin
            $display("ERROR: measured result too large: %0d",
                     dut.u_dut.u_reaction_fsm.result_latch);
            $fatal(1, "TEST_FAILED");
        end
        check16("T3_display_is_bcd",
                ref_bcd(dut.u_dut.u_reaction_fsm.result_latch),
                dut.u_dut.display_value);
        check_display_scan(dut.u_dut.display_value, 4*SCAN_DIV + 8);
        $display("PASS: T3_measured_ms=%0d",
                 dut.u_dut.u_reaction_fsm.result_latch);

        // =========================================================
        // T4: 结果态重新启动 -> 清屏
        // =========================================================
        start_pressed = 1'b1;
        wait_state(3'b001, 500);   // WAIT_DELAY
        @(posedge clk);
        check16("T4_restart_clears_display", 16'h0000, dut.u_dut.display_value);
        start_pressed = 1'b0;
        wait_ready(MAX_DELAY*CLK_FREQ + 500);
        check1("T4_ready_again", 1'b1, ready_led);
        response_pressed = 1'b1;
        wait_state(3'b011, 500);
        @(posedge clk);
        response_pressed = 1'b0;
        repeat (30) @(posedge clk);
        check16("T4_display_is_bcd_of_new_result",
                ref_bcd(dut.u_dut.u_reaction_fsm.result_latch),
                dut.u_dut.display_value);

        // =========================================================
        // T5: WAIT_DELAY 中按响应 -> FAIL
        // =========================================================
        do_reset;
        start_pressed = 1'b1;
        wait_state(3'b001, 500);   // WAIT_DELAY
        start_pressed = 1'b0;
        response_pressed = 1'b1;
        wait_state(3'b100, 500);   // FALSE_START
        check16("T5_false_start_display_FAIL", 16'hFFFF, dut.u_dut.display_value);
        check1("T5_false_start_ready_low", 1'b0, ready_led);
        check_display_scan(16'hFFFF, 4*SCAN_DIV + 8);
        response_pressed = 1'b0;

        timeout = FAIL_TIME*CLK_FREQ + 300;
        wait_state(3'b000, timeout);
        @(posedge clk);
        check16("T5_false_start_clears_display", 16'h0000, dut.u_dut.display_value);

        // =========================================================
        // T6: READY 中拉低 rst_n -> 异步复位
        // =========================================================
        do_reset;
        start_pressed = 1'b1;
        wait_ready(MAX_DELAY*CLK_FREQ + 500);
        start_pressed = 1'b0;

        @(negedge clk);
        rst_n = 1'b0;
        repeat (3) @(posedge clk);
        check1("T6_reset_ready_low", 1'b0, ready_led);
        check16("T6_reset_display_zero", 16'h0000, dut.u_dut.display_value);
        @(negedge clk);
        rst_n = 1'b1;
        repeat (4) @(posedge clk);
        check1("T6_recovery_ready_low", 1'b0, ready_led);
        check16("T6_recovery_display_zero", 16'h0000, dut.u_dut.display_value);
        if (dut.u_dut.u_reaction_fsm.state !== 3'b000) begin
            $display("ERROR: not IDLE after reset: %b",
                     dut.u_dut.u_reaction_fsm.state);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T6_reset_recovery");

        // =========================================================
        // T6b: 同一行 start + response 同时按下 -> start 优先
        //   IDLE 只检查 start_pulse，同一拍的 response_pulse 被忽略，
        //   因此进入 WAIT_DELAY 而不是 FALSE_START。
        // =========================================================
        do_reset;
        @(negedge clk);
        start_pressed    = 1'b1;
        response_pressed = 1'b1;
        wait_state(3'b001, 2000);   // WAIT_DELAY
        repeat (200) @(posedge clk);
        if (dut.u_dut.u_reaction_fsm.state !== 3'b001) begin
            $display("ERROR: simultaneous start+response left WAIT_DELAY, state=%b",
                     dut.u_dut.u_reaction_fsm.state);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T6b_simultaneous_start_wins");
        start_pressed    = 1'b0;
        response_pressed = 1'b0;
        do_reset;

        // =========================================================
        // T7: 随机按键，输出无 X/Z
        // =========================================================
        do_reset;
        for (i = 0; i < 2000; i = i + 1) begin
            @(negedge clk);
            start_pressed    = $random;
            response_pressed = $random;
            unmapped_pressed = $random;
            @(posedge clk);

            if (ready_led !== 1'b0 && ready_led !== 1'b1) begin
                $display("ERROR: ready_led X/Z: %b", ready_led);
                $fatal(1, "TEST_FAILED");
            end
            if ((^seg) === 1'bx) begin
                $display("ERROR: seg has X/Z: %b", seg);
                $fatal(1, "TEST_FAILED");
            end
            if (digit_sel !== 4'b1110 && digit_sel !== 4'b1101 &&
                digit_sel !== 4'b1011 && digit_sel !== 4'b0111) begin
                $display("ERROR: digit_sel invalid: %b", digit_sel);
                $fatal(1, "TEST_FAILED");
            end
            if (key_row !== 4'b1110 && key_row !== 4'b1101 &&
                key_row !== 4'b1011 && key_row !== 4'b0111) begin
                $display("ERROR: key_row invalid: %b", key_row);
                $fatal(1, "TEST_FAILED");
            end
        end
        start_pressed    = 1'b0;
        response_pressed = 1'b0;
        unmapped_pressed = 1'b0;
        $display("PASS: T7_random_no_xz");

        do_reset;
        $display("ALL_TESTS_PASSED");
        $finish;
    end

endmodule
