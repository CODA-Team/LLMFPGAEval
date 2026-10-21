// ============================================================
// 4 位共阴数码管动态扫描
//
//   seg[7:0]   = {dp, g, f, e, d, c, b, a}，高有效
//   digit_sel  = 低有效，[0] 是最右位
//
// 显示规则：
//   - 正常：右对齐，前导零消隐；单独一个 0 正常显示
//   - 负数：最左位显示 '-'，低 3 位显示绝对值
//   - 错误：显示 "Err"（最左位消隐）
// ============================================================
module seg_display #(
    parameter integer SCAN_DIV = 12500
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire [15:0] digits,
    input  wire        show_neg,
    input  wire        show_err,
    output reg  [7:0]  seg,
    output wire [3:0]  digit_sel
);

    reg [15:0] div_cnt;
    reg [1:0]  idx;

    reg [3:0] d0, d1, d2, d3;
    reg [3:0] blank;

    assign digit_sel = ~(4'b0001 << idx);

    // 0-9，以及 '-' / 'E' / 'r'
    function [6:0] seg7;
        input [3:0] d;
        begin
            case (d)
                4'd0: seg7 = 7'b0111111;
                4'd1: seg7 = 7'b0000110;
                4'd2: seg7 = 7'b1011011;
                4'd3: seg7 = 7'b1001111;
                4'd4: seg7 = 7'b1100110;
                4'd5: seg7 = 7'b1101101;
                4'd6: seg7 = 7'b1111101;
                4'd7: seg7 = 7'b0000111;
                4'd8: seg7 = 7'b1111111;
                4'd9: seg7 = 7'b1101111;
                4'hA: seg7 = 7'b1000000;   // '-'
                4'hB: seg7 = 7'b1111001;   // 'E'
                4'hC: seg7 = 7'b1010000;   // 'r'
                default: seg7 = 7'b0000000;
            endcase
        end
    endfunction

    // 根据显示模式生成每位数字和消隐掩码
    always @* begin
        d0 = digits[3:0];
        d1 = digits[7:4];
        d2 = digits[11:8];
        d3 = digits[15:12];
        blank = 4'b0000;

        if (show_err) begin
            d3    = 4'h0;
            d2    = 4'hB;   // E
            d1    = 4'hC;   // r
            d0    = 4'hC;   // r
            blank = 4'b1000;
        end else if (show_neg) begin
            d3 = 4'hA;      // '-'
            if (digits[11:8] != 4'd0) begin
                blank = 4'b0000;
            end else if (digits[7:4] != 4'd0) begin
                blank = 4'b0100;
            end else begin
                blank = 4'b0110;
            end
        end else begin
            if (digits[15:12] != 4'd0) begin
                blank = 4'b0000;
            end else if (digits[11:8] != 4'd0) begin
                blank = 4'b1000;
            end else if (digits[7:4] != 4'd0) begin
                blank = 4'b1100;
            end else begin
                blank = 4'b1110;
            end
        end
    end

    reg [3:0] cur_digit;
    always @* begin
        case (idx)
            2'd0:    cur_digit = d0;
            2'd1:    cur_digit = d1;
            2'd2:    cur_digit = d2;
            default: cur_digit = d3;
        endcase
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            div_cnt <= 16'd0;
            idx     <= 2'd0;
            seg     <= 8'h00;
        end else if (div_cnt >= SCAN_DIV - 1) begin
            // 换位时插入一拍消隐，避免拖影
            div_cnt <= 16'd0;
            idx     <= idx + 2'd1;
            seg     <= 8'h00;
        end else begin
            div_cnt <= div_cnt + 16'd1;
            if (blank[idx]) begin
                seg <= 8'h00;
            end else begin
                seg <= {1'b0, seg7(cur_digit)};
            end
        end
    end

endmodule
