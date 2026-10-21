// ============================================================
// 真实去抖窗口 DEBOUNCE_CNT = 500000 的专项验证
//
// 顶层 TB 和板级 TB 为了仿真速度都把 DEBOUNCE_CNT 覆盖成 5，
// 覆盖不到真实的 10 ms / 50 MHz 去抖逻辑。本 TB 直接用
// DEBOUNCE_CNT=500000、50 MHz（周期 20 ns）验证：
//
//   A. 499999 拍（略短于 10 ms）的毛刺被拒绝，不产生脉冲
//   B. 按住 500100 拍 -> start / response 各恰好一个脉冲
//   C. 继续按住 -> 不重复触发
//   D. 松开 -> 不产生脉冲（上升沿触发）
//   E. 松开只稳定 1 ms 就再按 -> 被忽略（去抖同样作用于释放）
//   F. 松开稳定 10 ms 后再按 -> 第二个脉冲
//
// 两个通道同时驱动，一次覆盖 start 和 response 两套计数器。
// ============================================================

`timescale 1ns/1ps

module tb_debounce_real;

    localparam DEBOUNCE_CNT = 500000;
    localparam CLK_PERIOD   = 20;   // 50 MHz

    reg  clk;
    reg  reset;
    reg  start_button;
    reg  response_button;
    wire start_pulse;
    wire response_pulse;

    input_sync_debounce #(
        .DEBOUNCE_CNT (DEBOUNCE_CNT)
    ) dut (
        .clk             (clk),
        .reset           (reset),
        .start_button    (start_button),
        .response_button (response_button),
        .start_pulse     (start_pulse),
        .response_pulse  (response_pulse)
    );

    integer start_pulses;
    integer response_pulses;

    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD/2) clk = ~clk;
    end

    always @(posedge clk) begin
        if (start_pulse    === 1'b1) start_pulses    = start_pulses + 1;
        if (response_pulse === 1'b1) response_pulses = response_pulses + 1;
    end

    initial begin
        start_pulses    = 0;
        response_pulses = 0;
        reset           = 1'b1;
        start_button    = 1'b0;
        response_button = 1'b0;

        repeat (5) @(posedge clk);
        @(negedge clk);
        reset = 1'b0;
        repeat (2) @(posedge clk);

        // A: 499999 拍毛刺，差 1 拍不到 10 ms
        @(negedge clk);
        start_button    = 1'b1;
        response_button = 1'b1;
        repeat (499999) @(posedge clk);
        if (start_pulses !== 0 || response_pulses !== 0) begin
            $display("ERROR: sub-threshold glitch produced a pulse (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        @(negedge clk);
        start_button    = 1'b0;
        response_button = 1'b0;
        repeat (20) @(posedge clk);
        if (start_pulses !== 0 || response_pulses !== 0) begin
            $display("ERROR: pulse appeared after glitch release (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: 499999-cycle glitch rejected");

        // B: 合法按下，超过 10 ms
        @(negedge clk);
        start_button    = 1'b1;
        response_button = 1'b1;

        // 400000 拍时还不该有脉冲
        repeat (400000) @(posedge clk);
        if (start_pulses !== 0 || response_pulses !== 0) begin
            $display("ERROR: pulse too early (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end

        repeat (100100) @(posedge clk);   // 共 500100 拍
        if (start_pulses !== 1 || response_pulses !== 1) begin
            $display("ERROR: expected exactly one pulse, got %0d/%0d",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: 10 ms press -> exactly one pulse on both channels");

        // C: 继续按住不重复触发
        repeat (1000) @(posedge clk);
        if (start_pulses !== 1 || response_pulses !== 1) begin
            $display("ERROR: holding retriggered (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: holding does not retrigger");

        // D: 松开不产生脉冲
        @(negedge clk);
        start_button    = 1'b0;
        response_button = 1'b0;
        repeat (1000) @(posedge clk);
        if (start_pulses !== 1 || response_pulses !== 1) begin
            $display("ERROR: release produced a pulse (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: release produces no pulse");

        // E: 松开只稳定 1 ms 就再按。
        //    去抖对释放同样生效：去抖后电平还没回落，重新按下
        //    被视为同一段按住，不应产生新脉冲。
        @(negedge clk);
        start_button    = 1'b1;
        response_button = 1'b1;
        repeat (501000) @(posedge clk);
        if (start_pulses !== 1 || response_pulses !== 1) begin
            $display("ERROR: short release re-press retriggered (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: re-press after <10 ms release ignored");

        // F: 松开稳定超过 10 ms，再按 -> 第二个脉冲
        @(negedge clk);
        start_button    = 1'b0;
        response_button = 1'b0;
        repeat (520000) @(posedge clk);
        if (start_pulses !== 1 || response_pulses !== 1) begin
            $display("ERROR: long release produced a pulse (%0d/%0d)",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end

        @(negedge clk);
        start_button    = 1'b1;
        response_button = 1'b1;
        repeat (501000) @(posedge clk);
        if (start_pulses !== 2 || response_pulses !== 2) begin
            $display("ERROR: second press expected 2 pulses, got %0d/%0d",
                     start_pulses, response_pulses);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: second press after >=10 ms release produces a pulse");

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #70000000;   // 70 ms，全部激励约 51 ms
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
