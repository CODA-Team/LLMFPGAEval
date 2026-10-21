// ============================================================
// 实验4：反应速度测试器 —— 板级完整 RTL
//
// 板卡：DMZ-EG4S20（EG4S20BG256，50 MHz）
//
// 本文件由 work/exp4_top/final_rtl.v 派生，针对真实硬件做了以下修正：
//   1. display_driver 改为 4 位数码管动态扫描：
//      seg[7:0] = {dp,g,f,e,d,c,b,a}（高有效）
//      digit_sel[3:0]（低有效，[0] 为最右位，对应板上 COM4）
//   2. reaction_fsm 增加 binary -> BCD 转换。原实现把二进制 ms_value
//      直接按 BCD 半字节送显示，除少数数字外都会显示错误。
//   3. WAIT_DELAY 每拍清 display_value，新一轮开始时不再残留旧结果。
//   4. delay_generator 改为"先随机整秒再乘 CLK_FREQ"。原来的 16 位 LFSR
//      对 (MAX-MIN)*CLK_FREQ 取模，在 50 MHz 下模数远大于 65535，
//      实际延迟只有 1.0000~1.0013 秒，无法覆盖 1~5 秒。
//   5. 新增板级封装 reaction_timer_board：
//      - rst_n / 按键低有效
//      - 4x4 矩阵键盘扫描，取 Row0/Col0 为 start，Row0/Col1 为 response
//      - 核心 reaction_timer_top 保持高有效、纯逻辑接口
//   6. 上板健壮性：display_driver 换位前后消隐（防鬼影）；
//      key_col 加两级同步器；去抖窗口取 10 ms（DEBOUNCE_CNT=500000）。
//
// 注意：矩阵键盘列线依赖板上外部上拉。若实际板卡没有上拉，需要在 TD
//       中对 key_col 配置内部上拉，否则列线悬空、读不到按键。
// ============================================================

