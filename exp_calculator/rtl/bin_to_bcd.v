// ============================================================
// 二进制 -> 4 位 BCD（Double Dabble）
//
// start 后逐拍处理 1 个二进制位，14 拍完成，避免组合除法/
// 取模在 50 MHz 下形成长路径。
//
// 输入范围 0..9999（14 位），输出 {千,百,十,个}。
// ============================================================
module bin_to_bcd (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [13:0] bin,
    output reg         busy,
    output reg         done,
    output reg  [15:0] bcd
);

    reg [13:0] src;
    reg [15:0] acc;
    reg [3:0]  cnt;

    // 每个 BCD 位 >=5 时先加 3，再整体左移
    wire [3:0] a0 = acc[3:0]  + ((acc[3:0]  > 4'd4) ? 4'd3 : 4'd0);
    wire [3:0] a1 = acc[7:4]  + ((acc[7:4]  > 4'd4) ? 4'd3 : 4'd0);
    wire [3:0] a2 = acc[11:8] + ((acc[11:8] > 4'd4) ? 4'd3 : 4'd0);
    wire [3:0] a3 = acc[15:12]+ ((acc[15:12]> 4'd4) ? 4'd3 : 4'd0);

    wire [15:0] adj     = {a3, a2, a1, a0};
    wire [15:0] shifted = {adj[14:0], src[13]};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            busy <= 1'b0;
            done <= 1'b0;
            bcd  <= 16'h0000;
            src  <= 14'd0;
            acc  <= 16'h0000;
            cnt  <= 4'd0;
        end else begin
            done <= 1'b0;

            if (start) begin
                src  <= bin;
                acc  <= 16'h0000;
                cnt  <= 4'd0;
                busy <= 1'b1;
            end else if (busy) begin
                acc <= shifted;
                src <= {src[12:0], 1'b0};

                if (cnt == 4'd13) begin
                    busy <= 1'b0;
                    done <= 1'b1;
                    bcd  <= shifted;
                end else begin
                    cnt <= cnt + 4'd1;
                end
            end
        end
    end

endmodule
