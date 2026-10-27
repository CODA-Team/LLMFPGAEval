import copy
import json
import http.client
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.teaching import TeachingManager
from workbench.simulation import evidence_manifest, evidence_valid


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.manager = TeachingManager(Path(self.temp.name) / 'teaching', load_config)
        self.addCleanup(self.manager.close)
        self.identifier = self.manager.create('计算器')['id']
        self.folder = self.manager.directory(self.identifier)
        self.wave = self.folder / 'fpga/post_route/run-1/wave.vcd'
        self.wave.parent.mkdir(parents=True)
        self.wave.write_bytes(b'w' * 4096)
        (self.wave.parent/'simulation.log').write_text('ALL_TESTS_PASSED\n')
        self.source = self.folder/'source/rtl/top.v'
        self.source.parent.mkdir(exist_ok=True)
        self.source.write_text('module top; endmodule\n')

    def preview(self, paths=None, whole=False):
        return self.manager.cleanup_preview(self.identifier, paths=paths, whole=whole)

    def test_inventory_and_wave_cleanup_preserve_source_and_evidence(self):
        storage = self.manager.storage(self.identifier)
        files = {f['path']:f for f in storage['files']}
        name = self.wave.relative_to(self.folder).as_posix()
        self.assertEqual(files[name]['bytes'], 4096)
        self.assertEqual(files[name]['kind'], 'waveform')
        self.assertFalse(files['session.json']['deletable'])
        manifest = evidence_manifest(self.wave.parent)
        preview = self.preview([name])
        self.assertTrue(self.wave.exists(), 'preview must never delete')
        self.assertEqual(preview['bytes'], 4096)
        result = self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)
        self.assertEqual(result['deleted_files'], 1)
        self.assertFalse(self.wave.exists())
        self.assertTrue(self.source.exists())
        self.assertTrue(evidence_valid(self.wave.parent, manifest))
        self.assertIn('files_deleted', (self.folder/'events.jsonl').read_text())

    def test_log_cleanup_invalidates_evidence(self):
        manifest = evidence_manifest(self.wave.parent)
        preview = self.preview(['fpga/post_route/run-1/simulation.log'])
        self.assertIn('evidence', preview['impacts'])
        self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)
        self.assertFalse(evidence_valid(self.wave.parent, manifest))

    def test_confirmation_is_bound_to_selection_experiment_and_operation(self):
        other = self.manager.create('另一个实验')['id']
        preview = self.preview(['source/rtl/top.v'])
        for identifier, token, whole in ((self.identifier,'',False),(other,preview['token'],False),
                                          (self.identifier,preview['token'],True)):
            with self.assertRaises(ValueError):
                self.manager.cleanup_confirm(identifier, token, whole=whole)
        self.assertTrue(self.source.exists())
        self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)
        with self.assertRaises(ValueError):
            self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)

    def test_changed_selection_and_new_whole_project_files_require_new_preview(self):
        preview = self.preview(['source/rtl/top.v'])
        self.source.write_text('module changed; endmodule\n')
        with self.assertRaisesRegex(ValueError, '变化|改变|重新'):
            self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)
        self.assertTrue(self.source.exists())
        preview = self.preview(whole=True)
        (self.folder/'new-file.txt').write_text('new')
        with self.assertRaises(ValueError):
            self.manager.cleanup_confirm(self.identifier, preview['token'], whole=True)
        self.assertTrue(self.folder.exists())

    def test_invalid_paths_and_control_files_never_delete(self):
        for name in ('../settings.json',str(self.wave),'source/../session.json','session.json',
                     'source\\..\\session.json','source/rtl/top.v:stream','source', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.preview([name])
        with self.assertRaises(ValueError):
            self.preview([])
        with self.assertRaises(ValueError):
            self.manager.cleanup_preview('../', whole=True)
        self.assertTrue(self.source.exists())

    def test_link_cannot_escape_project(self):
        outside = Path(self.temp.name)/'outside'
        outside.mkdir()
        (outside/'keep.txt').write_text('keep')
        link = self.folder/'source/linked'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('symlink privilege unavailable')
        for whole, paths in ((False,['source/linked/keep.txt']),(True,None)):
            with self.assertRaises(ValueError):
                self.manager.cleanup_preview(self.identifier, paths=paths, whole=whole)
        self.assertEqual((outside/'keep.txt').read_text(), 'keep')

    def test_running_experiment_rechecked_after_confirmation(self):
        preview = self.preview(['source/rtl/top.v'])
        self.manager.jobs[self.identifier] = object()
        try:
            with self.assertRaises(ValueError):
                self.preview(whole=True)
            with self.assertRaises(ValueError):
                self.manager.cleanup_confirm(self.identifier, preview['token'], whole=False)
            self.assertTrue(self.source.exists())
        finally:
            self.manager.jobs.clear()

    def test_another_active_experiment_does_not_block_idle_cleanup(self):
        other = self.manager.create('另一个运行中的实验')['id']
        preview = self.preview(whole=True)
        self.manager.jobs[other] = object()
        try:
            result = self.manager.cleanup_confirm(self.identifier, preview['token'], whole=True)
            self.assertTrue(result['deleted_experiment'])
            self.assertTrue(self.manager.directory(other).exists())
            self.assertIn(other, self.manager.active_ids)
        finally:
            self.manager.jobs.clear()

    def test_delete_whole_project_removes_every_file_but_keeps_other_experiment_and_key(self):
        other = self.manager.create('保留的实验')['id']
        keep = self.manager.directory(other)/'keep.txt'
        keep.write_text('keep')
        private = self.manager.root/'private'
        private.mkdir(exist_ok=True)
        (private/'sentinel').write_text('private')
        self.manager._update(self.identifier, opencode_session='ses_fixture')
        self.manager.source_snapshots[self.identifier] = {'a':'b'}
        preview = self.preview(whole=True)
        self.assertGreaterEqual(preview['file_count'], 4)
        with patch.object(self.manager, 'remove_opencode_session') as remove:
            result = self.manager.cleanup_confirm(self.identifier, preview['token'], whole=True)
        remove.assert_called_once()
        self.assertTrue(result['deleted_experiment'])
        self.assertFalse(self.folder.exists())
        self.assertTrue(keep.exists())
        self.assertTrue((private/'sentinel').exists())
        self.assertNotIn(self.identifier,self.manager.source_snapshots)
        self.assertNotIn(self.identifier,[s['id'] for s in self.manager.history()])
        self.assertNotIn(self.identifier,(self.manager.root/'sessions/实验索引.md').read_text(encoding='utf-8'))
        with self.assertRaises(ValueError): self.manager.get(self.identifier)
        self.assertFalse(self.folder.exists(), 'get after deletion must not recreate directory')

    def test_partial_file_failure_reports_what_was_actually_deleted(self):
        preview = self.preview(['fpga/post_route/run-1/wave.vcd','source/rtl/top.v'])
        real_unlink = Path.unlink
        def locked(path, *args, **kwargs):
            if path == self.wave: raise PermissionError('locked by simulator')
            return real_unlink(path,*args,**kwargs)
        with patch.object(Path,'unlink',locked):
            result = self.manager.cleanup_confirm(self.identifier,preview['token'],whole=False)
        self.assertEqual(result['deleted_files'],1)
        self.assertEqual(len(result['errors']),1)
        self.assertTrue(self.wave.exists())
        self.assertFalse(self.source.exists())

    def test_native_history_failure_does_not_remove_project(self):
        self.manager._update(self.identifier, opencode_session='ses_fixture')
        preview = self.preview(whole=True)
        with patch.object(self.manager,'remove_opencode_session',side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError):
                self.manager.cleanup_confirm(self.identifier,preview['token'],whole=True)
        self.assertTrue(self.source.exists())

    @unittest.skipUnless(os.name == 'nt','Windows junction check')
    def test_windows_junction_is_rejected_without_touching_target(self):
        outside = Path(self.temp.name)/'junction-target'
        outside.mkdir()
        keep = outside/'keep.txt'
        keep.write_text('keep')
        link = self.folder/'source/junction'
        quote = lambda value: "'"+str(value).replace("'","''")+"'"
        made = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',
                               'New-Item -ItemType Junction -Path '+quote(link)+' -Target '+quote(outside)+' | Out-Null'],
                              capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(made.returncode,0,made.stderr.decode(errors='replace'))
        try:
            with self.assertRaises(ValueError): self.preview(whole=True)
            with self.assertRaises(ValueError): self.preview(['source/junction/keep.txt'])
            self.assertEqual(keep.read_text(),'keep')
        finally:
            link.rmdir()

    @unittest.skipUnless(os.name == 'nt','Windows file sharing check')
    def test_locked_whole_project_remains_visible_and_can_be_retried(self):
        with self.wave.open('rb'):
            preview = self.preview(whole=True)
            result = self.manager.cleanup_confirm(self.identifier,preview['token'],whole=True)
        self.assertFalse(result['deleted_experiment'])
        self.assertTrue(result['errors'])
        self.assertEqual(self.manager.get(self.identifier)['title'],'计算器')
        preview = self.preview(whole=True)
        self.assertTrue(self.manager.cleanup_confirm(self.identifier,preview['token'],whole=True)['deleted_experiment'])

    @unittest.skipUnless(os.name == 'nt','Windows file sharing check')
    def test_locked_control_files_do_not_break_history_or_retry(self):
        for name in ('session.json','tool-config.json'):
            with self.subTest(name=name):
                identifier = self.manager.create('锁定控制文件测试')['id']
                folder = self.manager.directory(identifier)
                self.manager._update(identifier,status='passed')
                with (folder/name).open('rb'):
                    preview = self.manager.cleanup_preview(identifier,whole=True)
                    result = self.manager.cleanup_confirm(identifier,preview['token'],whole=True)
                self.assertFalse(result['deleted_experiment'])
                self.assertTrue(result['errors'])
                self.assertEqual(self.manager.get(identifier)['title'],'锁定控制文件测试')
                self.assertIn(identifier,[s['id'] for s in self.manager.history()])
                preview = self.manager.cleanup_preview(identifier,whole=True)
                self.assertTrue(self.manager.cleanup_confirm(identifier,preview['token'],whole=True)['deleted_experiment'])


