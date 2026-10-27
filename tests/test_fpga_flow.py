import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.agent_tools import ExperimentTools, write_json
from workbench.config import load_config
from workbench.fpga_flow import FpgaFlow

RTL = '''module lab_top(input clk, input rst_n, output led);
reg [3:0] count;
always @(posedge clk) if (!rst_n) count <= 0; else count <= count + 1;
assign led = count[3];
endmodule
'''
TB = '''`timescale 1ns/1ps
module tb;
reg clk=0; reg rst_n=0; wire led; integer i; integer expected;
lab_top dut(clk,rst_n,led);
always #10 clk=~clk;
initial begin
repeat(5) @(negedge clk); rst_n=1; expected=0;
for(i=0;i<40;i=i+1) begin
@(posedge clk); expected=(expected+1)%16; #15;
if (led !== (expected>=8)) $fatal(1,"TEST_FAIL count cycle=%0d led=%0b expected=%0d",i,led,expected);
end
$display("TEST_PASS counter_40_cycles"); $display("ALL_TESTS_PASSED"); $finish;
end
initial begin #5000; $fatal(1,"TEST_FAIL watchdog"); end
endmodule
'''
PROJECT = {'top': 'lab_top', 'clock_port': 'clk', 'clock_period_ns': 20,
           'pins': {'clk': 'clk', 'rst_n': 'rst_n', 'led': 'ready_led'}}
BUS_RTL = '''module lab_top(input clk, input rst_n, input [3:0] buttons,
output led, output [7:0] seg);
reg [3:0] count;
always @(posedge clk) if (!rst_n) count <= 0; else count <= count + 1;
assign led = rst_n;
assign seg = {1'b0, buttons[2:0], (count ^ {4{buttons[3]}})};
endmodule
'''
BUS_TB = '''`timescale 1ns/1ps
module tb;
reg clk=0; reg rst_n=0; reg [3:0] buttons=0;
wire led; wire [7:0] seg; integer i;
lab_top dut(.clk(clk),.rst_n(rst_n),.buttons(buttons),.led(led),.seg(seg));
always #10 clk=~clk;
initial begin
repeat(6) @(negedge clk); rst_n=1;
for(i=1;i<=32;i=i+1) begin
buttons = i % 16;
@(posedge clk); #15;
if (led !== 1'b1 || seg !== {1'b0, buttons[2:0], (i[3:0] ^ {4{buttons[3]}})})
$fatal(1,"TEST_FAIL bus cycle %0d: %b",i,seg);
end
$display("TEST_PASS bus_constants_and_direct_output");
$display("ALL_TESTS_PASSED"); $finish;
end
initial begin #5000; $fatal(1,"TEST_FAIL watchdog"); end
endmodule
'''
BUS_PROJECT = {**PROJECT, 'pins': {**PROJECT['pins'],
               **{f'buttons[{i}]': f'key_col[{i}]' for i in range(4)},
               **{f'seg[{i}]': f'seg[{i}]' for i in range(8)}}}


class FpgaFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.tmp.cleanup)
        self.tools = ExperimentTools(self.tmp.name, load_config())
        self.tools.begin_turn('flow-test')
        self.flow = FpgaFlow(self.tools)
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': TB})

    def test_project_rejects_unsafe_duplicate_and_wrong_clock_pins(self):
        for changes in ({'top': 'x; exec bad'}, {'clock_period_ns': float('nan')},
                        {'pins': {'clk': 'clk', 'led': 'clk'}},
                        {'pins': {'clk': 'rst_n', 'led': 'ready_led'}},
                        {'pins': {'clk': 'clk', 'led': 'unknown'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.flow.create_project(**{**PROJECT, **changes})

    def test_missing_functional_and_hardware_evidence_fail_closed(self):
        self.flow.create_project(**PROJECT)
        with self.assertRaisesRegex(ValueError, '功能仿真'):
            self.flow.implement()
        with self.assertRaises(ValueError):
            self.flow.post_route()
        with self.assertRaises(ValueError):
            self.flow.program('PROGRAM SRAM', True)
        with self.assertRaises(ValueError):
            self.flow.record_observation(True, 'counter', 'blink')

    def test_real_end_to_end_and_invalidated_evidence(self):
        self.flow.create_project(**PROJECT)
        functional = self.tools.simulate()
        self.assertTrue(functional['ok'], functional)
        build = self.flow.implement()
        self.assertTrue(build['ok'], build)
        self.assertTrue({'optimize_rtl', 'optimize_gate', 'place', 'route', 'bitgen'} <=
                        {s['name'] for s in build.get('steps', []) if s['status'] == 'passed'})
        post = self.flow.post_route()
        self.assertTrue(post['ok'], post)
        self.assertTrue(post['sdf_annotated'])
        self.assertTrue(self.flow.summary()['ready_to_program'])
        copied_sdf = Path(self.tmp.name) / 'fpga/post_route' / post['id'] / 'route.sdf'
        sdf_bytes = copied_sdf.read_bytes()
        copied_sdf.write_bytes(b'changed evidence')
        self.assertFalse(self.flow.summary()['ready_to_program'])
        copied_sdf.write_bytes(sdf_bytes)
        self.assertTrue(self.flow.summary()['ready_to_program'])
        with self.assertRaises(ValueError):
            self.flow.program('', True)
        with self.assertRaises(ValueError):
            self.flow.program('PROGRAM SRAM', False)
        # Even an old passing test must not hide the latest failing result.
        failed = {**post, 'id': '20990101T000000_aaaaaaaa', 'time': '2099-01-01', 'ok': False}
        failed_path = Path(self.tmp.name) / 'fpga/post_route' / failed['id'] / 'result.json'
        write_json(failed_path, failed)
        self.assertFalse(self.flow.summary()['ready_to_program'])
        failed_path.unlink()
        # Stub only the final download; this test never accesses physical hardware.
        from workbench.board_state import begin_download
        with patch('workbench.board_state.STATE_FILE', Path(self.tmp.name) / 'shared-board.json'), patch.object(self.tools, '_run', return_value=(0, 'download success', None)):
            self.assertTrue(self.flow.program('PROGRAM SRAM', True)['ok'])
            self.assertTrue(self.flow.summary()['programmed_current'])
            begin_download('another-experiment', 'different-bit-hash')
            self.assertFalse(self.flow.summary()['programmed_current'])
            with self.assertRaises(ValueError):
                self.flow.record_observation(True, 'observe hardware', 'looks right')
        bit = Path(self.tmp.name) / 'fpga/implementation' / build['id'] / 'design.bit'
        original = bit.read_bytes()
        bit.write_bytes(b'altered')
        self.assertFalse(self.flow.summary()['ready_to_program'])
        bit.write_bytes(original)
        self.flow.create_project(**{**PROJECT, 'clock_period_ns': 25})
        self.assertFalse(self.flow.summary()['ready_to_program'])
        with self.assertRaisesRegex(ValueError, '功能仿真'):
            self.flow.implement()

    def test_unbound_actual_td_port_stops_before_bitstream(self):
        self.flow.create_project(**{**PROJECT, 'pins': {'clk': 'clk', 'led': 'ready_led'}})
        self.assertTrue(self.tools.simulate()['ok'])
        result = self.flow.implement()
        self.assertFalse(result['ok'])
        self.assertIn('PORT_BINDING_MISMATCH', result['log_tail'])
        self.assertFalse((Path(self.tmp.name) / 'fpga/implementation' / result['id'] / 'design.bit').exists())

    def test_real_bus_ports_constants_and_direct_outputs(self):
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': BUS_RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': BUS_TB})
        self.flow.create_project(**BUS_PROJECT)
        functional = self.tools.simulate()
        self.assertTrue(functional['ok'], functional['log_tail'])
        build = self.flow.implement()
        self.assertTrue(build['ok'], build['log_tail'])
        post = self.flow.post_route()
        self.assertTrue(post['ok'], post['log_tail'])
        self.assertTrue(self.flow.summary()['ready_to_program'])

    def test_wrong_bus_direction_stops_before_bitstream(self):
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': BUS_RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': BUS_TB})
        self.flow.create_project(**{**BUS_PROJECT, 'pins': {**BUS_PROJECT['pins'],
                                   **{f'buttons[{i}]': f'key_row[{i}]' for i in range(4)}}})
        self.assertTrue(self.tools.simulate()['ok'])
        build = self.flow.implement()
        self.assertFalse(build['ok'])
        self.assertIn('PORT_DIRECTION_MISMATCH', build['log_tail'])
        self.assertFalse((Path(self.tmp.name) / 'fpga/implementation' / build['id'] / 'design.bit').exists())

    def test_project_rejects_mixed_directions_in_one_bus(self):
        with self.assertRaisesRegex(ValueError, '同一总线'):
            self.flow.create_project(**{**BUS_PROJECT, 'pins': {**BUS_PROJECT['pins'], 'buttons[0]': 'key_row[0]'}})

    def test_post_route_keeps_standalone_rtl_checks_with_routed_dut(self):
        # A TB may exercise a standalone RTL submodule alongside its board DUT.
        # Keeping both definitions in one source also catches accidental
        # replacement of the routed top by its behavioural definition.
        rtl = RTL + '\nmodule step4(input [3:0] a, output [3:0] y); assign y=a+1; endmodule\n'
        tb = TB.replace('lab_top dut(clk,rst_n,led);', '''
lab_top dut(.clk(clk),.rst_n(rst_n),.led(led));
reg [3:0] reference_in=0; wire [3:0] reference_out;
step4 reference_dut(.a(reference_in),.y(reference_out));''')
        tb = tb.replace('@(posedge clk); expected=', 'reference_in=i%16; @(posedge clk); expected=')
        tb = tb.replace('$display("TEST_PASS counter_40_cycles");', '''
if (reference_out !== 4'd8) $fatal(1,"TEST_FAIL reference helper");
$display("TEST_PASS standalone_helper");
$display("TEST_PASS counter_40_cycles");''')
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': rtl})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': tb})
        self.flow.create_project(**PROJECT)
        self.assertTrue(self.tools.simulate()['ok'])
        self.assertTrue(self.flow.implement()['ok'])
        post = self.flow.post_route()
        self.assertTrue(post['ok'], post['log_tail'])
        self.assertTrue(post['sdf_annotated'])
        self.assertEqual(post['tests_passed'], ['standalone_helper', 'counter_40_cycles'])
        self.assertIn('Loading work.lab_top', post['log_tail'])
        self.assertIn('source/rtl/lab_top.v', post['evidence_hashes'])
        copied_rtl = Path(self.tmp.name) / 'fpga/post_route' / post['id'] / 'source/rtl/lab_top.v'
        copied_rtl.write_text('changed reference source')
        self.assertFalse(self.flow.summary()['ready_to_program'])


if __name__ == '__main__':
    unittest.main()
