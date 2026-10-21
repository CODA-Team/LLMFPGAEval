// ============================================================
// 实验4：reaction_timer_top 自检 Testbench
//
// 设计目标（与 RTL 实现独立验证）：
//   1. 复位后 ready_led=0、display=0000
//   2. 短于去抖窗口的按键毛刺被忽略
//   3. 启动后 ready_led 在 [MIN_DELAY, MAX_DELAY] 内拉高
//   4. READY 前按响应 -> FAIL（0xFFFF），FAIL_TIME 后回 IDLE
//   5. READY 后按响应 -> 显示值 = 实测 ms 的 BCD，且结果保持
//   6. 结果态重新启动 -> 清屏并重新测量
//   7. 长时间不响应 -> ms 封顶 MAX_MS
//   8. 随机按键 2000 拍输出无 X/Z
//   9. 数码管动态扫描：位选 one-hot-low、段码与 BCD 一致、4 位都扫到
//
// 仿真参数取小值以缩短时间；比例与上板配置一致。
// ============================================================

`timescale 1ns/1ps

module tb_reaction_timer_top;

    // ---------------- 参数 ----------------
    localparam CLK_FREQ     = 1000;
    localparam DEBOUNCE_CNT = 5;
    localparam MIN_DELAY    = 1;
    localparam MAX_DELAY    = 5;
    localparam MS_DIV       = 10;
    localparam MAX_MS       = 200;
    localparam FAIL_TIME    = 1;
    localparam SCAN_DIV     = 4;
    localparam CLK_PERIOD   = 10;

    // ---------------- DUT 信号 ----------------
    reg         clk;
    reg         reset;
    reg         start_button;
    reg         response_button;
    wire        ready_led;
    wire [7:0]  seg;
    wire [3:0]  digit_sel;

    reaction_timer_top #(
        .CLK_FREQ     (CLK_FREQ),
        .DEBOUNCE_CNT (DEBOUNCE_CNT),
        .MIN_DELAY    (MIN_DELAY),
        .MAX_DELAY    (MAX_DELAY),
        .MS_DIV       (MS_DIV),
        .MAX_MS       (MAX_MS),
        .FAIL_TIME    (FAIL_TIME),
        .SCAN_DIV     (SCAN_DIV)
    ) dut (
        .clk             (clk),
        .reset           (reset),
        .start_button    (start_button),
        .response_button (response_button),
        .ready_led       (ready_led),
        .seg             (seg),
        .digit_sel       (digit_sel)
    );

    // 独立的 delay_generator 实例：用较低 CLK_FREQ 放大"秒"的周期数，
    // 以便在 20 轮内验证 1~5 秒范围是否真的覆盖到 5 秒。
    reg  delay_start_chk;
    wire delay_done_chk;

    delay_generator #(
        .CLK_FREQ  (20000),
        .MIN_DELAY (1),
        .MAX_DELAY (5)
    ) u_delay_chk (
        .clk        (clk),
        .reset      (reset),
        .start      (delay_start_chk),
        .delay_done (delay_done_chk)
    );

    `include "tb_common.vh"

    // ---------------- 时钟 ----------------
    initial begin
        clk = 1'b0;
        forever #(CLK_PERIOD/2) clk = ~clk;
    end

    // ---------------- 看门狗 ----------------
    initial begin
        #100000000;
        $display("ERROR: TIMEOUT");
        $fatal(1, "TEST_FAILED");
    end

    // ---------------- ms_counter 参考模型 + scoreboard ----------------
    reg [15:0] ref_div;
    reg [13:0] ref_ms;

    always @(posedge clk) begin
        if (reset) begin
            ref_div <= 16'd0;
            ref_ms  <= 14'd0;
        end else if (dut.u_reaction_fsm.ms_clear) begin
            ref_div <= 16'd0;
            ref_ms  <= 14'd0;
        end else if (dut.u_reaction_fsm.ms_enable) begin
            if (ref_div >= MS_DIV - 1) begin
                ref_div <= 16'd0;
                if (ref_ms < MAX_MS)
                    ref_ms <= ref_ms + 14'd1;
            end else begin
                ref_div <= ref_div + 16'd1;
            end
        end
    end

    always @(negedge clk) begin
        if (!reset && dut.ms_value !== ref_ms) begin
            $display("ERROR: ms_counter mismatch dut=%0d ref=%0d", dut.ms_value, ref_ms);
            $fatal(1, "TEST_FAILED");
        end
    end

    // ---------------- 通用任务 ----------------
    task do_reset;
        begin
            @(negedge clk);
            reset           = 1'b1;
            start_button    = 1'b0;
            response_button = 1'b0;
            repeat (4) @(posedge clk);
            @(negedge clk);
            reset = 1'b0;
            repeat (2) @(posedge clk);
        end
    endtask

    task press_start;
        input integer cycles;
        begin
            @(negedge clk);
            start_button = 1'b1;
            repeat (cycles) @(posedge clk);
            @(negedge clk);
            start_button = 1'b0;
        end
    endtask

    task press_response;
        input integer cycles;
        begin
            @(negedge clk);
            response_button = 1'b1;
            repeat (cycles) @(posedge clk);
            @(negedge clk);
            response_button = 1'b0;
        end
    endtask

    task wait_ready;
        input integer timeout;
        integer n;
        begin
            n = 0;
            while (ready_led !== 1'b1 && n < timeout) begin
                @(posedge clk);
                n = n + 1;
            end
            if (ready_led !== 1'b1) begin
                $display("ERROR: ready_led timeout after %0d cycles", timeout);
                $fatal(1, "TEST_FAILED");
            end
        end
    endtask

    task wait_state;
        input [2:0] target;
        input integer timeout;
        integer n;
        begin
            n = 0;
            while (dut.u_reaction_fsm.state !== target && n < timeout) begin
                @(posedge clk);
                n = n + 1;
            end
            if (dut.u_reaction_fsm.state !== target) begin
                $display("ERROR: state timeout, target=%b actual=%b",
                         target, dut.u_reaction_fsm.state);
                $fatal(1, "TEST_FAILED");
            end
        end
    endtask

    task check_digit_sel_known;
        begin
            if (digit_sel !== 4'b1110 && digit_sel !== 4'b1101 &&
                digit_sel !== 4'b1011 && digit_sel !== 4'b0111) begin
                $display("ERROR: digit_sel invalid: %b", digit_sel);
                $fatal(1, "TEST_FAILED");
            end
        end
    endtask

    // ---------------- 测试主流程 ----------------
    integer i;
    integer elapsed_cycles;
    integer timeout;
    integer r;
    integer n;
    integer d;
    integer min_delay;
    integer max_delay;
    time    t_start;
    time    t_ready;
    reg [15:0] saved_display;
    reg [15:0] expected_bcd;

    initial begin
        reset           = 1'b1;
        start_button    = 1'b0;
        response_button = 1'b0;

        // =========================================================
        // T0: 复位
        // =========================================================
        do_reset;
        check1("T0_reset_ready_low", 1'b0, ready_led);
        check16("T0_reset_display_zero", 16'h0000, dut.display_value);
        check_display_scan(16'h0000, 4*SCAN_DIV + 8);

        // =========================================================
        // T1: 短毛刺（< DEBOUNCE_CNT）被忽略
        // =========================================================
        do_reset;
        press_start(1);
        repeat (40) @(posedge clk);
        check1("T1_glitch_ready_low", 1'b0, ready_led);
        check16("T1_glitch_display_zero", 16'h0000, dut.display_value);

        // =========================================================
        // T2: 有效启动 -> ready 延迟落在窗口内
        // =========================================================
        do_reset;
        t_start = 0;
        fork
            press_start(20);
            begin
                while (dut.start_pulse !== 1'b1) @(posedge clk);
                t_start = $time;
                while (ready_led !== 1'b1) @(posedge clk);
                t_ready = $time;
            end
        join

        elapsed_cycles = (t_ready - t_start) / CLK_PERIOD;
        $display("INFO: start_pulse -> ready took %0d cycles", elapsed_cycles);

        check1("T2_ready_high_after_start", 1'b1, ready_led);
        if (elapsed_cycles < MIN_DELAY*CLK_FREQ ||
            elapsed_cycles > MAX_DELAY*CLK_FREQ + 50) begin
            $display("ERROR: delay out of window: %0d cycles (expected %0d..%0d)",
                     elapsed_cycles, MIN_DELAY*CLK_FREQ, MAX_DELAY*CLK_FREQ + 50);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T2_delay_in_window");
        check16("T2_wait_delay_display_zero", 16'h0000, dut.display_value);

        // =========================================================
        // T3: READY 后响应 -> 测量值正确、显示为 BCD、结果保持
        // =========================================================
        // 等待 10 ms（100 个计数周期）再按响应
        repeat (10*MS_DIV) @(posedge clk);
        press_response(20);
        wait_state(3'b011, 200);   // DISPLAY_RESULT
        @(posedge clk);
        // BCD 顺序转换需要 ~15 拍，等它刷新到 display_value
        repeat (30) @(posedge clk);

        check1("T3_ready_low_after_response", 1'b0, ready_led);
        if (dut.u_reaction_fsm.result_latch == 0) begin
            $display("ERROR: measured result is 0");
            $fatal(1, "TEST_FAILED");
        end
        // 粗范围：等待 10 ms + 按键/去抖开销
        if (dut.u_reaction_fsm.result_latch < 5 ||
            dut.u_reaction_fsm.result_latch > 30) begin
            $display("ERROR: measured result out of range: %0d",
                     dut.u_reaction_fsm.result_latch);
            $fatal(1, "TEST_FAILED");
        end
        // 与独立的 ms 参考模型对齐（计数可能多走 1 拍）
        if (!(dut.u_reaction_fsm.result_latch == ref_ms ||
              dut.u_reaction_fsm.result_latch + 1 == ref_ms)) begin
            $display("ERROR: result vs ref_ms mismatch: result=%0d ref_ms=%0d",
                     dut.u_reaction_fsm.result_latch, ref_ms);
            $fatal(1, "TEST_FAILED");
        end
        expected_bcd = ref_bcd(dut.u_reaction_fsm.result_latch);
        check16("T3_display_is_bcd_of_result", expected_bcd, dut.display_value);
        check_display_scan(dut.display_value, 4*SCAN_DIV + 8);
        $display("PASS: T3_measured_ms=%0d", dut.u_reaction_fsm.result_latch);

        // 结果保持
        saved_display = dut.display_value;
        repeat (50) @(posedge clk);
        check16("T3_result_held", saved_display, dut.display_value);

        // =========================================================
        // T4: 结果态重新启动 -> 清屏、重新测量
        // =========================================================
        press_start(20);
        wait_state(3'b001, 100);   // WAIT_DELAY
        @(posedge clk);
        check16("T4_restart_clears_display", 16'h0000, dut.display_value);
        check1("T4_restart_ready_low", 1'b0, ready_led);

        wait_ready(MAX_DELAY*CLK_FREQ + 200);
        check1("T4_ready_again", 1'b1, ready_led);
        press_response(20);
        wait_state(3'b011, 200);
        @(posedge clk);
        repeat (30) @(posedge clk);
        check16("T4_display_is_bcd_of_new_result",
                ref_bcd(dut.u_reaction_fsm.result_latch), dut.display_value);

        // =========================================================
        // T5: READY 前按响应 -> FALSE_START
        // =========================================================
        do_reset;
        press_start(20);
        wait_state(3'b001, 100);   // WAIT_DELAY
        press_response(20);
        wait_state(3'b100, 200);   // FALSE_START

        check16("T5_false_start_display_FAIL", 16'hFFFF, dut.display_value);
        check1("T5_false_start_ready_low", 1'b0, ready_led);
        check_display_scan(16'hFFFF, 4*SCAN_DIV + 8);

        timeout = FAIL_TIME*CLK_FREQ + 200;
        wait_state(3'b000, timeout);   // IDLE
        @(posedge clk);
        check16("T5_false_start_clears_display", 16'h0000, dut.display_value);
        check1("T5_false_start_returns_idle", 1'b1,
                dut.u_reaction_fsm.state == 3'b000);

        // =========================================================
        // T6: 长时间不响应 -> 封顶 MAX_MS
        // =========================================================
        do_reset;
        press_start(20);
        wait_ready(MAX_DELAY*CLK_FREQ + 200);

        repeat (MAX_MS*MS_DIV + 100) @(posedge clk);
        check16("T6_ms_counter_capped", MAX_MS, {2'b00, dut.ms_value});

        press_response(20);
        wait_state(3'b011, 200);
        @(posedge clk);
        repeat (30) @(posedge clk);
        check16("T6_result_capped", MAX_MS,
                {2'b00, dut.u_reaction_fsm.result_latch});
        check16("T6_display_capped_bcd", ref_bcd(MAX_MS),
                dut.display_value);

        // =========================================================
        // T7: READY 中途复位
        // =========================================================
        do_reset;
        press_start(20);
        wait_ready(MAX_DELAY*CLK_FREQ + 200);

        @(negedge clk);
        reset = 1'b1;
        repeat (3) @(posedge clk);
        check1("T7_reset_ready_low", 1'b0, ready_led);
        check16("T7_reset_display_zero", 16'h0000, dut.display_value);
        @(negedge clk);
        reset = 1'b0;
        repeat (2) @(posedge clk);
        check1("T7_recovery_ready_low", 1'b0, ready_led);
        check16("T7_recovery_display_zero", 16'h0000, dut.display_value);
        if (dut.u_reaction_fsm.state !== 3'b000) begin
            $display("ERROR: not IDLE after reset: state=%b", dut.u_reaction_fsm.state);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T7_reset_recovery");

        // =========================================================
        // T8: 响应毛刺被忽略（READY 中）
        // =========================================================
        do_reset;
        press_start(20);
        wait_ready(MAX_DELAY*CLK_FREQ + 200);
        press_response(1);          // < DEBOUNCE_CNT
        repeat (40) @(posedge clk);
        check1("T8_response_glitch_ignored", 1'b1, ready_led);

        // =========================================================
        // T9: 强制已知 BCD 值，验证扫描驱动
        // =========================================================
        force dut.u_reaction_fsm.display_value = 16'h1234;
        repeat (2) @(posedge clk);
        check_display_scan(16'h1234, 4*SCAN_DIV + 8);
        release dut.u_reaction_fsm.display_value;

        force dut.u_reaction_fsm.display_value = 16'h0000;
        repeat (2) @(posedge clk);
        check_display_scan(16'h0000, 4*SCAN_DIV + 8);
        release dut.u_reaction_fsm.display_value;

        force dut.u_reaction_fsm.display_value = 16'h9999;
        repeat (2) @(posedge clk);
        check_display_scan(16'h9999, 4*SCAN_DIV + 8);
        release dut.u_reaction_fsm.display_value;

        force dut.u_reaction_fsm.display_value = 16'hFFFF;
        repeat (2) @(posedge clk);
        check_display_scan(16'hFFFF, 4*SCAN_DIV + 8);
        release dut.u_reaction_fsm.display_value;

        // =========================================================
        // T10: 随机按键，输出无 X/Z
        // =========================================================
        do_reset;
        for (i = 0; i < 2000; i = i + 1) begin
            @(negedge clk);
            start_button    = $random;
            response_button = $random;
            @(posedge clk);

            if (ready_led !== 1'b0 && ready_led !== 1'b1) begin
                $display("ERROR: ready_led X/Z: %b", ready_led);
                $fatal(1, "TEST_FAILED");
            end
            if ((^seg) === 1'bx) begin
                $display("ERROR: seg has X/Z: %b", seg);
                $fatal(1, "TEST_FAILED");
            end
            check_digit_sel_known;
        end
        start_button    = 1'b0;
        response_button = 1'b0;
        $display("PASS: T10_random_no_xz");

        // =========================================================
        // T11: 延迟发生器 1~5 秒随机范围
        //   用 20 kHz 的独立实例：1~5 秒 = 20k~100k 周期。
        //   若 LFSR 只有 16 位且对 80,001 取模，最大只能到 85,535，
        //   永远覆盖不到 5 秒（90k 以上），本测试会失败。
        // =========================================================
        do_reset;
        min_delay = 32'h7fffffff;
        max_delay = 0;

        // 40 轮：随机映射下 5 秒只占约 1/8，20 轮有约 7% 概率漏掉
        for (r = 0; r < 40; r = r + 1) begin
            @(negedge clk);
            delay_start_chk = 1'b1;
            @(negedge clk);
            delay_start_chk = 1'b0;

            n = 0;
            while (delay_done_chk !== 1'b1 && n < 5*20000 + 200) begin
                @(posedge clk);
                n = n + 1;
            end
            if (delay_done_chk !== 1'b1) begin
                $display("ERROR: aux delay_generator timeout");
                $fatal(1, "TEST_FAILED");
            end

            d = n;
            if (d < 20000 || d > 100000 + 20) begin
                $display("ERROR: aux delay out of 1~5s range: %0d cycles", d);
                $fatal(1, "TEST_FAILED");
            end
            if (d < min_delay) min_delay = d;
            if (d > max_delay) max_delay = d;

            @(posedge clk);
        end

        $display("INFO: delay range observed %0d..%0d cycles (expected ~20000..100000)",
                 min_delay, max_delay);
        if (min_delay > 30000) begin
            $display("ERROR: never observed a ~1s delay (min=%0d)", min_delay);
            $fatal(1, "TEST_FAILED");
        end
        if (max_delay < 90000) begin
            $display("ERROR: never observed a ~5s delay (max=%0d)", max_delay);
            $fatal(1, "TEST_FAILED");
        end
        $display("PASS: T11_delay_range_1_to_5s");

        // =========================================================
        // 完成
        // =========================================================
        do_reset;
        $display("ALL_TESTS_PASSED");
        $finish;
    end

endmodule
