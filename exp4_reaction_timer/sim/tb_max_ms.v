// ============================================================
// MAX_MS = 9999 封顶的端到端验证
//
// 检查的是硬件逻辑本身，而不是测试假设：
//   1. ms_counter 在 ms_value == 9999 时停止递增，不会进位/回绕
//   2. reaction_fsm 的 result_latch 直接取 ms_value，因此也 <= 9999
//   3. BCD 转换后的 display_value 正好是 16'h9999（4 位 BCD 不溢出）
//
// 用 MS_DIV=1 让 ms 计数器每拍加 1，11000 拍就能跑过封顶点。
// ============================================================

`timescale 1ns/1ps

module tb_max_ms;

    localparam CLK_FREQ     = 1000;
    localparam DEBOUNCE_CNT = 5;
    localparam MIN_DELAY    = 1;
    localparam MAX_DELAY    = 1;
    localparam MS_DIV       = 1;      // 每拍加 1 ms
    localparam MAX_MS       = 9999;
    localparam FAIL_TIME    = 1;
    localparam SCAN_DIV     = 4;
    localparam CLK_PERIOD   = 10;

    reg        clk;
    reg        reset;
    reg        start_button;
    reg        response_button;
    wire       ready_led;
    wire [7:0] seg;
    wire [3:0] digit_sel;

    reaction_timer_top #(
        .CLK_FREQ     (CLK_FREQ),
        .DEBOUNCE_CNT (DEBOUNCE_CNT),
        .MIN_DELAY    (MIN_DELAY),
        .MAX_DELAY    (MAX_DELAY),
        .MS_DIV       (MS_DIV),
        .MAX_MS       (MAX_MS),
        .FAIL_TIME    (FAIL_TIME),
        .SCAN_DIV     (SCAN_DIV)
    ) dut (
        .clk             (clk),
        .reset           (reset),
        .start_button    (start_button),
        .response_button (response_button),
        .ready_led       (ready_led),
        .seg             (seg),
        .digit_sel       (digit_sel)
    );

    integer n;

    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD/2) clk = ~clk;
    end

    task do_reset;
        begin
            reset           = 1'b1;
            start_button    = 1'b0;
            response_button = 1'b0;
            repeat (5) @(posedge clk);
            @(negedge clk);
            reset = 1'b0;
            repeat (2) @(posedge clk);
        end
    endtask

    initial begin
        do_reset;

        // 启动，等 LED 亮（MIN_DELAY=1 -> 1000 拍）
        @(negedge clk);
        start_button = 1'b1;
        n = 0;
        while (ready_led !== 1'b1 && n < 5000) begin
            @(posedge clk);
            n = n + 1;
        end
        if (ready_led !== 1'b1) begin
            $display("ERROR: never reached READY");
            $fatal(1, "TEST_FAILED");
        end
        @(negedge clk);
        start_button = 1'b0;

        // 跑过封顶点：9999 ms + 1001 拍余量
        repeat (11000) @(posedge clk);
        if (dut.u_ms_counter.ms_value !== 14'd9999) begin
            $display("ERROR: ms_value did not cap at 9999, got %0d",
                     dut.u_ms_counter.ms_value);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: ms_value capped at %0d", dut.u_ms_counter.ms_value);

        // 继续 enable 500 拍，确认不回绕
        repeat (500) @(posedge clk);
        if (dut.u_ms_counter.ms_value !== 14'd9999) begin
            $display("ERROR: ms_value wrapped after cap, got %0d",
                     dut.u_ms_counter.ms_value);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: ms_value stays at 9999");

        // 应答，确认 result_latch 和 BCD 显示都是 9999
        @(negedge clk);
        response_button = 1'b1;
        repeat (20) @(posedge clk);
        @(negedge clk);
        response_button = 1'b0;

        n = 0;
        while (dut.u_reaction_fsm.state !== 3'b011 && n < 500) begin
            @(posedge clk);
            n = n + 1;
        end
        if (dut.u_reaction_fsm.state !== 3'b011) begin
            $display("ERROR: did not enter DISPLAY_RESULT");
            $fatal(1, "TEST_FAILED");
        end

        // BCD 顺序转换约 15 拍
        repeat (30) @(posedge clk);

        if (dut.u_reaction_fsm.result_latch !== 14'd9999) begin
            $display("ERROR: result_latch expected 9999, got %0d",
                     dut.u_reaction_fsm.result_latch);
            $fatal(1, "TEST_FAILED");
        end
        if (dut.display_value !== 16'h9999) begin
            $display("ERROR: display_value expected 9999, got %h",
                     dut.display_value);
            $fatal(1, "TEST_FAILED");
        end

        $display("PASS: result_latch = 9999, display_value = %h", dut.display_value);
        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #1000000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
