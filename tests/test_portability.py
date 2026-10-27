"""A fresh clone can start before any tools or remote server are configured."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench import config
from workbench.transport import remote_execute


class PortabilityTests(unittest.TestCase):
    def test_release_defaults_do_not_contact_a_personal_server(self):
        for field in ('ssh_host', 'ssh_identity', 'orfs_root', 'remote_work_root', 'remote_library_path'):
            self.assertEqual(config.DEFAULTS[field], '', field)

    def test_fpga_only_configuration_allows_empty_remote_fields(self):
        values = dict(config.DEFAULTS, ssh_host='', orfs_root='', remote_work_root='')
        self.assertEqual(config.validate_config(values)['ssh_host'], '')
        with patch('workbench.transport.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'ORFS.*配置'):
                remote_execute(values, {'action': 'check'})
            run.assert_not_called()

    def test_partial_remote_config_fails_before_starting_ssh(self):
        values = dict(config.DEFAULTS, ssh_host='test-host', orfs_root='', remote_work_root='/home/student/jobs')
        with patch('workbench.transport.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError, 'ORFS.*配置'):
                remote_execute(values, {'action': 'check'})
            run.assert_not_called()

    def test_saved_relative_tool_paths_are_resolved_against_source_root(self):
        with tempfile.TemporaryDirectory(dir=_TEMP) as directory:
            root = Path(directory)
            saved = {'td_root': 'tools/td', 'modelsim_root': 'tools/modelsim',
                     'modelsim_license': '27000@licenses.example', 'iverilog': 'tools/iverilog.exe',
                     'vvp': 'tools/vvp.exe', 'ssh_identity': 'keys/example-key'}
            path = root/'workbench.local.json'
            path.write_text(json.dumps(saved), encoding='utf-8')
            with patch.object(config, 'ROOT', root), patch.object(config, 'CONFIG_FILE', path):
                result = config.load_config()
            for field in ('td_root', 'modelsim_root', 'iverilog', 'vvp', 'ssh_identity'):
                self.assertEqual(Path(result[field]), root/saved[field], field)
            self.assertEqual(result['modelsim_license'], saved['modelsim_license'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), saved)

    def test_unconfigured_asic_is_rejected_without_leaving_a_queued_job(self):
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory(dir=_TEMP) as directory:
            runner = Runner(Path(directory))
            values = copy.deepcopy(config.DEFAULTS)
            values.update(ssh_host='', orfs_root='', remote_work_root='')
            try:
                with patch('workbench.runner.load_config', return_value=values):
                    with self.assertRaisesRegex(ValueError, 'ORFS.*配置'):
                        runner.create('reaction_timer', ['asic'])
                self.assertFalse(runner.busy)
                self.assertFalse(runner.history())
            finally:
                runner.busy = False

    def test_relative_opencode_path_can_be_configured_and_reported_from_another_cwd(self):
        from workbench.teaching import TeachingManager
        with tempfile.TemporaryDirectory(dir=_TEMP) as directory:
            root = Path(directory)
            executable = root/'vendor/opencode.exe'
            executable.parent.mkdir()
            executable.write_bytes(b'fixture, not executed')
            with patch.object(config, 'ROOT', root):
                manager = TeachingManager(root/'data', lambda: copy.deepcopy(config.DEFAULTS))
                try:
                    manager.configure({'executable': 'vendor/opencode.exe'})
                    self.assertEqual(Path(manager.settings['executable']), executable)
                    self.assertTrue(manager.public_settings()['installed'])
                    # Previously persisted relative values must use the same base.
                    manager.settings['executable'] = 'vendor/opencode.exe'
                    self.assertTrue(manager.public_settings()['installed'])
                finally:
                    manager.close()

    def test_existing_icarus_command_names_still_resolve_through_path(self):
        with tempfile.TemporaryDirectory(dir=_TEMP) as directory:
            root = Path(directory)
            path = root/'workbench.local.json'
            path.write_text(json.dumps({'iverilog': 'iverilog', 'vvp': 'vvp'}), encoding='utf-8')
            installed = {name: str(root/'installed'/f'{name}.exe') for name in ('iverilog', 'vvp')}
            with patch.object(config, 'ROOT', root), patch.object(config, 'CONFIG_FILE', path), \
                    patch('workbench.config.shutil.which', side_effect=installed.get):
                values = config.load_config()
            self.assertEqual(values['iverilog'], installed['iverilog'])
            self.assertEqual(values['vvp'], installed['vvp'])


if __name__ == '__main__':
    unittest.main()