class CleanupHttpTests(unittest.TestCase):
    def test_endpoints_require_confirmation_and_local_auth_and_do_not_recreate_deleted_project(self):
        from workbench.server import make_server
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory(dir=_TEMP) as td:
            server = make_server(Runner(Path(td)),0)
            worker = threading.Thread(target=server.serve_forever,daemon=True)
            worker.start()
            def call(method,path,body=None,headers=None):
                connection = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
                connection.request(method,path,body=json.dumps(body).encode() if body is not None else None,
                                   headers=headers or {})
                response = connection.getresponse()
                status,data = response.status,json.loads(response.read())
                connection.close()
                return status,data
            try:
                manager = server.teaching
                identifier = manager.create('HTTP cleanup')['id']
                folder = manager.directory(identifier)
                (folder/'source/rtl').mkdir(exist_ok=True)
                (folder/'source/rtl/top.v').write_text('module top; endmodule')
                auth = {'X-Workbench-Token':server.token,'Content-Type':'application/json'}
                for route in ('cleanup-preview','cleanup','delete-preview','delete'):
                    self.assertEqual(call('POST','/api/teaching/'+route,{'id':identifier})[0],403)
                    self.assertEqual(call('POST','/api/teaching/'+route,{'id':identifier},
                                          {**auth,'Origin':'https://untrusted.test'})[0],403)
                self.assertEqual(call('GET','/api/teaching/storage?id='+identifier)[0],200)
                self.assertEqual(call('POST','/api/teaching/delete',{'id':identifier},auth)[0],400)
                _,preview = call('POST','/api/teaching/delete-preview',{'id':identifier},auth)
                status, result = call('POST','/api/teaching/delete',{'id':identifier,'token':preview['token']},auth)
                self.assertEqual(status,200)
                self.assertTrue(result['deleted_experiment'])
                self.assertEqual(call('GET','/api/teaching/file?id='+identifier+'&path=rtl/top.v')[0],400)
                self.assertEqual(call('GET','/api/teaching/session?id='+identifier)[0],400)
                self.assertFalse(folder.exists())
            finally:
                server.shutdown();server.server_close();manager.close();worker.join()


if __name__ == '__main__':
    unittest.main()
