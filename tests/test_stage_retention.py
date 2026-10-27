"""Snapshot fixtures test bookkeeping without running EDA or programming hardware."""
import tempfile
import time
import unittest

from tests.test_workbench import _TEMP
from workbench.agent_tools import ExperimentTools, write_json
from workbench.config import load_config
from workbench.fpga_flow import FpgaFlow, project_digest
from workbench.output_io import hash_file
from workbench.simulation import evidence_manifest


class StageRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.tmp.cleanup)
        self.tools = ExperimentTools(self.tmp.name, load_config())
        self.tools.begin_turn('retention-test')
        self.flow = FpgaFlow(self.tools)
        self.flow.create_project('top', 'clk', 20, {'clk': 'clk', 'led': 'ready_led'})
        for name, text in {'rtl/top.v': 'module top; endmodule', 'tb/tb.sv': 'module tb; endmodule',
                           'scripts/functional.do': 'run -all', 'scripts/implement.tcl': 'place; route',
                           'scripts/post_route.do': 'run -all', 'constraints/clock.sdc': 'create_clock -period 20'}.items():
            self.edit(name, text)
        self.functional = self.save_run('functional', '20260101T000001_aaaaaaaa')
        self.build = self.save_run('implementation', '20260101T000002_bbbbbbbb')
        self.post = self.save_run('post_route', '20260101T000003_cccccccc')

    def edit(self, name, content):
        self.tools.call('write_file', {'path': name, 'content': content})

    def save_run(self, stage, identifier, **changes):
        root = self.tools.folder / ('evidence' if stage == 'functional' else 'fpga/' + stage) / identifier
        files = self.tools.files()
        for name, text in files.items():
            p = root / 'source' / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding='utf-8')
        write_json(root / 'project.json', self.flow.project())
        (root / 'simulation.log').write_text('fixture only', encoding='utf-8')
        script = {'functional': 'functional.do', 'implementation': 'implement.tcl', 'post_route': 'post_route.do'}[stage]
        result = {'id': identifier, 'time': identifier, 'stage': stage, 'ok': True, 'status': 'passed',
                  'engine': 'td' if stage == 'implementation' else 'modelsim', 'executor': 'agent_script',
                  'input_tracking_version': 1,
                  'script': 'scripts/' + script, 'source_sha256': self.tools.digest(files),
                  'project_sha256': project_digest(self.tools.folder), 'tests_passed': ['fixture'],
                  'evidence_hashes': evidence_manifest(root)}
        if stage == 'implementation':
            for name in ('design.bit', 'route.sdf', 'post_route.v'):
                (root / name).write_text('fixture only', encoding='utf-8')
            result.update(timing_ok=True, board_compatible=True,
                          artifacts={name: hash_file(root / name) for name in ('design.bit', 'route.sdf', 'post_route.v')})
        if stage == 'post_route':
            result.update(implementation_id=self.build['id'], sdf_annotated=True)
        result.update(changes)
        write_json(root / 'result.json', result)
        return result

    def test_tb_change_retains_implementation_and_post_result(self):
        self.assertTrue(self.flow.summary()['ready_to_program'])
        self.edit('tb/tb.sv', 'module tb; // additional checks\nendmodule')
        summary = self.flow.summary()
        self.assertTrue(summary['implementation_ok'])
        self.assertFalse(summary['functional_ok'])
        self.assertFalse(summary['post_route_ok'])
        self.assertTrue(summary['post_route']['ok'])
        self.assertEqual(summary['post_route']['changed_inputs'], ['tb/tb.sv'])
        self.assertFalse(summary['ready_to_program'])

    def test_unrelated_script_edits_do_not_require_reimplementation(self):
        self.edit('scripts/post_route.do', 'run 100; run -all')
        self.assertTrue(self.flow.summary()['implementation_ok'])
        self.assertTrue(self.tools.summary()['verified_current'])
        self.assertFalse(self.flow.summary()['post_route_ok'])

    def test_failed_functional_rerun_does_not_invalidate_existing_implementation(self):
        self.save_run('functional', '20260101T000004_dddddddd', ok=False, status='failed')
        summary = self.flow.summary()
        self.assertTrue(summary['implementation_ok'])
        self.assertTrue(summary['post_route_ok'])
        self.assertFalse(summary['ready_to_program'])

    def test_added_rtl_marks_build_inputs_changed(self):
        self.edit('rtl/helper.v', 'module helper; endmodule')
        summary = self.flow.summary()
        self.assertFalse(summary['implementation_ok'])
        self.assertTrue(summary['implementation']['ok'])
        self.assertIn('rtl/helper.v', summary['implementation']['changed_inputs'])

    def test_cleaned_evidence_is_distinguished_from_an_input_edit(self):
        path = self.tools.folder / 'fpga/implementation' / self.build['id'] / 'design.bit'
        path.unlink()
        build = self.flow.summary()['implementation']
        self.assertTrue(build['inputs_current'])
        self.assertFalse(build['evidence_valid'])
        self.assertTrue(build['ok'])

    def test_new_build_does_not_replace_old_post_result(self):
        self.save_run('implementation', '20260101T000004_dddddddd')
        summary = self.flow.summary()
        self.assertTrue(summary['post_route']['ok'])
        self.assertFalse(summary['post_route']['implementation_current'])
        self.assertFalse(summary['post_route_ok'])

    def test_only_executed_nested_scripts_affect_new_tracked_run(self):
        self.edit('scripts/pins.tcl', '# pin bindings')
        self.edit('scripts/unrelated.tcl', '# unused helper')
        self.save_run('implementation', '20260101T000004_dddddddd', input_tracking_version=1,
                      steps=[{'name': 'source', 'args': ['source/scripts/pins.tcl'], 'status': 'passed'}])
        self.edit('scripts/unrelated.tcl', '# unused helper edited')
        self.assertTrue(self.flow.summary()['implementation_ok'])
        self.edit('scripts/pins.tcl', '# changed pin bindings')
        build = self.flow.summary()['implementation']
        self.assertFalse(build['inputs_current'])
        self.assertIn('scripts/pins.tcl', build['changed_inputs'])

    def test_legacy_dynamic_source_is_compared_conservatively(self):
        self.edit('scripts/implement.tcl', 'set helper source/scripts/pins.tcl\nsource $helper')
        self.edit('scripts/pins.tcl', '# pin bindings')
        self.save_run('implementation', '20260101T000004_dddddddd', input_tracking_version=None)
        self.edit('scripts/pins.tcl', '# changed bindings')
        self.assertFalse(self.flow.summary()['implementation_ok'])

    def test_legacy_computed_source_command_cannot_hide_script_dependencies(self):
        for text in ('{source} source/scripts/pins.tcl', 'set loader {source}; $loader source/scripts/pins.tcl'):
            with self.subTest(script=text):
                self.edit('scripts/implement.tcl', text)
                self.edit('scripts/pins.tcl', '# original bindings')
                self.save_run('implementation', '20260101T000004_dddddddd', input_tracking_version=None)
                self.edit('scripts/pins.tcl', '# changed bindings')
                self.assertFalse(self.flow.summary()['implementation_ok'])

    def test_constraint_or_deleted_rtl_changes_keep_saved_outcome(self):
        self.edit('constraints/clock.sdc', 'create_clock -period 25')
        build = self.flow.summary()['implementation']
        self.assertFalse(build['inputs_current'])
        self.assertEqual(build['changed_inputs'], ['constraints/clock.sdc'])
        (self.tools.source / 'rtl/top.v').unlink()
        self.assertIn('rtl/top.v', self.flow.summary()['implementation']['changed_inputs'])
        self.assertTrue(self.flow.summary()['implementation']['ok'])

    def test_input_edit_during_run_does_not_rewrite_saved_tool_outcome(self):
        folder = self.tools.folder / 'evidence' / self.functional['id']
        self.edit('tb/tb.sv', 'module tb; // changed while simulating\nendmodule')
        result = self.tools._finish_simulation(folder, self.functional, time.monotonic())
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'passed')
        self.assertFalse(self.tools.summary()['verified_current'])


if __name__ == '__main__':
    unittest.main()
