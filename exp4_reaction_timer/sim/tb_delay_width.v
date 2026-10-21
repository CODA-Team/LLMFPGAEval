// ============================================================
// delay_generator 真实时钟位宽 / 取值验证
//
// 其他 TB 用很小的 CLK_FREQ 跑完整 1~5 秒，覆盖不到真板
// 50 MHz × 5 s = 250,000,000 这个量级。
//
// 本 TB 直接用 CLK_FREQ = 50,000,000，只检查启动后算出的
// delay_target，不等待 2.5 亿拍，所以仿真时间只有几微秒。
//
// 检查点：
//   - lfsr[2:0] = 0..7 依次映射到 1,2,3,4,5,1,2,3 秒
//   - 5 秒目标 = 250,000,000，需要 28 位，delay_target 是 32 位
// ============================================================

`timescale 1ns/1ps

module tb_delay_width;

    localparam integer CLK_FREQ   = 50000000;
    localparam integer MIN_DELAY  = 1;
    localparam integer MAX_DELAY  = 5;
    localparam integer CLK_PERIOD = 20;   // 50 MHz

    reg  clk;
    reg  reset;
    reg  start;
    wire delay_done;

    delay_generator #(
        .CLK_FREQ  (CLK_FREQ),
        .MIN_DELAY (MIN_DELAY),
        .MAX_DELAY (MAX_DELAY)
    ) dut (
        .clk        (clk),
        .reset      (reset),
        .start      (start),
        .delay_done (delay_done)
    );

    integer    i;
    integer    exp_sec;
    reg [31:0] expected;
    reg [31:0] actual;
    reg [31:0] min_target;
    reg [31:0] max_target;

    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD/2) clk = ~clk;
    end

    initial begin
        reset = 1'b1;
        start = 1'b0;
        repeat (5) @(posedge clk);

        min_target = 32'hFFFFFFFF;
        max_target = 32'd0;

        for (i = 0; i < 8; i = i + 1) begin
            // 复位清 running，再强制一个已知的 3 位随机值
            @(negedge clk);
            reset = 1'b1;
            @(negedge clk);
            reset = 1'b0;
            force dut.lfsr = {13'd0, i[2:0]};

            @(negedge clk);
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;

            // 第 1 拍锁存秒数，第 2 拍乘 CLK_FREQ
            repeat (2) @(posedge clk);
            #1 actual = dut.delay_target;

            exp_sec  = MIN_DELAY + ((i < 5) ? i : i - 5);
            expected = exp_sec * CLK_FREQ;

            if (actual !== expected) begin
                $display("ERROR: i=%0d expected target=%0d actual=%0d",
                         i, expected, actual);
                $fatal(1, "TEST_FAILED");
            end

            if (actual < min_target) min_target = actual;
            if (actual > max_target) max_target = actual;

            release dut.lfsr;
            @(negedge clk);
        end

        if (min_target !== 32'd50000000) begin
            $display("ERROR: min target should be 50000000, got %0d", min_target);
            $fatal(1, "TEST_FAILED");
        end
        if (max_target !== 32'd250000000) begin
            $display("ERROR: max target should be 250000000, got %0d", max_target);
            $fatal(1, "TEST_FAILED");
        end

        $display("PASS: delay_target 1..5s at 50 MHz, min=%0d max=%0d (28 bits needed, reg is 32)",
                 min_target, max_target);
        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #100000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
