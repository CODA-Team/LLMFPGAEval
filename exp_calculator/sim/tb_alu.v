// ============================================================
// calc_alu 单元测试
//   加 / 减 / 乘 / 除 / 除零 / 溢出 / 负数范围
// ============================================================
`timescale 1ns/1ps

module tb_alu;

    reg        clk;
    reg        rst_n;
    reg        start;
    reg  [1:0] op;
    reg  [13:0] a;
    reg  [13:0] b;
    wire       busy;
    wire       done;
    wire [13:0] res;
    wire       neg;
    wire       err;

    `include "tb_common.vh"

    localparam [1:0] OP_ADD = 2'd0;
    localparam [1:0] OP_SUB = 2'd1;
    localparam [1:0] OP_MUL = 2'd2;
    localparam [1:0] OP_DIV = 2'd3;

    calc_alu dut (
        .clk   (clk),
        .rst_n (rst_n),
        .start (start),
        .op    (op),
        .a     (a),
        .b     (b),
        .busy  (busy),
        .done  (done),
        .res   (res),
        .neg   (neg),
        .err   (err)
    );

    integer cycles;

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    task run_op;
        input [255:0] name;
        input [1:0]   t_op;
        input [13:0]  t_a;
        input [13:0]  t_b;
        input [13:0]  e_res;
        input         e_neg;
        input         e_err;
        begin
            @(negedge clk);
            op    = t_op;
            a     = t_a;
            b     = t_b;
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;

            cycles = 0;
            while (done !== 1'b1 && cycles < 100) begin
                @(negedge clk);
                cycles = cycles + 1;
            end

            if (done !== 1'b1) begin
                $display("ERROR: %0s timeout", name);
                $fatal(1, "TEST_FAILED");
            end

            if (res !== e_res || neg !== e_neg || err !== e_err) begin
                $display("ERROR: %0s expected res=%0d neg=%b err=%b, ",
                         name, e_res, e_neg, e_err);
                $display("       actual   res=%0d neg=%b err=%b (cycles=%0d)",
                         res, neg, err, cycles);
                $fatal(1, "TEST_FAILED");
            end

            $display("PASS: %0s (res=%0d neg=%b err=%b, %0d cycles)",
                     name, res, neg, err, cycles);
            @(negedge clk);
        end
    endtask

    initial begin
        rst_n = 1'b0;
        start = 1'b0;
        op    = 2'd0;
        a     = 14'd0;
        b     = 14'd0;
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        run_op("12 + 34",       OP_ADD, 14'd12,   14'd34,   14'd46,   1'b0, 1'b0);
        run_op("9999 + 0",      OP_ADD, 14'd9999, 14'd0,    14'd9999, 1'b0, 1'b0);
        run_op("9999 + 1",      OP_ADD, 14'd9999, 14'd1,    14'd0,    1'b0, 1'b1);
        run_op("99 - 100",      OP_SUB, 14'd99,   14'd100,  14'd1,    1'b1, 1'b0);
        run_op("9999 - 9999",   OP_SUB, 14'd9999, 14'd9999, 14'd0,    1'b0, 1'b0);
        run_op("1000 - 9999",   OP_SUB, 14'd1000, 14'd9999, 14'd0,    1'b0, 1'b1);
        run_op("12 * 12",       OP_MUL, 14'd12,   14'd12,   14'd144,  1'b0, 1'b0);
        run_op("9999 * 1",      OP_MUL, 14'd9999, 14'd1,    14'd9999, 1'b0, 1'b0);
        run_op("9999 * 9999",   OP_MUL, 14'd9999, 14'd9999, 14'd0,    1'b0, 1'b1);
        run_op("100 / 5",       OP_DIV, 14'd100,  14'd5,    14'd20,   1'b0, 1'b0);
        run_op("9999 / 1",      OP_DIV, 14'd9999, 14'd1,    14'd9999, 1'b0, 1'b0);
        run_op("1 / 9999",      OP_DIV, 14'd1,    14'd9999, 14'd0,    1'b0, 1'b0);
        run_op("100 / 0",       OP_DIV, 14'd100,  14'd0,    14'd0,    1'b0, 1'b1);
        run_op("0 / 7",         OP_DIV, 14'd0,    14'd7,    14'd0,    1'b0, 1'b0);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #500000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
