// ============================================================
// 4x4 矩阵键盘扫描器
//
// 行输出低有效，逐行扫描；列输入低有效（外部/内部上拉）。
// 每扫完 4 行产生一次采样：
//   sample_valid   : 单周期脉冲
//   sample_pressed : 本帧是否有键按下
//   sample_code    : {row[1:0], col[1:0]}，无键时无意义
//
// SCAN_DIV = 每行保持的时钟数。默认 12500 @50 MHz：
//   每行 0.25 ms，整帧 1 ms（1 kHz 采样率），与 10 次去抖
//   配合得到约 10 ms 的按键响应。
// ============================================================
module keypad_scanner #(
    parameter integer SCAN_DIV = 12500
) (
    input  wire       clk,
    input  wire       rst_n,
    output reg  [3:0] key_row,       // 低有效，one-hot-low
    input  wire [3:0] key_col,       // 低有效，按下为 0
    output reg        sample_valid,
    output reg  [3:0] sample_code,
    output reg        sample_pressed
);

    reg [15:0] div_cnt;
    reg [1:0]  row_idx;
    reg [3:0]  col_meta;
    reg [3:0]  col_sync;
    reg        found;                // 本帧前面的行是否已经找到按键
    reg [3:0]  found_code;

    wire [3:0] hit = ~col_sync;
    wire       any_hit = |hit;

    // 同一行内多键按下的优先级：列号小的优先
    wire [1:0] hit_idx = hit[0] ? 2'd0 :
                         hit[1] ? 2'd1 :
                         hit[2] ? 2'd2 : 2'd3;

    wire [3:0] cur_code    = {row_idx, hit_idx};
    // 前面行已经找到按键时保持它，避免同一列在后续行被重复识别
    wire [3:0] slot_code   = found ? found_code : cur_code;
    wire       slot_pressed = found | any_hit;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt        <= 16'd0;
            row_idx        <= 2'd0;
            key_row        <= 4'b1110;
            col_meta       <= 4'hF;
            col_sync       <= 4'hF;
            sample_valid   <= 1'b0;
            sample_code    <= 4'h0;
            sample_pressed <= 1'b0;
            found          <= 1'b0;
            found_code     <= 4'h0;
        end else begin
            sample_valid <= 1'b0;

            // 列输入两级同步，消除亚稳态
            col_meta <= key_col;
            col_sync <= col_meta;

            if (div_cnt >= SCAN_DIV - 1) begin
                div_cnt <= 16'd0;

                if (row_idx == 2'd3) begin
                    // 四行扫完，输出本帧采样
                    sample_valid   <= 1'b1;
                    sample_pressed <= slot_pressed;
                    sample_code    <= slot_code;
                    found          <= 1'b0;
                    row_idx        <= 2'd0;
                    key_row        <= 4'b1110;
                end else begin
                    if (any_hit && !found) begin
                        found      <= 1'b1;
                        found_code <= cur_code;
                    end
                    row_idx <= row_idx + 2'd1;
                    key_row <= ~(4'b0001 << (row_idx + 2'd1));
                end
            end else begin
                div_cnt <= div_cnt + 16'd1;
            end
        end
    end

endmodule
