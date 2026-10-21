// ============================================================
// key_debounce 单元测试
//   - 连续 SAMPLES 帧才输出一次
//   - 按下期间不重复触发
//   - 释放不足 SAMPLES 帧不重新武装
//   - 中途换键 / 毛刺会重新计数
// ============================================================
`timescale 1ns/1ps

module tb_debounce;

    reg        clk;
    reg        rst_n;
    reg        sample_valid;
    reg  [3:0] sample_code;
    reg        sample_pressed;
    wire       key_valid;
    wire [3:0] key_code;

    localparam integer SAMPLES = 3;

    key_debounce #(
        .SAMPLES (SAMPLES)
    ) dut (
        .clk            (clk),
        .rst_n          (rst_n),
        .sample_valid   (sample_valid),
        .sample_code    (sample_code),
        .sample_pressed (sample_pressed),
        .key_valid      (key_valid),
        .key_code       (key_code)
    );

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    task send_sample;
        input [3:0] code;
        input       pressed;
        begin
            @(negedge clk);
            sample_valid   = 1'b1;
            sample_code    = code;
            sample_pressed = pressed;
            @(negedge clk);
            sample_valid   = 1'b0;
        end
    endtask

    task expect_no_event;
        input [255:0] name;
        begin
            if (key_valid !== 1'b0) begin
                $display("ERROR: %0s unexpected key_valid code=%0d",
                         name, key_code);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: %0s", name);
        end
    endtask

    task expect_event;
        input [255:0] name;
        input [3:0]   code;
        begin
            if (key_valid !== 1'b1 || key_code !== code) begin
                $display("ERROR: %0s expected code=%0d, key_valid=%b code=%0d",
                         name, code, key_valid, key_code);
                $fatal(1, "TEST_FAILED");
            end
            $display("PASS: %0s (code=%0d)", name, code);
        end
    endtask

    initial begin
        rst_n          = 1'b0;
        sample_valid   = 1'b0;
        sample_code    = 4'h0;
        sample_pressed = 1'b0;
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        // 2 帧还不够
        send_sample(4'd5, 1'b1);
        expect_no_event("2/3 samples");
        send_sample(4'd5, 1'b1);
        expect_no_event("2/3 samples again");

        // 第 3 帧触发一次
        send_sample(4'd5, 1'b1);
        expect_event("3/3 samples triggers", 4'd5);

        // 按住不放不再触发
        send_sample(4'd5, 1'b1);
        expect_no_event("held key does not repeat");
        send_sample(4'd5, 1'b1);
        expect_no_event("held key still no repeat");

        // 只释放 1 帧不能重新武装
        send_sample(4'd0, 1'b0);
        expect_no_event("1/3 release samples");
        send_sample(4'd5, 1'b1);
        expect_no_event("press during release debounce");

        // 稳定释放 3 帧后重新武装
        send_sample(4'd0, 1'b0);
        expect_no_event("release 1");
        send_sample(4'd0, 1'b0);
        expect_no_event("release 2");
        send_sample(4'd0, 1'b0);
        expect_no_event("release 3");

        // 新键需要重新计满 3 帧
        send_sample(4'd6, 1'b1);
        expect_no_event("new key 1/3");
        send_sample(4'd6, 1'b1);
        expect_no_event("new key 2/3");
        send_sample(4'd6, 1'b1);
        expect_event("new key 3/3", 4'd6);

        send_sample(4'd0, 1'b0);
        send_sample(4'd0, 1'b0);
        send_sample(4'd0, 1'b0);

        // 中途换键：2 帧 5 + 3 帧 6，只应触发 6
        send_sample(4'd5, 1'b1);
        send_sample(4'd5, 1'b1);
        expect_no_event("code change resets count (1)");
        send_sample(4'd6, 1'b1);
        expect_no_event("code change resets count (2)");
        send_sample(4'd6, 1'b1);
        expect_no_event("code change resets count (3)");
        send_sample(4'd6, 1'b1);
        expect_event("code change then stable", 4'd6);

        send_sample(4'd0, 1'b0);
        send_sample(4'd0, 1'b0);
        send_sample(4'd0, 1'b0);

        // 毛刺：2 帧按下 -> 1 帧松开 -> 2 帧按下 -> 1 帧松开 -> 3 帧按下
        send_sample(4'd7, 1'b1);
        send_sample(4'd7, 1'b1);
        send_sample(4'd0, 1'b0);
        expect_no_event("glitch part 1");
        send_sample(4'd7, 1'b1);
        send_sample(4'd7, 1'b1);
        send_sample(4'd0, 1'b0);
        expect_no_event("glitch part 2");
        send_sample(4'd7, 1'b1);
        send_sample(4'd7, 1'b1);
        expect_no_event("glitch recovery 2/3");
        send_sample(4'd7, 1'b1);
        expect_event("glitch recovery 3/3", 4'd7);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #100000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
