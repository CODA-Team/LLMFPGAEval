import tempfile
import unittest
from pathlib import Path

from tests.test_workbench import _TEMP
from tests.test_fpga_flow import RTL, TB, PROJECT
from workbench.agent_tools import ExperimentTools
from workbench.config import load_config
from workbench.fpga_flow import FpgaFlow


class EdaScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.tmp.cleanup)
        self.tools = ExperimentTools(self.tmp.name, load_config())
        self.tools.begin_turn('editable-scripts')

    def prepare(self):
        templates = self.tools.call('templates', {})
        for name, content in templates['files'].items():
            self.tools.call('write_file', {'path': name, 'content': content})
        self.tools.call('write_file', {'path': 'rtl/lab_top.v', 'content': RTL})
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': TB})
        FpgaFlow(self.tools).create_project(**PROJECT)

    def run_stage(self, stage):
        return self.tools.call('run_tool', {'tool': 'td' if stage == 'implementation' else 'modelsim',
                               'stage': stage, 'script': 'scripts/' + {'implementation':'implement.tcl','functional':'functional.do','post_route':'post_route.do'}[stage]})

    def test_testbench_failure_can_be_repaired_in_same_turn(self):
        self.prepare()
        original = self.tools.files()['tb/tb.sv']
        broken = original.replace('module tb;', 'module tb; initial #1 $fatal(1, "testbench defect");')
        self.assertNotEqual(broken, original)
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': broken})
        failed = self.run_stage('functional')
        self.assertFalse(failed['ok'])
        self.assertTrue(failed['compiled'])
        self.assertFalse(self.tools.summary()['testbench_locked'])
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': original})
        passed = self.run_stage('functional')
        self.assertTrue(passed['ok'], passed.get('log_tail'))
        self.assertEqual(passed['turn_id'], failed['turn_id'])
        snapshot = self.tools.folder / 'evidence' / failed['id'] / 'source/tb/tb.sv'
        self.assertEqual(snapshot.read_text(encoding='utf-8'), broken)

    def test_modelsim_diagnostic_options_do_not_hide_assertion_failures(self):
        self.prepare()
        script = self.tools.files()['scripts/functional.do'].replace('vsim -t', 'vsim -quiet -suppress 2685,2718 +nowarnTFMPC -voptargs=+acc -t')
        self.tools.call('write_file', {'path': 'scripts/functional.do', 'content': script})
        passed = self.run_stage('functional')
        self.assertTrue(passed['ok'], passed.get('log_tail'))
        content = self.tools.files()['tb/tb.sv'].replace('module tb;', 'module tb; initial #1 $fatal(1, "must remain visible");')
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': content})
        failed = self.run_stage('functional')
        self.assertFalse(failed['ok'])
        self.assertIn('must remain visible', failed['log_tail'])

    def test_templates_are_reference_only_and_engineering_files_are_editable(self):
        examples = self.tools.call('templates', {})
        self.assertIn('scripts/implement.tcl', examples['files'])
        self.assertEqual(self.tools.files(), {})
        self.tools.call('write_file', {'path':'constraints/clock.sdc','content':'# student constraint\n'})
        self.tools.call('templates', {})
        self.assertEqual(self.tools.files()['constraints/clock.sdc'], '# student constraint\n')
        for path in ('../x.tcl','scripts/../../x.tcl','scripts/file.py','constraints/key.lic','rtl/escape.tcl'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.tools.call('write_file', {'path':path,'content':'bad'})

    def test_agent_surface_and_compact_logs_preserve_evidence_pointer(self):
        from workbench.agent_mcp import AGENT_SCHEMAS
        from workbench.eda_scripts import compact
        names = {s['name'] for s in AGENT_SCHEMAS}
        self.assertTrue({'templates','write_file','run_tool','read_run'} <= names)
        self.assertFalse({'simulate','implement','post_route','program'} & names)
        result = compact({'id':'run', 'log_tail':'a' * 6000, 'read_more':{'file':'td.log'},
                          'runs':[{'log_tail':'unneeded'}], 'evidence_hashes':{'input':'digest'},
                          'artifacts':{'design.bit':'digest'}})
        self.assertEqual(len(result['log_tail']), 3500)
        self.assertEqual(result['read_more'], {'file':'td.log'})
        self.assertNotIn('runs', result)
        self.assertEqual(result['artifacts'], ['design.bit'])

    def test_real_script_error_can_be_read_repaired_and_run_through_sdf(self):
        self.prepare()
        self.assertTrue(self.run_stage('functional')['ok'])
        original = self.tools.files()['scripts/implement.tcl']
        broken = original.replace('optimize_gate', 'typo_optimize_gate')
        self.tools.call('write_file', {'path':'scripts/implement.tcl','content':broken})
        self.assertTrue(self.run_stage('functional')['ok'])
        failed = self.run_stage('implementation')
        self.assertFalse(failed['ok'])
        self.assertIn('typo_optimize_gate', failed['log_tail'])
        self.assertEqual(self.tools.files()['scripts/implement.tcl'], broken)
        log = self.tools.call('read_run', {'stage':'implementation','run_id':failed['id'],'file':'td.log','start_line':1,'max_lines':10})
        self.assertLessEqual(len(log['content'].splitlines()), 10)
        self.assertTrue(log['has_more'])
        self.tools.call('write_file', {'path':'scripts/implement.tcl','content':original})
        self.assertFalse(FpgaFlow(self.tools).summary()['ready_to_program'])
        self.assertTrue(self.run_stage('functional')['ok'])
        build = self.run_stage('implementation')
        self.assertTrue(build['ok'], build.get('log_tail'))
        self.assertTrue({'place','route','bitgen'} <= {s['name'] for s in build['steps'] if s['status']=='passed'})
        post = self.run_stage('post_route')
        self.assertTrue(post['ok'], post.get('log_tail'))
        self.assertTrue(post['sdf_annotated'])
        self.assertTrue(FpgaFlow(self.tools).summary()['ready_to_program'])
        self.tools.call('write_file', {'path':'constraints/clock.sdc','content':'create_clock -period 25 [get_ports clk]\n'})
        self.assertFalse(FpgaFlow(self.tools).summary()['ready_to_program'])

    def test_real_default_unlimited_runs_beyond_previous_5_3_3_limits(self):
        self.prepare()
        for stage, count in (('functional', 6), ('implementation', 4), ('post_route', 4)):
            for attempt in range(count):
                with self.subTest(stage=stage, attempt=attempt + 1):
                    result = self.run_stage(stage)
                    self.assertTrue(result['ok'], result.get('log_tail'))
                    self.assertEqual(self.tools.run_budget()[stage]['used'], attempt + 1)
            self.assertIsNone(self.tools.run_budget()[stage]['limit'])
        self.assertTrue(FpgaFlow(self.tools).summary()['ready_to_program'])

    def test_tb_repair_reuses_existing_td_run_with_real_tools(self):
        self.prepare()
        # Check sourced-script tracking using an actual ModelSim invocation.
        self.tools.call('write_file', {'path': 'scripts/helper.do', 'content': 'puts {helper loaded}\n'})
        script = 'source source/scripts/helper.do\n' + self.tools.files()['scripts/functional.do']
        self.tools.call('write_file', {'path': 'scripts/functional.do', 'content': script})
        functional = self.run_stage('functional')
        self.assertTrue(functional['ok'], functional.get('log_tail'))
        self.assertTrue(any(s['name'] == 'source' and s['args'] == ['source/scripts/helper.do'] for s in functional['steps']))
        build = self.run_stage('implementation')
        self.assertTrue(build['ok'], build.get('log_tail'))
        post = self.run_stage('post_route')
        self.assertTrue(post['ok'], post.get('log_tail'))
        tb = self.tools.files()['tb/tb.sv'].replace('i<40', 'i<48')
        self.tools.call('write_file', {'path': 'tb/tb.sv', 'content': tb})
        self.assertTrue(FpgaFlow(self.tools).summary()['implementation_ok'])
        self.assertFalse(FpgaFlow(self.tools).summary()['post_route_ok'])
        # Directly rerun post-route with the new TB, without repeating TD or RTL simulation.
        rerun = self.run_stage('post_route')
        self.assertTrue(rerun['ok'], rerun.get('log_tail'))
        self.assertEqual(rerun['implementation_id'], build['id'])
        summary = FpgaFlow(self.tools).summary()
        self.assertEqual(len(summary['runs']['implementation']), 1)
        self.assertEqual(len(summary['runs']['post_route']), 2)
        self.assertFalse(summary['ready_to_program'])  # new functional TB not yet run
        self.assertTrue(self.run_stage('functional')['ok'])
        self.assertTrue(FpgaFlow(self.tools).summary()['ready_to_program'])

    def test_rejected_place_arguments_record_the_failed_stage_attempt(self):
        self.prepare()
        self.assertTrue(self.run_stage('functional')['ok'])
        script = self.tools.files()['scripts/implement.tcl'].replace('\nplace\n', '\nplace -bad\n')
        self.tools.call('write_file', {'path': 'scripts/implement.tcl', 'content': script})
        result = self.run_stage('implementation')
        self.assertFalse(result['ok'])
        self.assertIn({'name': 'place', 'status': 'failed'},
                      [{'name': s['name'], 'status': s['status']} for s in result['steps']])
        self.assertFalse(any(s['name'] == 'route' for s in result['steps']))

    def test_script_bridge_blocks_shell_download_and_outside_paths(self):
        self.prepare()
        for command in ('exec cmd.exe /c echo forbidden', 'download -bit design.bit -cable 0',
                        'source ../../outside.tcl', 'read_verilog -file C:/outside.v -top lab_top'):
            self.tools.call('write_file', {'path':'scripts/query.tcl','content':command})
            result = self.tools.call('run_tool', {'tool':'td','stage':'query','script':'scripts/query.tcl'})
            self.assertFalse(result['ok'], command)
            self.assertTrue('not allowed' in result['log_tail'] or 'invalid command' in result['log_tail'], result['log_tail'])

    def test_normalized_netlist_path_still_uses_the_routed_design(self):
        self.prepare()
        path = 'scripts/post_route.do'
        script = self.tools.files()[path].replace('work post_route.v', 'work ./post_route.v')
        self.tools.call('write_file', {'path':path, 'content':script})
        self.assertTrue(self.run_stage('functional')['ok'])
        self.assertTrue(self.run_stage('implementation')['ok'])
        result = self.run_stage('post_route')
        self.assertTrue(result['ok'], result.get('log_tail'))

    def test_clock_on_reset_does_not_qualify_for_the_board_clock(self):
        self.prepare()
        self.tools.call('write_file', {'path':'constraints/clock.sdc', 'content':'create_clock -name clk -period 20 [get_ports rst_n]\n'})
        self.assertTrue(self.run_stage('functional')['ok'])
        result = self.run_stage('implementation')
        self.assertFalse(result['board_compatible'])

    def test_reports_cannot_overwrite_a_bitstream(self):
        self.prepare()
        path = 'scripts/implement.tcl'
        script = self.tools.files()[path] + '\nreport_clock_summary -file design.bit\n'
        self.tools.call('write_file', {'path':path,'content':script})
        self.assertTrue(self.run_stage('functional')['ok'])
        result = self.run_stage('implementation')
        self.assertFalse(result['ok'])

    def test_normalized_rtl_alias_cannot_overwrite_the_work_library(self):
        self.prepare()
        path = 'scripts/post_route.do'
        script = self.tools.files()[path].replace('vlog -sv -work rtl_reference {*}$rtl_files',
                                                  'vlog -sv -work work ./source/rtl/lab_top.v')
        self.tools.call('write_file', {'path':path,'content':script})
        self.assertTrue(self.run_stage('functional')['ok'])
        self.assertTrue(self.run_stage('implementation')['ok'])
        result = self.run_stage('post_route')
        self.assertIn('post-route RTL helpers must use rtl_reference', result['log_tail'])

    def test_auxiliary_icarus_choice_and_legacy_source_filters(self):
        self.prepare()
        self.tools.config['simulation_backend'] = 'icarus'
        from workbench import agent_mcp
        self.assertTrue(hasattr(agent_mcp, 'available_schemas'), 'MCP cannot expose configured auxiliary Icarus')
        names = {s['name'] for s in agent_mcp.available_schemas(self.tools)}
        self.assertIn('simulate', names)
        self.assertEqual(self.tools.call('board_info', {})['functional_backend'], 'icarus')
        result = self.tools.simulate()
        self.assertTrue(result['ok'], result.get('log_tail'))
        self.assertFalse(self.flow_summary()['ready_to_program'])

    def flow_summary(self):
        return FpgaFlow(self.tools).summary()

    def test_legacy_post_route_accepts_new_engineering_file_groups(self):
        self.prepare()
        self.assertTrue(self.tools.simulate()['ok'])
        self.assertTrue(FpgaFlow(self.tools).implement()['ok'])
        result = FpgaFlow(self.tools).post_route()
        self.assertTrue(result['ok'], result.get('log_tail'))

    def test_manual_programming_log_can_be_read_without_programming(self):
        folder = self.tools.folder / 'fpga/programming/20261023T000000_aaaaaaaa'
        folder.mkdir(parents=True)
        (folder / 'program.log').write_text('download success\n')
        result = self.tools.call('read_run', {'stage':'programming','run_id':folder.name,'file':'program.log'})
        self.assertEqual(result['content'], 'download success')


if __name__ == '__main__':
    unittest.main()
