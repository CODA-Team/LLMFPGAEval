// ============================================================
// bin_to_bcd 单元测试
// 参考模型用 / 和 % 直接算，不复刻 Double Dabble。
// ============================================================
`timescale 1ns/1ps

module tb_bcd;

    reg        clk;
    reg        rst_n;
    reg        start;
    reg  [13:0] bin;
    wire       busy;
    wire       done;
    wire [15:0] bcd;

    `include "tb_common.vh"

    bin_to_bcd dut (
        .clk   (clk),
        .rst_n (rst_n),
        .start (start),
        .bin   (bin),
        .busy  (busy),
        .done  (done),
        .bcd   (bcd)
    );

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    task convert;
        input [13:0] v;
        reg [15:0] expected;
        integer cycles;
        begin
            expected = ref_bcd(v);

            @(negedge clk);
            start = 1'b1;
            bin   = v;
            @(negedge clk);
            start = 1'b0;

            cycles = 0;
            while (done !== 1'b1 && cycles < 100) begin
                @(negedge clk);
                cycles = cycles + 1;
            end

            if (done !== 1'b1) begin
                $display("ERROR: bcd timeout for %0d", v);
                $fatal(1, "TEST_FAILED");
            end

            if (bcd !== expected) begin
                $display("ERROR: bcd(%0d) expected=%h actual=%h",
                         v, expected, bcd);
                $fatal(1, "TEST_FAILED");
            end

            $display("PASS: bcd(%0d) = %h", v, bcd);

            @(negedge clk);
        end
    endtask

    initial begin
        rst_n = 1'b0;
        start = 1'b0;
        bin   = 14'd0;
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        convert(14'd0);
        convert(14'd1);
        convert(14'd9);
        convert(14'd10);
        convert(14'd20);
        convert(14'd46);
        convert(14'd144);
        convert(14'd999);
        convert(14'd1234);
        convert(14'd5000);
        convert(14'd9999);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #500000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
