// ============================================================
// 按键去抖 / 单次触发
//
// 输入是键盘扫描器每帧一次的采样（默认 1 kHz）：
//   - 同一键连续 SAMPLES 帧稳定按下才输出 key_valid（单周期）
//   - 按下期间只输出一次，必须稳定释放 SAMPLES 帧后才重新武装
//   - 采样中途换键会重新计数，短毛刺被丢弃
//
// SAMPLES = 10 时，1 kHz 采样对应约 10 ms 去抖。
// ============================================================
module key_debounce #(
    parameter integer SAMPLES = 10
) (
    input  wire       clk,
    input  wire       rst_n,
    input  wire       sample_valid,
    input  wire [3:0] sample_code,
    input  wire       sample_pressed,
    output reg        key_valid,
    output reg  [3:0] key_code
);

    reg [3:0]  cand_code;
    reg [15:0] stable_cnt;
    reg [15:0] release_cnt;
    reg        latched;

    // 本次采样后，候选键已经连续稳定的帧数
    wire [15:0] next_stable = (stable_cnt != 16'd0 && sample_code == cand_code)
                              ? (stable_cnt + 16'd1)
                              : 16'd1;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            key_valid   <= 1'b0;
            key_code    <= 4'h0;
            cand_code   <= 4'h0;
            stable_cnt  <= 16'd0;
            release_cnt <= 16'd0;
            latched     <= 1'b0;
        end else begin
            key_valid <= 1'b0;

            if (sample_valid) begin
                if (!latched) begin
                    if (sample_pressed) begin
                        if (stable_cnt != 16'd0 && sample_code == cand_code) begin
                            stable_cnt <= stable_cnt + 16'd1;
                        end else begin
                            cand_code  <= sample_code;
                            stable_cnt <= 16'd1;
                        end

                        if (next_stable >= SAMPLES) begin
                            key_valid   <= 1'b1;
                            key_code    <= sample_code;
                            latched     <= 1'b1;
                            stable_cnt  <= 16'd0;
                            release_cnt <= 16'd0;
                        end
                    end else begin
                        stable_cnt  <= 16'd0;
                        release_cnt <= 16'd0;
                    end
                end else begin
                    // 已输出过一次，等待稳定释放后才允许下一次按键
                    if (sample_pressed) begin
                        release_cnt <= 16'd0;
                    end else if (release_cnt + 16'd1 >= SAMPLES) begin
                        latched     <= 1'b0;
                        release_cnt <= 16'd0;
                        stable_cnt  <= 16'd0;
                    end else begin
                        release_cnt <= release_cnt + 16'd1;
                    end
                end
            end
        end
    end

endmodule
