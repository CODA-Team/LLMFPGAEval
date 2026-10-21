// ============================================================
// 实验4 Testbench 公共函数/任务
//
// 使用前提：包含本文件的 TB 必须声明
//   - clk        : 时钟
//   - seg[7:0]   : DUT 段码输出
//   - digit_sel[3:0] : DUT 位选输出
// ============================================================

// ------------------------------------------------------------
// 独立的二进制 -> BCD 参考模型（用除法/取模，不复刻 RTL 的
// double-dabble，避免共享同一实现错误）
// ------------------------------------------------------------
function [15:0] ref_bcd;
    input [13:0] v;
    reg [3:0] th, hu, te, on;
    begin
        th = (v / 1000) % 10;
        hu = (v / 100)  % 10;
        te = (v / 10)   % 10;
        on = v          % 10;
        ref_bcd = {th, hu, te, on};
    end
endfunction

// ------------------------------------------------------------
// 标准共阴段码：bit0=a ... bit6=g
// ------------------------------------------------------------
function [6:0] ref_seg;
    input [3:0] d;
    begin
        case (d)
            4'd0: ref_seg = 7'b0111111;
            4'd1: ref_seg = 7'b0000110;
            4'd2: ref_seg = 7'b1011011;
            4'd3: ref_seg = 7'b1001111;
            4'd4: ref_seg = 7'b1100110;
            4'd5: ref_seg = 7'b1101101;
            4'd6: ref_seg = 7'b1111101;
            4'd7: ref_seg = 7'b0000111;
            4'd8: ref_seg = 7'b1111111;
            4'd9: ref_seg = 7'b1101111;
            default: ref_seg = 7'b0000000;
        endcase
    end
endfunction

// ------------------------------------------------------------
// 扫描显示检查：
//   - digit_sel 必须始终是 one-hot-low
//   - 每个位被选中时，seg 必须等于该位 BCD 的段码
//   - 一个完整扫描周期内 4 位都要出现
//   - 0xFFFF 特殊图案为 F/A/1/1
//
// cycles 建议 >= 4*SCAN_DIV + 4
// ------------------------------------------------------------
task check_display_scan;
    input [15:0] bcd_value;
    input integer cycles;
    integer k;
    integer idx;
    reg [3:0] seen;
    reg [3:0] valid;
    reg [3:0] digit;
    reg [6:0] expected;
    begin
        seen  = 4'b0000;
        valid = 4'b0000;

        for (k = 0; k < cycles; k = k + 1) begin
            @(negedge clk);

            case (digit_sel)
                4'b1110: idx = 0;
                4'b1101: idx = 1;
                4'b1011: idx = 2;
                4'b0111: idx = 3;
                default: begin
                    $display("ERROR: digit_sel not one-hot-low: %b", digit_sel);
                    $fatal(1, "TEST_FAILED");
                end
            endcase

            seen[idx] = 1'b1;

            // 动态扫描在换位前后有消隐窗口（seg 全 0）。消隐采样
            // 不做段码比较，但每位必须至少有一次非消隐的有效采样，
            // 否则“整帧都消隐”也会误判为通过。
            if (seg !== 8'b00000000) begin
                valid[idx] = 1'b1;

                if (bcd_value == 16'hFFFF) begin
                    case (idx)
                        0: begin digit = 4'h1; expected = 7'b0000110; end
                        1: begin digit = 4'h1; expected = 7'b0000110; end
                        2: begin digit = 4'hA; expected = 7'b1110111; end
                        default: begin digit = 4'hF; expected = 7'b1110001; end
                    endcase
                end else begin
                    case (idx)
                        0: digit = bcd_value[3:0];
                        1: digit = bcd_value[7:4];
                        2: digit = bcd_value[11:8];
                        default: digit = bcd_value[15:12];
                    endcase
                    expected = ref_seg(digit);
                end

                if (seg[7] !== 1'b0) begin
                    $display("ERROR: dp should be 0, seg=%b", seg);
                    $fatal(1, "TEST_FAILED");
                end

                if (seg[6:0] !== expected) begin
                    $display("ERROR: seg mismatch idx=%0d digit=%h expected=%b actual=%b (display=%h)",
                             idx, digit, expected, seg[6:0], bcd_value);
                    $fatal(1, "TEST_FAILED");
                end
            end
        end

        if (seen !== 4'b1111) begin
            $display("ERROR: scan did not visit all digits, seen=%b after %0d cycles",
                     seen, cycles);
            $fatal(1, "TEST_FAILED");
        end

        if (valid !== 4'b1111) begin
            $display("ERROR: some digit had no non-blanked sample, valid=%b after %0d cycles",
                     valid, cycles);
            $fatal(1, "TEST_FAILED");
        end

        $display("PASS: display_scan value=%h", bcd_value);
    end
endtask

// ------------------------------------------------------------
// 简单比较任务
// ------------------------------------------------------------
task check16;
    input [255:0] name;
    input [15:0]  expected;
    input [15:0]  actual;
    begin
        if (actual !== expected) begin
            $display("ERROR: %0s expected=%h actual=%h", name, expected, actual);
            $fatal(1, "TEST_FAILED");
        end else begin
            $display("PASS: %0s", name);
        end
    end
endtask

task check1;
    input [255:0] name;
    input         expected;
    input         actual;
    begin
        if (actual !== expected) begin
            $display("ERROR: %0s expected=%b actual=%b", name, expected, actual);
            $fatal(1, "TEST_FAILED");
        end else begin
            $display("PASS: %0s", name);
        end
    end
endtask
