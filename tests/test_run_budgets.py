import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from tests.test_fpga_flow import RTL, TB
from workbench.agent_tools import ExperimentTools, read_json, write_json
from workbench.config import load_config
from workbench.fpga_flow import FpgaFlow
from workbench.eda_scripts import EdaScripts


class RunBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.tools = ExperimentTools(self.temp.name, load_config())
        self.tools.begin_turn('quota-test')

    def test_default_limits_allow_more_than_old_counts(self):
        for stage in ('functional', 'implementation', 'post_route'):
            for _ in range(9):
                self.tools.reserve_run(stage)
            status = self.tools.run_budget()[stage]
            self.assertEqual(status['used'], 9)
            self.assertIsNone(status['limit'])
            self.assertIsNone(status['remaining'])

    def test_independent_limits_persist_across_tools_and_reset_on_new_turn(self):
        self.tools.begin_turn('limited', run_limits={'functional': 1, 'implementation': 2, 'post_route': 3})
        for stage, count in (('functional', 1), ('implementation', 2), ('post_route', 3)):
            for _ in range(count):
                self.tools.reserve_run(stage)
            # MCP and the manager construct separate tool objects for the same turn.
            reopened = ExperimentTools(self.tools.folder)
            with self.assertRaisesRegex(ValueError, str(count)):
                reopened.reserve_run(stage)
            self.assertEqual(reopened.run_budget()[stage]['used'], count)
            self.assertEqual(reopened.run_budget()[stage]['remaining'], 0)
        self.tools.begin_turn('next-unlimited')
        self.assertTrue(all(self.tools.run_budget()[s]['used'] == 0 for s in ('functional', 'implementation', 'post_route')))
        self.assertIsNone(self.tools.run_budget()['functional']['limit'])

    def test_old_control_without_limits_uses_unlimited_defaults(self):
        write_json(self.tools.folder / 'control.json', {'turn_id': 'old', 'simulations': 50, 'implementations': 30,
                                                       'post_route_simulations': 30})
        for stage in ('functional', 'implementation', 'post_route'):
            self.tools.reserve_run(stage)
        self.assertEqual(self.tools.run_budget()['functional']['used'], 51)

    def test_invalid_budget_does_not_reset_an_existing_turn(self):
        self.tools.reserve_run('functional')
        original = read_json(self.tools.folder / 'control.json')
        for limits in ({'functional': 0}, {'post_route': True}, {'implementation': -1}, {'unknown': 3}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                self.tools.begin_turn('bad', run_limits=limits)
            self.assertEqual(read_json(self.tools.folder / 'control.json'), original)

    def test_board_info_reports_actual_limits_and_usage(self):
        self.tools.begin_turn('limited', run_limits={'functional': 4})
        self.tools.reserve_run('functional')
        budget = FpgaFlow(self.tools).board_info()['run_budget']
        self.assertEqual(budget['functional'], {'limit': 4, 'used': 1, 'remaining': 3})
        self.assertIsNone(budget['implementation']['limit'])

    def test_legacy_implementation_and_post_route_share_the_same_quota(self):
        flow = FpgaFlow(self.tools)
        for stage, method, counter in (('implementation', flow.implement, 'implementations'),
                                      ('post_route', flow.post_route, 'post_route_simulations')):
            with self.subTest(stage=stage):
                self.tools.begin_turn('limited', run_limits={stage: 1})
                self.tools.reserve_run(stage)
                with patch.object(flow, 'summary', return_value={'functional_ok': True, 'implementation_ok': True}), \
                     patch.object(flow, '_new') as run:
                    with self.assertRaisesRegex(ValueError, '1'):
                        method()
                    run.assert_not_called()
                self.tools.begin_turn('unlimited')
                control = read_json(self.tools.folder / 'control.json')
                control[counter] = 3
                write_json(self.tools.folder / 'control.json', control)
                with patch.object(flow, 'summary', return_value={'functional_ok': True, 'implementation_ok': True,
                                                               'implementation': {'id': 'previous'}}), \
                     patch.object(flow, 'project', return_value={}), \
                     patch.object(flow, '_new', side_effect=RuntimeError('execution reached')):
                    with self.assertRaisesRegex(RuntimeError, 'execution reached'):
                        method()
                self.assertEqual(self.tools.run_budget()[stage]['used'], 4)

    def test_functional_legacy_adapter_respects_shared_limit(self):
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': TB})
        self.tools.begin_turn('limited', run_limits={'functional': 1})
        self.tools.reserve_run('functional')
        with patch.object(self.tools, '_run') as run:
            with self.assertRaisesRegex(ValueError, '1'):
                self.tools.simulate()
            run.assert_not_called()

    def test_manual_cancellation_blocks_additional_reservations(self):
        self.tools.cancel()
        with self.assertRaisesRegex(ValueError, '停止'):
            self.tools.reserve_run('functional')
        self.assertEqual(self.tools.run_budget()['functional']['used'], 0)

    def test_script_adapter_respects_same_quota_before_starting_process(self):
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': TB})
        self.tools.call('write_file', {'path': 'scripts/test.tcl', 'content': 'puts test'})
        runner = EdaScripts(self.tools)
        self.tools.begin_turn('limited', run_limits={'functional': 1, 'implementation': 2, 'post_route': 3})
        for stage, count in (('functional', 1), ('implementation', 2), ('post_route', 3)):
            for _ in range(count):
                self.tools.reserve_run(stage)
            with patch.object(runner.flow, 'summary', return_value={'functional_ok': True, 'implementation_ok': True}), \
                 patch.object(self.tools, '_run') as process:
                with self.assertRaisesRegex(ValueError, str(count)):
                    runner.run_tool('td' if stage == 'implementation' else 'modelsim', stage, 'scripts/test.tcl')
                process.assert_not_called()
            self.assertEqual(self.tools.run_budget()[stage]['used'], count)

    def test_invalid_or_not_ready_script_does_not_consume_attempt(self):
        runner = EdaScripts(self.tools)
        with self.assertRaises(ValueError):
            runner.run_tool('modelsim', 'functional', 'scripts/missing.do')
        self.tools.call('write_file', {'path': 'scripts/test.tcl', 'content': 'puts test'})
        with self.assertRaises(ValueError):
            runner.run_tool('td', 'implementation', 'scripts/test.tcl')
        self.assertTrue(all(item['used'] == 0 for item in self.tools.run_budget().values()))

    def test_query_budget_remains_separate(self):
        self.tools.begin_turn('query-cap', run_limits={'query': 8})
        for _ in range(8):
            self.tools.reserve_run('query')
        with self.assertRaisesRegex(ValueError, '8'):
            self.tools.reserve_run('query')
        self.assertTrue(all(item['used'] == 0 for stage, item in self.tools.run_budget().items() if stage != 'query'))
