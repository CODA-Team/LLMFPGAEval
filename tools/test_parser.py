"""解析器单元测试（硬验收）。

使用 board/exp4_reaction_timer/ 的真实报告：
  - parse_timing() 的 wns_ns == 14.652（±0.001）
  - whs_ns == 0.278
  - min_period_ns == 5.348
  - 关键路径 startpoint / endpoint 非空
  - parse_clock_summary() 找到 clk / 50 MHz / skew
  - parse_resource() 从真实 TD 日志抓到 LUT=455 / SEQ=399 / BRAM=0

运行：
    python tools/test_parser.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from tools import td_report_parser as parser
    from tools import result_schema as schema
except ImportError:  # 允许从 tools/ 目录直接运行
    import td_report_parser as parser
    import result_schema as schema

EXP4 = os.path.join(ROOT, "exp4_reaction_timer")
FIXTURES = os.path.join(ROOT, "tests", "fixtures", "td")
TIMING_RPT = os.path.join(FIXTURES, "reaction_timer_board_timing.rpt")
CLOCK_RPT = os.path.join(FIXTURES, "reaction_timer_board_clock_summary.rpt")
FLOW_LOG = os.path.join(FIXTURES, "run-with-license.log")


class TestTimingParser(unittest.TestCase):

    def test_wns_real(self):
        """硬验收：真实报告 WNS = 14.652 ± 0.001。"""
        timing = parser.parse_timing(TIMING_RPT)
        self.assertIsNotNone(timing["wns_ns"])
        self.assertAlmostEqual(timing["wns_ns"], 14.652, delta=0.001)

    def test_whs_real(self):
        timing = parser.parse_timing(TIMING_RPT)
        self.assertAlmostEqual(timing["whs_ns"], 0.278, delta=0.001)

    def test_tns_real(self):
        timing = parser.parse_timing(TIMING_RPT)
        self.assertAlmostEqual(timing["tns_ns"], 0.0, delta=1e-9)

    def test_min_period_real(self):
        timing = parser.parse_timing(TIMING_RPT)
        self.assertAlmostEqual(timing["min_period_ns"], 5.348, delta=0.001)

    def test_critical_path_real(self):
        timing = parser.parse_timing(TIMING_RPT)
        path = timing["critical_path"]
        self.assertIsNotNone(path)
        self.assertAlmostEqual(path["slack_ns"], 14.652, delta=0.001)
        self.assertIn("u_dut/u_reaction_fsm/reg2_b1", path["startpoint"])
        self.assertIn("u_dut/u_reaction_fsm/reg2_b12", path["endpoint"])
        # 触发沿说明应被剥掉
        self.assertNotIn("rising edge", path["startpoint"])

    def test_ps_units_synthetic(self):
        """正则必须容忍 ps 单位。"""
        text = (
            "Minimum period is 5348 ps\n"
            "Minimal setup slack: 14652 ps, minimal hold slack: 278 ps\n"
            "Slack (setup check)      14652 ps\n"
            " StartPoint:              a.clk\n"
            " EndPoint:                b.d[0]\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".rpt", delete=False,
                                         encoding="utf-8") as f:
            f.write(text)
            path = f.name
        try:
            timing = parser.parse_timing(path)
            self.assertAlmostEqual(timing["wns_ns"], 14.652, delta=1e-6)
            self.assertAlmostEqual(timing["whs_ns"], 0.278, delta=1e-6)
            self.assertAlmostEqual(timing["min_period_ns"], 5.348, delta=1e-6)
            self.assertEqual(timing["critical_path"]["startpoint"], "a.clk")
        finally:
            os.remove(path)

    def test_missing_file(self):
        timing = parser.parse_timing(os.path.join(EXP4, "__not_exists__.rpt"))
        self.assertIsNone(timing["wns_ns"])
        self.assertIsNotNone(timing["parse_error"])


class TestClockSummary(unittest.TestCase):

    def test_clk_parsed(self):
        summary = parser.parse_clock_summary(CLOCK_RPT)
        self.assertIsNone(summary["parse_error"])
        self.assertEqual(summary["groups"], ["clk"])
        by_name = {c["name"]: c for c in summary["clocks"]}
        self.assertIn("clk", by_name)
        clk = by_name["clk"]
        self.assertAlmostEqual(clk["freq_mhz"], 50.0, delta=1e-6)
        self.assertEqual(clk["type"], "master")
        self.assertEqual(clk["loads"], 256)
        self.assertEqual(clk["max_rise_skew_ps"], 397.0)
        self.assertEqual(clk["max_fall_skew_ps"], 399.0)

    def test_missing_file(self):
        summary = parser.parse_clock_summary(
            os.path.join(EXP4, "__not_exists__.rpt"))
        self.assertEqual(summary["clocks"], [])
        self.assertIsNotNone(summary["parse_error"])


class TestResourceParser(unittest.TestCase):
    def test_registers_packed_with_adders_are_not_zero_flipflops(self):
        text = '''SYN-4002 : Packing 4 DFF/LATCH to SEQ ...
SYN-4008 : Packed 5 adder and 4 SEQ to BLE.
SYN-4008 : Packed 0 LUT and 0 SEQ to BLE.
SYN-4003 : Packing 0 remaining SEQ's ...
'''
        self.assertEqual(parser.parse_resource_text(text)['ff'], 4)


    def test_resource_real(self):
        res = parser.parse_resource(FLOW_LOG)
        self.assertEqual(res["lut"], 455)
        self.assertEqual(res["ff"], 399)
        self.assertEqual(res["bram"], 0)

    def test_resource_missing(self):
        res = parser.parse_resource(os.path.join(EXP4, "__not_exists__.log"))
        self.assertEqual(res, {"lut": None, "ff": None, "bram": None})


class TestReportPaths(unittest.TestCase):

    def test_paths_from_flow_tcl(self):
        tcl = os.path.join(EXP4, "flow.tcl")
        paths = parser.parse_report_paths(tcl)
        self.assertTrue(paths["timing"].endswith("reaction_timer_board_timing.rpt"))
        self.assertTrue(paths["clock_summary"].endswith(
            "reaction_timer_board_clock_summary.rpt"))
        self.assertTrue(paths["bit"].endswith("reaction_timer_board.bit"))


class TestSchema(unittest.TestCase):

    def test_validate_ok(self):
        result = schema.make_result(
            stage="flow", ok=True, exit_code=0, duration_s=1.234,
            errors=[],
            timing={"wns_ns": 1.0, "tns_ns": 0.0, "whs_ns": 0.5,
                    "min_period_ns": 10.0,
                    "critical_path": {"startpoint": "a", "endpoint": "b",
                                      "slack_ns": 1.0}},
            resources={"lut": 1, "ff": 2, "bram": 0},
            artifacts={"bit": "x.bit", "reports": ["x.rpt"]},
        )
        self.assertEqual(schema.validate_result(result), [])

    def test_validate_catches_missing(self):
        result = schema.make_result("sim", True, 0, 0.1, sim=None)
        del result["errors"]
        problems = schema.validate_result(result)
        self.assertTrue(any("errors" in p for p in problems))


def main():
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TestResult()
    suite.run(result)

    for test, _ in result.errors:
        print(f"FAIL: {test.id()}")
    for test, _ in result.failures:
        print(f"FAIL: {test.id()}")
    total = result.testsRun
    failed = len(result.failures) + len(result.errors)
    for i in range(total - failed):
        print(f"PASS: {i + 1}/{total}")

    for test, trace in result.failures + result.errors:
        print(f"\n--- {test.id()} ---\n{trace}")

    if failed == 0:
        print(f"ALL TESTS PASSED ({total})")
        return 0
    print(f"TESTS FAILED: {failed}/{total}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
