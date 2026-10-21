// ============================================================
// 多周期算术单元
//
// op: 0=加 1=减 2=乘 3=除
// 加/减 单周期完成；乘/除用移位迭代，14 拍完成，
// 避免 14x14 乘法器和 14/14 除法器压垮 50 MHz 时序。
//
// 结果约定：
//   res     : 结果绝对值（0..9999）
//   neg     : 结果为负（仅减法）
//   err     : 溢出 / 除零 / 负数超出 3 位显示范围
// 负数显示范围取 -1..-999；-1000 及以下置 err。
// ============================================================
module calc_alu (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [1:0]  op,
    input  wire [13:0] a,
    input  wire [13:0] b,
    output reg         busy,
    output reg         done,
    output reg  [13:0] res,
    output reg         neg,
    output reg         err
);

    localparam [1:0] OP_ADD = 2'd0;
    localparam [1:0] OP_SUB = 2'd1;
    localparam [1:0] OP_MUL = 2'd2;
    localparam [1:0] OP_DIV = 2'd3;

    localparam [1:0] ST_IDLE = 2'd0;
    localparam [1:0] ST_MUL  = 2'd1;
    localparam [1:0] ST_DIV  = 2'd2;

    reg [1:0]  state;
    reg [13:0] a_r;
    reg [13:0] b_r;

    // 乘法：28 位累加器 + 28 位被乘数 + 14 位乘数
    reg [27:0] mul_acc;
    reg [27:0] mul_mcand;
    reg [13:0] mul_mplier;

    // 除法：15 位余数 + 14 位商 + 14 位被除数移位寄存器
    reg [14:0] div_rem;
    reg [13:0] div_quot;
    reg [13:0] div_dend;

    reg [3:0] cnt;

    // ---------------- 组合运算 ----------------
    // 加/减在 start 当拍直接用输入端口计算，不能等 a_r/b_r 打拍
    wire [14:0] sum15 = {1'b0, a} + {1'b0, b};

    wire signed [15:0] diff16 =
        $signed({2'b00, a}) - $signed({2'b00, b});
    wire [14:0] diff_mag = diff16[15] ? (~diff16[14:0] + 15'd1)
                                      :  diff16[14:0];

    wire [27:0] mul_next = mul_acc +
                           (mul_mplier[0] ? mul_mcand : 28'd0);

    wire [14:0] div_shift = {div_rem[13:0], div_dend[13]};
    wire [14:0] div_sub   = div_shift - {1'b0, b_r};
    wire        div_ge    = (div_shift >= {1'b0, b_r});
    wire [13:0] div_qnext = {div_quot[12:0], div_ge};

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state      <= ST_IDLE;
            busy       <= 1'b0;
            done       <= 1'b0;
            res        <= 14'd0;
            neg        <= 1'b0;
            err        <= 1'b0;
            a_r        <= 14'd0;
            b_r        <= 14'd0;
            mul_acc    <= 28'd0;
            mul_mcand  <= 28'd0;
            mul_mplier <= 14'd0;
            div_rem    <= 15'd0;
            div_quot   <= 14'd0;
            div_dend   <= 14'd0;
            cnt        <= 4'd0;
        end else begin
            done <= 1'b0;

            case (state)
                ST_IDLE: begin
                    busy <= 1'b0;
                    if (start) begin
                        a_r <= a;
                        b_r <= b;
                        neg <= 1'b0;
                        err <= 1'b0;

                        case (op)
                            OP_ADD: begin
                                if (sum15 > 15'd9999) begin
                                    err <= 1'b1;
                                    res <= 14'd0;
                                end else begin
                                    res <= sum15[13:0];
                                end
                                done <= 1'b1;
                            end

                            OP_SUB: begin
                                if (diff16[15]) begin
                                    if (diff_mag > 15'd999) begin
                                        err <= 1'b1;
                                        res <= 14'd0;
                                    end else begin
                                        res <= diff_mag[13:0];
                                        neg <= 1'b1;
                                    end
                                end else begin
                                    res <= diff16[13:0];
                                end
                                done <= 1'b1;
                            end

                            OP_MUL: begin
                                mul_acc    <= 28'd0;
                                mul_mcand  <= {14'd0, a};
                                mul_mplier <= b;
                                cnt        <= 4'd0;
                                busy       <= 1'b1;
                                state      <= ST_MUL;
                            end

                            OP_DIV: begin
                                if (b == 14'd0) begin
                                    err  <= 1'b1;
                                    res  <= 14'd0;
                                    done <= 1'b1;
                                end else begin
                                    div_rem  <= 15'd0;
                                    div_quot <= 14'd0;
                                    div_dend <= a;
                                    cnt      <= 4'd0;
                                    busy     <= 1'b1;
                                    state    <= ST_DIV;
                                end
                            end

                            default: begin
                                err  <= 1'b1;
                                done <= 1'b1;
                            end
                        endcase
                    end
                end

                ST_MUL: begin
                    if (cnt == 4'd13) begin
                        if (mul_next > 28'd9999) begin
                            err <= 1'b1;
                            res <= 14'd0;
                        end else begin
                            res <= mul_next[13:0];
                        end
                        busy  <= 1'b0;
                        done  <= 1'b1;
                        state <= ST_IDLE;
                    end else begin
                        mul_acc    <= mul_next;
                        mul_mcand  <= mul_mcand << 1;
                        mul_mplier <= mul_mplier >> 1;
                        cnt        <= cnt + 4'd1;
                    end
                end

                ST_DIV: begin
                    if (cnt == 4'd13) begin
                        if (div_qnext > 14'd9999) begin
                            err <= 1'b1;
                            res <= 14'd0;
                        end else begin
                            res <= div_qnext;
                        end
                        busy  <= 1'b0;
                        done  <= 1'b1;
                        state <= ST_IDLE;
                    end else begin
                        div_rem  <= div_ge ? div_sub : div_shift;
                        div_quot <= div_qnext;
                        div_dend <= {div_dend[12:0], 1'b0};
                        cnt      <= cnt + 4'd1;
                    end
                end

                default: state <= ST_IDLE;
            endcase
        end
    end

endmodule
