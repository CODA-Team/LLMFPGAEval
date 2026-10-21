// ============================================================
// 矩阵扫描默认分频验证
//
// board TB 为了仿真速度把 KEY_SCAN_DIV 覆盖成 4，测不到
// 真实默认值。本 TB 用 matrix_key_scanner 的默认参数，
// 检查：
//   - 默认 SCAN_DIV = 12500（50 MHz 下每行 0.25 ms）
//   - 四行完整一轮 = 4 * 12500 = 50000 拍
// ============================================================

`timescale 1ns/1ps

module tb_key_scan_div;

    reg        clk;
    reg        reset;
    wire [3:0] key_row;
    wire       start_button;
    wire       response_button;

    matrix_key_scanner dut (
        .clk             (clk),
        .reset           (reset),
        .key_row         (key_row),
        .key_col         (4'b1111),   // 无按键
        .start_button    (start_button),
        .response_button (response_button)
    );

    integer cnt;

    initial begin
        clk = 1'b0;
        forever #5 clk = ~clk;
    end

    initial begin
        // 默认参数检查
        if (dut.SCAN_DIV !== 12500) begin
            $display("ERROR: default SCAN_DIV expected 12500, got %0d", dut.SCAN_DIV);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: default SCAN_DIV = %0d", dut.SCAN_DIV);

        reset = 1'b1;
        repeat (3) @(posedge clk);
        @(negedge clk);
        reset = 1'b0;

        // 等到离开 row0，再等到 row0 的起始沿，避免从槽中间开始计数
        @(negedge clk);
        while (key_row === 4'b1110) @(negedge clk);
        while (key_row !== 4'b1110) @(negedge clk);

        // 从 row0 的起始沿数到下一个 row0 起始沿：
        // 先在 row0 槽内数，离开后继续数，直到 row0 再次出现
        cnt = 0;
        while (key_row === 4'b1110) begin
            @(negedge clk);
            cnt = cnt + 1;
        end
        while (key_row !== 4'b1110) begin
            @(negedge clk);
            cnt = cnt + 1;
        end

        if (cnt !== 50000) begin
            $display("ERROR: frame period expected 50000 cycles, got %0d", cnt);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: four-row frame = %0d cycles (4 x 12500)", cnt);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #3000000;   // 3 ms：同步到帧起点约 1 ms + 测量 0.5 ms
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