`timescale 1ns/1ps

// ------------------------------------------------------------
// 1. 按键同步 + 去抖（高有效输入）
// ------------------------------------------------------------
module input_sync_debounce #(
    parameter DEBOUNCE_CNT = 500000   // 10 ms @ 50 MHz
) (
    input  wire clk,
    input  wire reset,
    input  wire start_button,
    input  wire response_button,
    output reg  start_pulse,
    output reg  response_pulse
);

    reg [1:0] start_sync;
    reg [1:0] response_sync;

    reg [19:0] start_cnt;
    reg [19:0] response_cnt;
    reg        start_debounce;
    reg        response_debounce;
    reg        start_debounce_d;
    reg        response_debounce_d;

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            start_sync    <= 2'b00;
            response_sync <= 2'b00;
        end else begin
            start_sync    <= {start_sync[0], start_button};
            response_sync <= {response_sync[0], response_button};
        end
    end

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            start_cnt      <= 20'd0;
            start_debounce <= 1'b0;
        end else begin
            if (start_sync[1] != start_debounce) begin
                if (start_cnt >= DEBOUNCE_CNT[19:0]) begin
                    start_debounce <= start_sync[1];
                    start_cnt      <= 20'd0;
                end else begin
                    start_cnt <= start_cnt + 20'd1;
                end
            end else begin
                start_cnt <= 20'd0;
            end
        end
    end

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            response_cnt      <= 20'd0;
            response_debounce <= 1'b0;
        end else begin
            if (response_sync[1] != response_debounce) begin
                if (response_cnt >= DEBOUNCE_CNT[19:0]) begin
                    response_debounce <= response_sync[1];
                    response_cnt      <= 20'd0;
                end else begin
                    response_cnt <= response_cnt + 20'd1;
                end
            end else begin
                response_cnt <= 20'd0;
            end
        end
    end

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            start_debounce_d    <= 1'b0;
            response_debounce_d <= 1'b0;
            start_pulse         <= 1'b0;
            response_pulse      <= 1'b0;
        end else begin
            start_debounce_d    <= start_debounce;
            response_debounce_d <= response_debounce;
            start_pulse         <= start_debounce & ~start_debounce_d;
            response_pulse      <= response_debounce & ~response_debounce_d;
        end
    end

endmodule

// ------------------------------------------------------------
// 2. 随机延迟发生器（1~5 秒，LFSR）
// ------------------------------------------------------------
module delay_generator #(
    parameter CLK_FREQ   = 50000000,
    parameter MIN_DELAY  = 1,
    parameter MAX_DELAY  = 5,
    parameter LFSR_INIT  = 16'hACE1
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        start,
    output reg         delay_done
);

    reg [15:0] lfsr;
    reg [31:0] delay_counter;
    reg [31:0] delay_target;
    reg        running;
    reg [3:0]  sec_sel;
    reg        calc_pending;

    wire lfsr_feedback = lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10];

    // 随机范围按【整秒】取，而不是按周期取。
    // 原实现:
    //   delay_target = MIN_DELAY*CLK_FREQ + lfsr % ((MAX-MIN)*CLK_FREQ + 1)
    // 在 50 MHz、1~5 秒时模数是 200,000,001，16 位 LFSR 最大 65535，
    // 取模结果恒等于 lfsr 本身，实际延迟只有 1.0000~1.0013 秒。
    // 改成先随机秒数再乘 CLK_FREQ，既符合"1~5 秒"的需求，也避免
    // 综合出大尺寸取模电路。
    localparam integer SEC_RANGE = MAX_DELAY - MIN_DELAY + 1;

    // 随机秒数只用 3 位，并用条件减法代替取模：
    // 16 位 lfsr % 5 在 TD 里综合成多级比较/减法链，50 MHz 下
    // 路径约 52 ns（WNS = -32 ns）。3 位映射的分布略不均匀
    // （0/1/2 概率略高），对 1~5 秒的随机等待足够。
    // 要求 3 <= SEC_RANGE <= 8，默认 1~5 秒满足。
    wire [2:0] rand3 = lfsr[2:0];
    wire [2:0] r1 = (rand3 >= SEC_RANGE) ? (rand3 - SEC_RANGE) : rand3;
    wire [2:0] r2 = (r1    >= SEC_RANGE) ? (r1    - SEC_RANGE) : r1;
    wire [3:0] sec_sel_next = MIN_DELAY + r2;

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            lfsr <= LFSR_INIT;
        end else begin
            lfsr <= {lfsr[14:0], lfsr_feedback};
        end
    end

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            running       <= 1'b0;
            calc_pending  <= 1'b0;
            sec_sel       <= 4'd0;
            delay_counter <= 32'd0;
            delay_target  <= 32'd0;
            delay_done    <= 1'b0;
        end else begin
            delay_done <= 1'b0;
            if (start && !running && !calc_pending) begin
                // 第一拍只锁存随机秒数，把取模/条件减的路径收在 4 位上
                sec_sel      <= sec_sel_next;
                calc_pending <= 1'b1;
            end else if (calc_pending) begin
                // 第二拍再乘 CLK_FREQ，避免"取模 + 乘法"串成一条长路径
                delay_target  <= sec_sel * CLK_FREQ;
                delay_counter <= 32'd0;
                calc_pending  <= 1'b0;
                running       <= 1'b1;
            end else if (running) begin
                if (delay_counter >= delay_target) begin
                    running    <= 1'b0;
                    delay_done <= 1'b1;
                end else begin
                    delay_counter <= delay_counter + 32'd1;
                end
            end
        end
    end

endmodule

// ------------------------------------------------------------
// 3. 毫秒计数器（二进制）
// ------------------------------------------------------------
module ms_counter #(
    parameter CLK_FREQ = 50000000,
    parameter MS_DIV   = 50000,
    parameter MAX_MS   = 9999
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        enable,
    input  wire        clear,
    output reg  [13:0] ms_value
);

    reg [15:0] div_cnt;

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            div_cnt  <= 16'd0;
            ms_value <= 14'd0;
        end else if (clear) begin
            div_cnt  <= 16'd0;
            ms_value <= 14'd0;
        end else if (enable) begin
            if (div_cnt >= MS_DIV - 1) begin
                div_cnt <= 16'd0;
                if (ms_value < MAX_MS)
                    ms_value <= ms_value + 14'd1;
            end else begin
                div_cnt <= div_cnt + 16'd1;
            end
        end
    end

endmodule

// ------------------------------------------------------------
// 4. 4 位数码管动态扫描驱动
//    display_value 为 16 位 BCD：{千,百,十,个}；0xFFFF 表示 "FAIL"
// ------------------------------------------------------------
module display_driver #(
    parameter SCAN_DIV = 50000   // 每位点亮周期数；50 MHz 下 50000 = 1 ms
) (
    input  wire        clk,
    input  wire        reset,
    input  wire [15:0] display_value,
    output wire [7:0]  seg,        // {dp,g,f,e,d,c,b,a}，高有效
    output wire [3:0]  digit_sel   // 低有效，[0] = 最右位
);

    // 换位消隐：每位开始和结束各熄灭 BLANK_CYCLES 拍，避免旧位段码
    // 在位选切换的偏斜窗口里点亮新位造成鬼影。50 MHz 下 16 拍 = 320 ns。
    // 仿真里 SCAN_DIV 很小（例如 4），所以用 1 拍。
    localparam BLANK_CYCLES = (SCAN_DIV >= 64) ? 16 : 1;

    reg [15:0] scan_cnt;
    reg [1:0]  scan_idx;

    reg [3:0] d0, d1, d2, d3;
    always @(*) begin
        if (display_value == 16'hFFFF) begin
            d3 = 4'hF;
            d2 = 4'hA;
            d1 = 4'h1;
            d0 = 4'h1;
        end else begin
            d3 = display_value[15:12];
            d2 = display_value[11:8];
            d1 = display_value[7:4];
            d0 = display_value[3:0];
        end
    end

    // 标准共阴数码管段码：bit0=a ... bit6=g
    function [6:0] bcd_to_seg7;
        input [3:0] bcd;
        begin
            case (bcd)
                4'd0: bcd_to_seg7 = 7'b0111111;
                4'd1: bcd_to_seg7 = 7'b0000110;
                4'd2: bcd_to_seg7 = 7'b1011011;
                4'd3: bcd_to_seg7 = 7'b1001111;
                4'd4: bcd_to_seg7 = 7'b1100110;
                4'd5: bcd_to_seg7 = 7'b1101101;
                4'd6: bcd_to_seg7 = 7'b1111101;
                4'd7: bcd_to_seg7 = 7'b0000111;
                4'd8: bcd_to_seg7 = 7'b1111111;
                4'd9: bcd_to_seg7 = 7'b1101111;
                default: bcd_to_seg7 = 7'b0000000;  // 非法 BCD 位熄灭
            endcase
        end
    endfunction

    reg [6:0] cur_seg7;
    always @(*) begin
        case (scan_idx)
            2'd0: cur_seg7 = bcd_to_seg7(d0);
            2'd1: cur_seg7 = bcd_to_seg7(d1);
            2'd2: cur_seg7 = bcd_to_seg7(d2);
            default: cur_seg7 = bcd_to_seg7(d3);
        endcase

        // 0xFFFF 显示 "FAIL"：左起 F A 1 1
        if (display_value == 16'hFFFF) begin
            case (scan_idx)
                2'd0: cur_seg7 = 7'b0000110;  // 1
                2'd1: cur_seg7 = 7'b0000110;  // 1
                2'd2: cur_seg7 = 7'b1110111;  // A
                default: cur_seg7 = 7'b1110001; // F
            endcase
        end
    end

    wire blank = (scan_cnt < BLANK_CYCLES) ||
                 (scan_cnt >= SCAN_DIV - BLANK_CYCLES);

    assign seg       = blank ? 8'b00000000 : {1'b0, cur_seg7};
    assign digit_sel = ~(4'b0001 << scan_idx);

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            scan_cnt <= 16'd0;
            scan_idx <= 2'd0;
        end else if (scan_cnt >= SCAN_DIV - 1) begin
            scan_cnt <= 16'd0;
            scan_idx <= scan_idx + 2'd1;
        end else begin
            scan_cnt <= scan_cnt + 16'd1;
        end
    end

endmodule

// ------------------------------------------------------------
// 4.5 二进制 -> BCD 顺序转换器
//
// 组合版 double-dabble 是 14 级"比较 + 加 3 + 移位"，在 50 MHz 下
// 综合出来的路径约 52 ns（WNS = -32 ns），必须拆成多拍。
// 这里每拍做一级，14 拍完成；输入稳定后约 15 拍输出有效。
// 输入为 14 位、输出 4 位 BCD（0~9999），更高输入会溢出 4 位 BCD。
// ------------------------------------------------------------
module bin_to_bcd_seq #(
    parameter WIDTH = 14
) (
    input  wire             clk,
    input  wire             reset,
    input  wire [WIDTH-1:0] din,
    output reg  [15:0]      dout,
    output reg              valid
);

    reg [WIDTH-1:0] din_d;
    reg [29:0]      shift;
    reg [4:0]       iter;

    // 一级 double-dabble：四个 BCD 位段先判 >=5 加 3，再整体左移。
    function [29:0] dabble;
        input [29:0] s;
        reg [29:0] t;
        begin
            t = s;
            if (t[17:14] >= 4'd5) t[17:14] = t[17:14] + 4'd3;
            if (t[21:18] >= 4'd5) t[21:18] = t[21:18] + 4'd3;
            if (t[25:22] >= 4'd5) t[25:22] = t[25:22] + 4'd3;
            if (t[29:26] >= 4'd5) t[29:26] = t[29:26] + 4'd3;
            dabble = t << 1;
        end
    endfunction

    wire [29:0] shift_next = dabble(shift);

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            din_d <= {WIDTH{1'b0}};
            shift <= 30'd0;
            iter  <= 5'd0;
            dout  <= 16'd0;
            valid <= 1'b0;
        end else if (din != din_d) begin
            // 输入变化，重新开始转换
            din_d <= din;
            shift <= {16'd0, din};
            iter  <= 5'd0;
            dout  <= 16'd0;
            valid <= 1'b0;
        end else if (iter < WIDTH) begin
            shift <= shift_next;
            iter  <= iter + 5'd1;
            if (iter == WIDTH - 1) begin
                dout  <= shift_next[29:14];
                valid <= 1'b1;
            end
        end
    end

endmodule

// ------------------------------------------------------------
// 5. 反应状态机
// ------------------------------------------------------------
module reaction_fsm #(
    parameter CLK_FREQ  = 50000000,
    parameter FAIL_TIME = 2,
    parameter MAX_MS    = 9999
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        start_pulse,
    input  wire        response_pulse,
    input  wire        delay_done,
    input  wire [13:0] ms_value,
    output reg         ready_led,
    output reg  [15:0] display_value,
    output reg         delay_start,
    output reg         ms_enable,
    output reg         ms_clear
);

    localparam IDLE           = 3'b000;
    localparam WAIT_DELAY     = 3'b001;
    localparam READY          = 3'b010;
    localparam DISPLAY_RESULT = 3'b011;
    localparam FALSE_START    = 3'b100;

    reg [2:0]  state;
    reg [13:0] result_latch;
    reg [31:0] fail_counter;

    wire [31:0] fail_target = FAIL_TIME * CLK_FREQ;

    // 二进制 -> BCD 改成顺序转换（见 bin_to_bcd_seq）。
    // 组合 double-dabble 的 14 级路径在 50 MHz 下时序不收敛。
    wire [15:0] bcd_ms;
    wire [15:0] bcd_result;
    wire        bcd_ms_valid;
    wire        bcd_result_valid;

    bin_to_bcd_seq #(.WIDTH(14)) u_bcd_ms (
        .clk   (clk),
        .reset (reset),
        .din   (ms_value),
        .dout  (bcd_ms),
        .valid (bcd_ms_valid)
    );

    bin_to_bcd_seq #(.WIDTH(14)) u_bcd_result (
        .clk   (clk),
        .reset (reset),
        .din   (result_latch),
        .dout  (bcd_result),
        .valid (bcd_result_valid)
    );

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            state         <= IDLE;
            ready_led     <= 1'b0;
            display_value <= 16'h0000;
            delay_start   <= 1'b0;
            ms_enable     <= 1'b0;
            ms_clear      <= 1'b0;
            result_latch  <= 14'd0;
            fail_counter  <= 32'd0;
        end else begin
            delay_start <= 1'b0;
            ms_clear    <= 1'b0;

            case (state)
                IDLE: begin
                    ready_led     <= 1'b0;
                    ms_enable     <= 1'b0;
                    display_value <= 16'h0000;
                    if (start_pulse) begin
                        state       <= WAIT_DELAY;
                        delay_start <= 1'b1;
                        ms_clear    <= 1'b1;
                    end
                end

                WAIT_DELAY: begin
                    ready_led     <= 1'b0;
                    ms_enable     <= 1'b0;
                    // 新一轮测量开始时清屏，避免残留上一轮结果
                    display_value <= 16'h0000;
                    if (response_pulse) begin
                        state         <= FALSE_START;
                        fail_counter  <= 32'd0;
                        display_value <= 16'hFFFF;
                    end else if (delay_done) begin
                        state     <= READY;
                        ready_led <= 1'b1;
                        ms_enable <= 1'b1;
                        ms_clear  <= 1'b1;
                    end
                end

                READY: begin
                    ready_led <= 1'b1;
                    ms_enable <= 1'b1;
                    if (response_pulse) begin
                        result_latch  <= ms_value;
                        state         <= DISPLAY_RESULT;
                        ready_led     <= 1'b0;
                        ms_enable     <= 1'b0;
                        // bcd_ms 是顺序转换的稳态值；转换中则先保持原值
                        if (bcd_ms_valid)
                            display_value <= bcd_ms;
                    end
                end

                DISPLAY_RESULT: begin
                    ready_led     <= 1'b0;
                    ms_enable     <= 1'b0;
                    // result_latch 变化后约 15 拍 BCD 才有效，此前保持旧值
                    if (bcd_result_valid)
                        display_value <= bcd_result;
                    if (start_pulse) begin
                        state       <= WAIT_DELAY;
                        delay_start <= 1'b1;
                        ms_clear    <= 1'b1;
                    end
                end

                FALSE_START: begin
                    ready_led     <= 1'b0;
                    ms_enable     <= 1'b0;
                    display_value <= 16'hFFFF;
                    if (fail_counter >= fail_target) begin
                        state        <= IDLE;
                        fail_counter <= 32'd0;
                    end else begin
                        fail_counter <= fail_counter + 32'd1;
                    end
                end

                default: begin
                    state <= IDLE;
                end
            endcase
        end
    end

endmodule

// ------------------------------------------------------------
// 6. 顶层逻辑（高有效按键/复位，扫描显示接口）
// ------------------------------------------------------------
module reaction_timer_top #(
    parameter CLK_FREQ     = 50000000,
    parameter DEBOUNCE_CNT = 500000,   // 10 ms @ 50 MHz
    parameter MIN_DELAY    = 1,
    parameter MAX_DELAY    = 5,
    parameter MS_DIV       = 50000,
    parameter MAX_MS       = 9999,
    parameter FAIL_TIME    = 2,
    parameter SCAN_DIV     = 50000
) (
    input  wire        clk,
    input  wire        reset,
    input  wire        start_button,
    input  wire        response_button,
    output wire        ready_led,
    output wire [7:0]  seg,
    output wire [3:0]  digit_sel
);

    wire        start_pulse;
    wire        response_pulse;
    wire        delay_done;
    wire        delay_start;
    wire        ms_enable;
    wire        ms_clear;
    wire [13:0] ms_value;
    wire [15:0] display_value;

    input_sync_debounce #(
        .DEBOUNCE_CNT(DEBOUNCE_CNT)
    ) u_input_sync_debounce (
        .clk             (clk),
        .reset           (reset),
        .start_button    (start_button),
        .response_button (response_button),
        .start_pulse     (start_pulse),
        .response_pulse  (response_pulse)
    );

    delay_generator #(
        .CLK_FREQ  (CLK_FREQ),
        .MIN_DELAY (MIN_DELAY),
        .MAX_DELAY (MAX_DELAY)
    ) u_delay_generator (
        .clk        (clk),
        .reset      (reset),
        .start      (delay_start),
        .delay_done (delay_done)
    );

    ms_counter #(
        .CLK_FREQ (CLK_FREQ),
        .MS_DIV   (MS_DIV),
        .MAX_MS   (MAX_MS)
    ) u_ms_counter (
        .clk      (clk),
        .reset    (reset),
        .enable   (ms_enable),
        .clear    (ms_clear),
        .ms_value (ms_value)
    );

    display_driver #(
        .SCAN_DIV (SCAN_DIV)
    ) u_display_driver (
        .clk           (clk),
        .reset         (reset),
        .display_value (display_value),
        .seg           (seg),
        .digit_sel     (digit_sel)
    );

    reaction_fsm #(
        .CLK_FREQ  (CLK_FREQ),
        .FAIL_TIME (FAIL_TIME),
        .MAX_MS    (MAX_MS)
    ) u_reaction_fsm (
        .clk            (clk),
        .reset          (reset),
        .start_pulse    (start_pulse),
        .response_pulse (response_pulse),
        .delay_done     (delay_done),
        .ms_value       (ms_value),
        .ready_led      (ready_led),
        .display_value  (display_value),
        .delay_start    (delay_start),
        .ms_enable      (ms_enable),
        .ms_clear       (ms_clear)
    );

endmodule

// ------------------------------------------------------------
// 7. 4x4 矩阵键盘扫描
//    - 行线逐个拉低（低有效），列线外部上拉、按下为低
//    - 每个行槽结束时采样一次列线，并保持到下一次同一行
//    - 输出 active-high 的 start_button / response_button
// ------------------------------------------------------------
module matrix_key_scanner #(
    parameter SCAN_DIV  = 12500,   // 每个行槽的时钟数；50 MHz 下 0.25 ms（整轮 1 ms）
    parameter START_ROW = 0,
    parameter START_COL = 0,
    parameter RESP_ROW  = 0,
    parameter RESP_COL  = 1
) (
    input  wire       clk,
    input  wire       reset,
    output wire [3:0] key_row,
    input  wire [3:0] key_col,
    output reg        start_button,
    output reg        response_button
);

    reg [15:0] scan_cnt;
    reg [1:0]  row_idx;
    reg [3:0]  row_state [0:3];

    integer i;

    assign key_row = ~(4'b0001 << row_idx);

    always @(posedge clk or posedge reset) begin
        if (reset) begin
            scan_cnt        <= 16'd0;
            row_idx         <= 2'd0;
            start_button    <= 1'b0;
            response_button <= 1'b0;
            for (i = 0; i < 4; i = i + 1)
                row_state[i] <= 4'd0;
        end else begin
            if (scan_cnt >= SCAN_DIV - 1) begin
                scan_cnt <= 16'd0;
                row_state[row_idx] <= ~key_col;
                row_idx <= row_idx + 2'd1;
            end else begin
                scan_cnt <= scan_cnt + 16'd1;
            end

            start_button    <= row_state[START_ROW][START_COL];
            response_button <= row_state[RESP_ROW][RESP_COL];
        end
    end

endmodule

// ------------------------------------------------------------
// 8. 板级封装
//    rst_n 低有效；矩阵键盘 Row0/Col0 = start，Row0/Col1 = response
// ------------------------------------------------------------
module reaction_timer_board #(
    parameter CLK_FREQ     = 50000000,
    parameter DEBOUNCE_CNT = 500000,   // 10 ms @ 50 MHz
    parameter MIN_DELAY    = 1,
    parameter MAX_DELAY    = 5,
    parameter MS_DIV       = 50000,
    parameter MAX_MS       = 9999,
    parameter FAIL_TIME    = 2,
    parameter SCAN_DIV     = 50000,   // 数码管每位 1 ms
    parameter KEY_SCAN_DIV = 12500    // 矩阵键盘每行 0.25 ms（整轮 1 ms）
) (
    input  wire       clk,
    input  wire       rst_n,       // 低有效复位
    output wire [3:0] key_row,     // 矩阵键盘行输出，低有效
    input  wire [3:0] key_col,     // 矩阵键盘列输入，按下为低
    output wire       ready_led,
    output wire [7:0] seg,
    output wire [3:0] digit_sel
);

    // 异步置位、同步释放的复位同步器
    reg [1:0] rst_sync;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n)
            rst_sync <= 2'b00;
        else
            rst_sync <= {rst_sync[0], 1'b1};
    end

    wire reset = ~rst_sync[1];

    // key_col 是异步输入，先过两级同步器再进扫描器，避免亚稳态。
    // 复位时列线按上拉约定置为高（未按下）。
    reg [3:0] key_col_sync0;
    reg [3:0] key_col_sync1;
    always @(posedge clk or posedge reset) begin
        if (reset) begin
            key_col_sync0 <= 4'b1111;
            key_col_sync1 <= 4'b1111;
        end else begin
            key_col_sync0 <= key_col;
            key_col_sync1 <= key_col_sync0;
        end
    end

    wire start_button;
    wire response_button;

    matrix_key_scanner #(
        .SCAN_DIV  (KEY_SCAN_DIV),
        .START_ROW (0),
        .START_COL (0),
        .RESP_ROW  (0),
        .RESP_COL  (1)
    ) u_matrix_key_scanner (
        .clk             (clk),
        .reset           (reset),
        .key_row         (key_row),
        .key_col         (key_col_sync1),
        .start_button    (start_button),
        .response_button (response_button)
    );

    reaction_timer_top #(
        .CLK_FREQ     (CLK_FREQ),
        .DEBOUNCE_CNT (DEBOUNCE_CNT),
        .MIN_DELAY    (MIN_DELAY),
        .MAX_DELAY    (MAX_DELAY),
        .MS_DIV       (MS_DIV),
        .MAX_MS       (MAX_MS),
        .FAIL_TIME    (FAIL_TIME),
        .SCAN_DIV     (SCAN_DIV)
    ) u_dut (
        .clk             (clk),
        .reset           (reset),
        .start_button    (start_button),
        .response_button (response_button),
        .ready_led       (ready_led),
        .seg             (seg),
        .digit_sel       (digit_sel)
    );

endmodule
