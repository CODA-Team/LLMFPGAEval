import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_workbench import _TEMP
from workbench.config import load_config

GOOD = "module dut(input [3:0] a, output [3:0] y); assign y = a + 1; endmodule\n"
BAD = GOOD.replace("a + 1", "a + 2")
TB = '''module tb;
reg [3:0] a; wire [3:0] y;
dut d(a,y);
initial begin
  a=3; #1;
  if (y !== 4) $fatal(1, "expected 4 got %0d", y);
  $display("TEST_PASS increment");
  $display("ALL_TESTS_PASSED"); $finish;
end
endmodule
'''


class AgentToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def tools(self):
        self.assertIsNotNone(importlib.util.find_spec("workbench.agent_tools"), "experiment tool implementation missing")
        from workbench.agent_tools import ExperimentTools
        tools = ExperimentTools(self.folder, {**load_config(), 'simulation_backend': 'icarus'})
        tools.begin_turn("test-turn")
        return tools

    def design(self, tools, rtl=BAD, tb=TB):
        tools.call("write_file", {"path": "rtl/dut.sv", "content": rtl})
        tools.call("write_file", {"path": "tb/tb.sv", "content": tb})

    def test_real_failure_repair_pass_and_immutable_evidence(self):
        tools = self.tools()
        self.design(tools)
        failed = tools.call("simulate", {})
        self.assertEqual(failed["status"], "failed")
        self.assertIn("expected 4 got 5", failed["log_tail"])
        self.assertFalse(tools.summary()['testbench_locked'])
        tools.call("write_file", {"path": "rtl/dut.sv", "content": GOOD})
        passed = tools.call("simulate", {})
        self.assertTrue(passed["ok"])
        self.assertEqual(passed["tests_passed"], ["increment"])
        self.assertNotEqual(failed["source_sha256"], passed["source_sha256"])
        snapshot = self.folder / "evidence" / failed["id"] / "source/rtl/dut.sv"
        self.assertEqual(snapshot.read_text(), BAD)
        self.assertTrue(tools.summary()["verified_current"])
        tools.call("write_file", {"path": "rtl/dut.sv", "content": BAD})
        self.assertFalse(tools.summary()["verified_current"])
        self.assertEqual(tools.summary()["latest"]["status"], "passed")

    def test_compile_failure_and_missing_marker_are_not_success(self):
        tools = self.tools()
        self.design(tools, "module dut( BROKEN", TB)
        self.assertEqual(tools.call("simulate", {})["status"], "compile_failed")
        tools.call("write_file", {"path": "rtl/dut.sv", "content": GOOD})
        tools.call("write_file", {"path": "tb/tb.sv", "content": TB.replace('$display("TEST_PASS increment");', "")})
        result = tools.call("simulate", {})
        self.assertEqual(result["status"], "inconclusive")
        self.assertFalse(result["ok"])

    def test_workspace_paths_and_file_operations_are_bounded(self):
        tools = self.tools()
        for path in ("../secret.sv", "rtl/../../secret.sv", "C:/secret.sv", "rtl/a.txt", "opencode.json", "rtl/a.sv:stream"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                tools.call("write_file", {"path": path, "content": GOOD})
        tools.config['max_source_file_kib'] = 256
        with self.assertRaises(ValueError):
            tools.call("write_file", {"path": "rtl/huge.sv", "content": "x" * 300000})
        tools.config['max_source_file_kib'] = None
        self.design(tools, GOOD, TB + '\n// $fopen in a comment is harmless\n')
        tools.call("write_file", {"path": "rtl/dut.sv", "content": GOOD.replace("endmodule", 'initial $system("cmd"); endmodule')})
        with self.assertRaisesRegex(ValueError, "system"):
            tools.call("simulate", {})

    def test_turn_limit_and_legacy_testbench_lock_migration(self):
        tools = self.tools()
        self.design(tools, GOOD)
        tools.call("simulate", {})
        (self.folder / 'locked-testbench.json').write_text('{"sha256": "legacy"}')
        tools.begin_turn("next-turn", run_limits={"functional": 5})
        self.assertFalse((self.folder / 'locked-testbench.json').exists())
        tools.call("write_file", {"path": "tb/tb.sv", "content": TB + "\n"})
        for _ in range(5):
            tools.call("simulate", {})
        with self.assertRaisesRegex(ValueError, "5"):
            tools.call("simulate", {})
        events = [json.loads(line) for line in (self.folder / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(event["type"] == "testbench_policy_updated" for event in events))

    def test_timeout_is_recorded_and_cancellation_prevents_writes(self):
        tools = self.tools()
        tools.config["simulation_timeout_s"] = 0.3
        self.design(tools, GOOD, "module tb; initial forever #1; endmodule")
        result = tools.call("simulate", {})
        self.assertEqual(result["status"], "timeout")
        tools.cancel()
        self.assertEqual(tools.call("read_file", {"path": "rtl/dut.sv"})["content"], GOOD)
        with self.assertRaisesRegex(ValueError, "停止"):
            tools.call("write_file", {"path": "rtl/dut.sv", "content": GOOD})

    def test_latest_result_uses_time_not_random_identifier_order(self):
        tools = self.tools()
        self.design(tools, GOOD)
        from workbench.agent_tools import write_json
        digest = tools.digest(tools.files())
        write_json(self.folder / "evidence/zzzz/result.json", {"time": "2026-10-23T00:00:00", "ok": False, "source_sha256": digest})
        write_json(self.folder / "evidence/aaaa/result.json", {"time": "2026-10-23T00:00:01", "ok": True, "source_sha256": digest})
        self.assertTrue(tools.summary()["verified_current"])

    def test_early_failure_in_long_log_cannot_be_hidden_by_pass_marker(self):
        tools = self.tools()
        tb = '''module tb;
integer i;
initial begin
  $display("FAIL expected 4 got 5");
  for (i=0; i<2000; i=i+1) $display("padding line %0d", i);
  $display("TEST_PASS misleading");
  $display("ALL_TESTS_PASSED"); $finish;
end
endmodule
'''
        self.design(tools, GOOD, tb)
        result = tools.call("simulate", {})
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["ok"])
        self.assertLessEqual(len(result["log_tail"]), 16000)

    def test_dumpfile_macro_alias_cannot_escape_waveform_directory(self):
        tools = self.tools()
        unsafe = '`define WAV $dumpfile\nmodule tb; initial `WAV("../../outside.vcd"); endmodule'
        with self.assertRaisesRegex(ValueError, "dumpfile"):
            tools._validate_source({"tb/tb.sv": unsafe})
        tools._validate_source({"tb/tb.sv": 'module tb; initial $dumpfile("wave.vcd"); endmodule'})


if __name__ == "__main__":
    unittest.main()
