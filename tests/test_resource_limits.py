import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.agent_tools import ExperimentTools, read_json
from workbench.config import load_config, validate_config
from workbench.eda_scripts import EdaScripts
from workbench.simulation import modelsim_verdict, evidence_manifest, evidence_valid


class ResourceLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        config_patch = patch('workbench.config.CONFIG_FILE', self.folder/'isolated-config.json')
        config_patch.start()
        self.addCleanup(config_patch.stop)
        self.tools = ExperimentTools(self.folder, load_config())
        self.tools.begin_turn('resource-test')

    def run_python(self, code):
        return self.tools._run([sys.executable, '-c', code], self.folder, self.folder / 'simulation.log')

    def test_default_execution_and_source_budgets_are_optional(self):
        config = load_config()
        for field in ('simulation_timeout_s', 'implementation_timeout_s', 'programming_timeout_s',
                      'max_source_files', 'max_source_file_kib'):
            self.assertIsNone(config.get(field))
            self.assertIsNone(self.tools.config.get(field))
        self.assertEqual(config.get('log_limit_mib'), 1024)
        self.assertEqual(config.get('waveform_limit_mib'), 1024)

    def test_large_log_and_waveform_no_longer_hit_old_caps(self):
        code, log, reason = self.run_python(
            'from pathlib import Path; import sys; '
            'Path("wave.vcd").write_bytes(b"w"*(32*1024*1024)); '
            'sys.stdout.write("padding line\\n"*220000); '
            'print("TEST_PASS large_output"); print("ALL_TESTS_PASSED")')
        self.assertIsNone(reason)
        self.assertEqual(code, 0)
        self.assertLessEqual(len(log), 16000)
        self.assertIn('ALL_TESTS_PASSED', log)
        self.assertGreater((self.folder / 'simulation.log').stat().st_size, 2_000_000)
        self.assertGreater((self.folder / 'wave.vcd').stat().st_size, 24_000_000)

    def test_explicit_size_limit_identifies_file_actual_size_and_setting(self):
        for filename, field in (('wave.vcd', 'waveform_limit_mib'), ('simulation.log', 'log_limit_mib')):
            with self.subTest(filename=filename):
                self.tools.config.update(log_limit_mib=None, waveform_limit_mib=None)
                self.tools.config[field] = 1
                command = ('from pathlib import Path; Path("wave.vcd").write_bytes(b"w"*(2*1024*1024))'
                           if filename == 'wave.vcd' else 'print("x"*(2*1024*1024))')
                _, _, reason = self.run_python(command)
                self.assertEqual(reason, 'output_limit')
                detail = read_json(self.folder / 'execution_issue.json', {})
                self.assertEqual(detail.get('file'), filename)
                self.assertEqual(detail.get('limit_bytes'), 1024 * 1024)
                self.assertGreater(detail.get('actual_bytes', 0), detail['limit_bytes'])
                self.assertEqual(detail.get('setting'), field)

    def test_explicit_timeout_and_cancellation_still_work(self):
        self.tools.config['simulation_timeout_s'] = .1
        _, _, reason = self.run_python('import time; time.sleep(2)')
        self.assertEqual(reason, 'timeout')
        self.tools.config['simulation_timeout_s'] = None
        self.tools.cancel()
        _, _, reason = self.run_python('import time; time.sleep(2)')
        self.assertEqual(reason, 'cancelled')

    def test_unlimited_sources_and_explicit_source_budgets(self):
        for i in range(34):
            self.tools.call('write_file', {'path': f'rtl/m{i}.v', 'content': f'module m{i}; endmodule\n'})
        self.tools.call('write_file', {'path': 'rtl/large.v', 'content': '// padding\n' * 30000})
        self.assertEqual(len(self.tools.files()), 35)
        self.tools.config['max_source_files'] = 35
        with self.assertRaisesRegex(ValueError, '35'):
            self.tools.call('write_file', {'path': 'rtl/extra.v', 'content': 'module extra; endmodule'})
        self.tools.config['max_source_file_kib'] = 1
        with self.assertRaises(ValueError):
            self.tools.call('write_file', {'path': 'rtl/m0.v', 'content': '// large\n' * 300})

    def test_query_budget_is_optional_and_independent(self):
        for _ in range(12):
            self.tools.reserve_run('query')
        self.assertIsNone(self.tools.run_budget()['query']['limit'])
        self.tools.begin_turn('limited-query', run_limits={'query': 1})
        self.tools.reserve_run('query')
        with self.assertRaisesRegex(ValueError, '1'):
            self.tools.reserve_run('query')

    def test_large_modelsim_log_checks_entire_file_not_only_tail(self):
        logfile = self.folder / 'simulation.log'
        logfile.write_text('# ** Error: early actual failure\n' + '# padding\n' * 300000 +
                           '# TEST_PASS misleading\n# ALL_TESTS_PASSED\n', encoding='utf-8')
        verdict = modelsim_verdict(0, 'tail only', log_path=logfile)
        self.assertFalse(verdict['ok'])
        self.assertEqual(verdict['status'], 'failed')
        self.assertIn('early actual failure', verdict['errors'][0])
        logfile.write_text('# ** Note: SDF Backannotation Successfully Completed.\n' +
                           '# ** Warning: padding\n' * 100000 +
                           '# TEST_PASS real_check\n# ALL_TESTS_PASSED\n', encoding='utf-8')
        verdict = modelsim_verdict(0, 'tail only', require_sdf=True, log_path=logfile)
        self.assertTrue(verdict['ok'])
        self.assertEqual(verdict['warning_count'], 100000)
        self.assertLessEqual(len(verdict['warnings']), 100)

    def test_large_evidence_is_hashed_and_paged_without_whole_file_reads(self):
        run_id = '20261026T000000_aaaaaaaa'
        folder = self.folder / 'fpga/query' / run_id
        folder.mkdir(parents=True)
        logfile = folder / 'simulation.log'
        with logfile.open('w', encoding='utf-8', newline='\n') as output:
            output.write('first\n')
            for _ in range(30000):
                output.write('p' * 1023 + '\n')
            output.write('last\n')
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('whole file read')):
            manifest = evidence_manifest(folder)
            self.assertTrue(evidence_valid(folder, manifest))
        with patch.object(Path, 'read_text', side_effect=AssertionError('whole file read')):
            page = EdaScripts(self.tools).read_run('query', run_id, 'simulation.log', start_line=30002)
        self.assertEqual(page['content'], 'last')
        self.assertFalse(page['has_more'])
        self.assertEqual(page['total_lines'], 30002)

    def test_resource_settings_reject_invalid_values_and_allow_null(self):
        config = load_config()
        for field in ('simulation_timeout_s', 'implementation_timeout_s', 'programming_timeout_s',
                      'max_source_files', 'max_source_file_kib', 'log_limit_mib', 'waveform_limit_mib'):
            for value in (0, -1, True, 1.5, '2'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate_config({**config, field: value})
            validate_config({**config, field: None})

