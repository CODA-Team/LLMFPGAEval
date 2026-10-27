import tempfile
import unittest
from pathlib import Path

from tests.test_workbench import _TEMP
from tests.test_agent_tools import GOOD, BAD, TB
from workbench.config import load_config
from workbench.agent_tools import ExperimentTools
from workbench.simulation import modelsim_verdict


class ModelSimVerdictTests(unittest.TestCase):
    PASS = '# TEST_PASS increment\n# ALL_TESTS_PASSED\n# Errors: 0, Warnings: 0\n'

    def test_zero_exit_fatal_and_early_errors_override_pass_markers(self):
        for error in ('# ** Fatal: wrong output\n', '# ** Error: SDF failed\n', '# TEST_FAIL mismatch\n'):
            result = modelsim_verdict(0, error + 'padding\n' * 3000 + self.PASS)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status'], 'failed')

    def test_missing_marker_license_and_sdf_are_not_passes(self):
        self.assertEqual(modelsim_verdict(0, '# ALL_TESTS_PASSED\n')['status'], 'inconclusive')
        self.assertEqual(modelsim_verdict(1, 'Unable to checkout a license')['status'], 'license_error')
        self.assertFalse(modelsim_verdict(0, self.PASS, require_sdf=True)['ok'])
        result = modelsim_verdict(0, '# ** Note: SDF Backannotation Successfully Completed.\n' + self.PASS, require_sdf=True)
        self.assertTrue(result['ok'])

    def test_warnings_preserved_and_cancel_beats_pass(self):
        result = modelsim_verdict(0, '# ** Warning: negative timing check\n' + self.PASS)
        self.assertTrue(result['ok'])
        self.assertIn('negative timing check', result['warnings'][0])
        self.assertFalse(modelsim_verdict(0, self.PASS, reason='cancelled')['ok'])


class RealModelSimTests(unittest.TestCase):
    def test_actual_failure_repair_and_current_evidence(self):
        config = load_config()
        config['simulation_backend'] = 'modelsim'
        if not (Path(config['modelsim_root']) / 'win64/vsim.exe').exists():
            self.skipTest('ModelSim not installed')
        with tempfile.TemporaryDirectory(dir=_TEMP) as temporary:
            tools = ExperimentTools(temporary, config)
            tools.begin_turn('modelsim-test')
            tools.call('write_file', {'path': 'rtl/dut.v', 'content': BAD})
            tools.call('write_file', {'path': 'tb/tb.sv', 'content': TB})
            failed = tools.simulate()
            self.assertEqual(failed['status'], 'failed', failed)
            self.assertEqual(failed['engine'], 'modelsim')
            self.assertFalse(failed['ok'])
            self.assertFalse(tools.summary()['testbench_locked'])
            tools.call('write_file', {'path': 'rtl/dut.v', 'content': GOOD})
            passed = tools.simulate()
            self.assertTrue(passed['ok'], passed)
            self.assertTrue(tools.summary()['verified_current'])


if __name__ == '__main__':
    unittest.main()
