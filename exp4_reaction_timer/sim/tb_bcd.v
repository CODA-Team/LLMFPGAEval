// ============================================================
// bin_to_bcd_seq 边界值验证
//
// 顶层 TB 只覆盖到较小的 ms 值（约为 10~200），这里直接测
// 14 位输入的 0~9999 边界，确认 4 位 BCD 没有溢出或截断。
//
// 说明：输入超过 9999 时 4 位 BCD 装不下，属于设计约定外；
//       RTL 里 MAX_MS=9999 保证 ms_value/result_latch 不会越界。
// ============================================================

`timescale 1ns/1ps

module tb_bcd;

    reg         clk;
    reg         reset;
    reg  [13:0] din;
    wire [15:0] dout;
    wire        valid;

    bin_to_bcd_seq #(.WIDTH(14)) dut (
        .clk   (clk),
        .reset (reset),
        .din   (din),
        .dout  (dout),
        .valid (valid)
    );

    integer i;
    reg [13:0] test_value [0:15];
    reg [15:0] expected;

    always #5 clk = ~clk;

    task check_bcd;
        input [13:0] v;
        begin
            @(negedge clk);
            din = v;
            // 1 拍检测输入变化 + 14 拍转换，20 拍足够
            repeat (20) @(posedge clk);

            expected = (((v / 1000) % 10) << 12) |
                       (((v / 100)  % 10) << 8)  |
                       (((v / 10)   % 10) << 4)  |
                       ( v          % 10);

            if (valid !== 1'b1) begin
                $display("ERROR: bcd valid not high for %0d", v);
                $fatal(1, "TEST_FAILED");
            end
            if (dout !== expected) begin
                $display("ERROR: bcd(%0d) expected=%h actual=%h", v, expected, dout);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: bcd %0d -> %h", v, dout);
        end
    endtask

    initial begin
        clk   = 1'b0;
        reset = 1'b1;
        din   = 14'd0;

        test_value[0]  = 14'd0;
        test_value[1]  = 14'd1;
        test_value[2]  = 14'd5;
        test_value[3]  = 14'd9;
        test_value[4]  = 14'd10;
        test_value[5]  = 14'd11;
        test_value[6]  = 14'd99;
        test_value[7]  = 14'd100;
        test_value[8]  = 14'd101;
        test_value[9]  = 14'd999;
        test_value[10] = 14'd1000;
        test_value[11] = 14'd1001;
        test_value[12] = 14'd1234;
        test_value[13] = 14'd5000;
        test_value[14] = 14'd9998;
        test_value[15] = 14'd9999;

        repeat (3) @(posedge clk);
        @(negedge clk);
        reset = 1'b0;

        for (i = 0; i < 16; i = i + 1)
            check_bcd(test_value[i]);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #100000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
