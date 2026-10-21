// ============================================================
// 计算器主状态机
//
// 键盘物理编码是 {row[1:0], col[1:0]}，decode_key 按板上
// 键盘布局转换成功能码：
//   0..9  数字
//   10 +  11 -  12 *  13 /
//   14 =  15 C
//
// 输入上限 4 位十进制（0..9999），第 5 位数字被忽略。
// 运算交给 calc_alu 多周期完成；显示用 bin_to_bcd。
// ============================================================
module calc_ctrl (
    input  wire        clk,
    input  wire        rst_n,

    input  wire        key_valid,
    input  wire [3:0]  key_code,

    output reg         alu_start,
    output reg  [1:0]  alu_op,
    output reg  [13:0] alu_a,
    output reg  [13:0] alu_b,
    input  wire        alu_busy,
    input  wire        alu_done,
    input  wire [13:0] alu_res,
    input  wire        alu_neg,
    input  wire        alu_err,

    output reg         bcd_start,
    output reg  [13:0] bcd_bin,
    input  wire        bcd_done,
    input  wire [15:0] bcd_digits,

    output reg  [15:0] digits,
    output reg         disp_neg,
    output reg         disp_err
);

    localparam [2:0] S_INPUT_A  = 3'd0;
    localparam [2:0] S_INPUT_B  = 3'd1;
    localparam [2:0] S_WAIT_ALU = 3'd2;
    localparam [2:0] S_RESULT   = 3'd3;
    localparam [2:0] S_ERROR    = 3'd4;

    localparam [3:0] KEY_PLUS  = 4'd10;
    localparam [3:0] KEY_MINUS = 4'd11;
    localparam [3:0] KEY_MUL   = 4'd12;
    localparam [3:0] KEY_DIV   = 4'd13;
    localparam [3:0] KEY_EQ    = 4'd14;
    localparam [3:0] KEY_C     = 4'd15;

    reg [2:0]  state;
    reg [13:0] acc;         // 当前输入
    reg [2:0]  ndig;        // 当前输入位数 0..4
    reg [13:0] a_reg;       // 左操作数
    reg [1:0]  op_reg;
    reg [13:0] res_mag;     // 结果绝对值
    reg        res_neg;
    reg        err_flag;

    // ---------------- 键盘布局 ----------------
    // Row0: 7 8 9 /      Row1: 4 5 6 *
    // Row2: 1 2 3 -      Row3: 0 = + C
    function [3:0] decode_key;
        input [3:0] raw;
        begin
            case (raw)
                4'd0:  decode_key = 4'd7;   // r0c0
                4'd1:  decode_key = 4'd8;   // r0c1
                4'd2:  decode_key = 4'd9;   // r0c2
                4'd3:  decode_key = 4'd13;  // r0c3  /
                4'd4:  decode_key = 4'd4;   // r1c0
                4'd5:  decode_key = 4'd5;   // r1c1
                4'd6:  decode_key = 4'd6;   // r1c2
                4'd7:  decode_key = 4'd12;  // r1c3  *
                4'd8:  decode_key = 4'd1;   // r2c0
                4'd9:  decode_key = 4'd2;   // r2c1
                4'd10: decode_key = 4'd3;   // r2c2
                4'd11: decode_key = 4'd11;  // r2c3  -
                4'd12: decode_key = 4'd0;   // r3c0
                4'd13: decode_key = 4'd14;  // r3c1  =
                4'd14: decode_key = 4'd10;  // r3c2  +
                default: decode_key = 4'd15; // r3c3  C
            endcase
        end
    endfunction

    wire [3:0]  key     = decode_key(key_code);
    wire [17:0] acc_x10 = {4'b0000, acc} * 18'd10 + {14'd0, key};
    wire [3:0]  op_num  = key - 4'd10;
    wire [1:0]  new_op  = op_num[1:0];

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state     <= S_INPUT_A;
            acc       <= 14'd0;
            ndig      <= 3'd0;
            a_reg     <= 14'd0;
            op_reg    <= 2'd0;
            res_mag   <= 14'd0;
            res_neg   <= 1'b0;
            err_flag  <= 1'b0;

            alu_start <= 1'b0;
            alu_op    <= 2'd0;
            alu_a     <= 14'd0;
            alu_b     <= 14'd0;

            bcd_start <= 1'b0;
            bcd_bin   <= 14'd0;

            digits    <= 16'h0000;
            disp_neg  <= 1'b0;
            disp_err  <= 1'b0;
        end else begin
            alu_start <= 1'b0;
            bcd_start <= 1'b0;

            if (bcd_done) begin
                digits <= bcd_digits;
            end

            if (key_valid) begin
                if (key == KEY_C) begin
                    // 全部清零，回到输入 A
                    acc      <= 14'd0;
                    ndig     <= 3'd0;
                    a_reg    <= 14'd0;
                    op_reg   <= 2'd0;
                    res_mag  <= 14'd0;
                    res_neg  <= 1'b0;
                    err_flag <= 1'b0;
                    disp_neg <= 1'b0;
                    disp_err <= 1'b0;
                    digits   <= 16'h0000;
                    bcd_start<= 1'b1;
                    bcd_bin  <= 14'd0;
                    state    <= S_INPUT_A;

                end else if (key <= 4'd9) begin
                    // 数字键
                    if (state == S_RESULT || state == S_ERROR) begin
                        acc      <= {10'b0, key};
                        ndig     <= (key == 4'd0) ? 3'd0 : 3'd1;
                        a_reg    <= 14'd0;
                        op_reg   <= 2'd0;
                        res_mag  <= 14'd0;
                        res_neg  <= 1'b0;
                        err_flag <= 1'b0;
                        disp_neg <= 1'b0;
                        disp_err <= 1'b0;
                        bcd_start<= 1'b1;
                        bcd_bin  <= {10'b0, key};
                        state    <= S_INPUT_A;
                    end else if (ndig < 3'd4) begin
                        acc      <= acc_x10[13:0];
                        // 前导零不占用 4 位输入额度
                        ndig     <= (acc_x10[13:0] == 14'd0)
                                    ? 3'd0 : (ndig + 3'd1);
                        bcd_start<= 1'b1;
                        bcd_bin  <= acc_x10[13:0];
                    end

                end else if (key <= KEY_DIV) begin
                    // 运算符 + - * /
                    if (state == S_INPUT_A || state == S_INPUT_B ||
                        state == S_RESULT) begin
                        if (state == S_RESULT && res_neg) begin
                            // 负数结果不能直接作为下一步操作数
                            err_flag <= 1'b1;
                            disp_err <= 1'b1;
                            disp_neg <= 1'b0;
                            state    <= S_ERROR;
                        end else begin
                            if (state == S_INPUT_A) begin
                                a_reg <= acc;
                            end else if (state == S_RESULT) begin
                                a_reg <= res_mag;
                            end
                            op_reg   <= new_op;
                            acc      <= 14'd0;
                            ndig     <= 3'd0;
                            res_mag  <= 14'd0;
                            res_neg  <= 1'b0;
                            err_flag <= 1'b0;
                            disp_neg <= 1'b0;
                            disp_err <= 1'b0;
                            digits   <= 16'h0000;
                            bcd_start<= 1'b1;
                            bcd_bin  <= 14'd0;
                            state    <= S_INPUT_B;
                        end
                    end

                end else if (key == KEY_EQ) begin
                    // 等号
                    if (state == S_INPUT_B && !alu_busy) begin
                        alu_a     <= a_reg;
                        alu_b     <= acc;
                        alu_op    <= op_reg;
                        alu_start <= 1'b1;
                        state     <= S_WAIT_ALU;
                    end else if (state == S_INPUT_A) begin
                        res_mag  <= acc;
                        res_neg  <= 1'b0;
                        err_flag <= 1'b0;
                        disp_neg <= 1'b0;
                        disp_err <= 1'b0;
                        bcd_start<= 1'b1;
                        bcd_bin  <= acc;
                        state    <= S_RESULT;
                    end
                end
            end

            if (state == S_WAIT_ALU && alu_done) begin
                if (alu_err) begin
                    err_flag <= 1'b1;
                    disp_err <= 1'b1;
                    disp_neg <= 1'b0;
                    state    <= S_ERROR;
                end else begin
                    res_mag  <= alu_res;
                    res_neg  <= alu_neg;
                    err_flag <= 1'b0;
                    disp_neg <= alu_neg;
                    disp_err <= 1'b0;
                    bcd_start<= 1'b1;
                    bcd_bin  <= alu_res;
                    state    <= S_RESULT;
                end
            end
        end
    end

endmodule
