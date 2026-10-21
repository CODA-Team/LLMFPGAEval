// ============================================================
// keypad_scanner 单元测试
//   - 行扫描 one-hot-low，整帧 4*SCAN_DIV 拍
//   - 单键采样编码
//   - 同一行多键时列号小的优先
// ============================================================
`timescale 1ns/1ps

module tb_keypad;

    reg        clk;
    reg        rst_n;
    reg  [15:0] pressed;      // 16 个物理键的按下状态
    reg  [3:0]  key_col_r;
    wire [3:0]  key_col;
    wire [3:0] key_row;
    wire       sample_valid;
    wire [3:0] sample_code;
    wire       sample_pressed;

    localparam integer SCAN = 8;

    `include "tb_common.vh"

    keypad_scanner #(
        .SCAN_DIV (SCAN)
    ) dut (
        .clk            (clk),
        .rst_n          (rst_n),
        .key_row        (key_row),
        .key_col        (key_col),
        .sample_valid   (sample_valid),
        .sample_code    (sample_code),
        .sample_pressed (sample_pressed)
    );

    integer k;
    integer cnt;
    integer i;

    // 真实矩阵模型：只有被扫描到的那一行，按键才会把对应列拉低
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

    initial begin
        rst_n   = 1'b0;
        pressed = 16'h0000;
        repeat (4) @(posedge clk);
        @(negedge clk);
        rst_n = 1'b1;

        // 行扫描 one-hot-low
        for (k = 0; k < 4 * SCAN; k = k + 1) begin
            @(negedge clk);
            case (key_row)
                4'b1110, 4'b1101, 4'b1011, 4'b0111: ;
                default: begin
                    $display("ERROR: invalid key_row=%b", key_row);
                    $fatal(1, "TEST_FAILED");
                end
            endcase
        end
        $display("PASS: key_row is one-hot-low");

        // 帧周期：row1 起点到下一次 row0 起点 = 3 * SCAN_DIV
        while (key_row !== 4'b1110) @(negedge clk);
        while (key_row === 4'b1110) @(negedge clk);

        cnt = 0;
        while (key_row !== 4'b1110) begin
            @(negedge clk);
            cnt = cnt + 1;
        end
        if (cnt !== 3 * SCAN) begin
            $display("ERROR: row1..row3 span expected %0d, got %0d",
                     3 * SCAN, cnt);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: row1..row3 span = %0d cycles (3 x SCAN_DIV)", cnt);

        // 无键采样
        wait (sample_valid === 1'b1);
        @(negedge clk);
        check1("idle sample_pressed", 1'b0, sample_pressed);

        // 单键：row1 col2 -> code 6
        pressed[6] = 1'b1;
        wait (sample_valid === 1'b1 && sample_pressed === 1'b1);
        @(negedge clk);
        check1("single key pressed", 1'b1, sample_pressed);
        check16("single key code", 16'h0006, {12'h000, sample_code});

        pressed = 16'h0000;
        wait (sample_valid === 1'b1 && sample_pressed === 1'b0);
        @(negedge clk);
        check1("key released", 1'b0, sample_pressed);

        // 同一行多键：row0 col0 + col3，优先 col0 -> code 0
        pressed[0] = 1'b1;
        pressed[3] = 1'b1;
        wait (sample_valid === 1'b1 && sample_pressed === 1'b1);
        @(negedge clk);
        check16("multi key priority", 16'h0000, {12'h000, sample_code});

        pressed = 16'h0000;
        wait (sample_valid === 1'b1 && sample_pressed === 1'b0);
        @(negedge clk);

        $display("ALL_TESTS_PASSED");
        $finish;
    end

    initial begin
        #500000;
        $display("ERROR: timeout");
        $fatal(1, "TEST_FAILED");
    end

endmodule
