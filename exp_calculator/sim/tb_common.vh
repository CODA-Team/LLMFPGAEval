// ============================================================
// 计算器 Testbench 公共函数
//
// 参考模型用除法/取模实现 BCD，不复刻 RTL 的 Double Dabble，
// 避免两边共享同一个实现错误。
// ============================================================

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

// 共阴段码：bit0=a ... bit6=g；4'hA='-', 4'hB='E', 4'hC='r'
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
            4'hA: ref_seg = 7'b1000000;
            4'hB: ref_seg = 7'b1111001;
            4'hC: ref_seg = 7'b1010000;
            default: ref_seg = 7'b0000000;
        endcase
    end
endfunction

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
