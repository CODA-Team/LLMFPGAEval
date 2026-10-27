import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.fpga_flow import execution_lease
from workbench.teaching import TeachingManager


class FakeRuntime:
    def __init__(self, *args, **kwargs):
        self.started = threading.Event()
        self.done = threading.Event()
        self.stopped = threading.Event()
        self.folder = None
        self.process = None
        self.prompts = []

    def start(self, folder, *args):
        self.folder = folder
        self.started.set()

    def request(self, method, path, body=None):
        sid = 'ses_' + self.folder.name
        if path == '/session': return {'id': sid}
        if path.endswith('/prompt_async'):
            self.prompts.append(body)
            return None
        if path == '/session/status': return {sid: {'type': 'idle' if self.done.is_set() else 'busy'}}
        if path.endswith('/message'):
            if not self.prompts: return []
            return [{'info': {'id': 'answer', 'role': 'assistant', 'finish': 'stop' if self.done.is_set() else None,
                             'time': {'completed': 1} if self.done.is_set() else {}},
                     'parts': [{'type': 'text', 'text': self.folder.name}] if self.done.is_set() else []}]
        raise AssertionError(path)

    def stop(self): self.stopped.set()


class ParallelExperimentsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.factory = patch('workbench.teaching.OpenCodeRuntime', side_effect=FakeRuntime)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.manager = TeachingManager(self.root, load_config)
        self.addCleanup(self.manager.close)
        self.manager.key = 'test-only'
        self.a = self.manager.create('A')['id']
        self.b = self.manager.create('B')['id']

    def wait(self, condition):
        deadline = time.monotonic() + 4
        while not condition() and time.monotonic() < deadline: time.sleep(.02)
        self.assertTrue(condition())

    def test_two_experiments_progress_and_stop_independently(self):
        self.manager.send(self.a, 'A task')
        self.manager.send(self.b, 'B task')
        self.assertEqual(set(self.manager.active_ids), {self.a, self.b})
        a, b = self.manager.runtimes[self.a], self.manager.runtimes[self.b]
        self.assertIsNot(a, b)
        self.assertTrue(a.started.wait(2)); self.assertTrue(b.started.wait(2))
        with self.assertRaisesRegex(ValueError, '该实验'):
            self.manager.send(self.a, 'duplicate')
        self.manager.stop(self.a)
        self.wait(lambda: self.a not in self.manager.active_ids)
        self.assertEqual(self.manager.get(self.a)['status'], 'cancelled')
        self.assertFalse(b.stopped.is_set())
        self.assertIn(self.b, self.manager.active_ids)
        b.done.set()
        self.wait(lambda: not self.manager.busy)
        self.assertEqual(self.manager.get(self.b)['messages'][-1]['text'], self.b)
        self.assertTrue(b.stopped.is_set())

    def test_optional_limit_and_cleanup_of_idle_experiment(self):
        self.assertIsNone(self.manager.settings['max_parallel_experiments'])
        self.manager.configure({'max_parallel_experiments': 1})
        self.manager.send(self.a, 'A task')
        with self.assertRaisesRegex(ValueError, '并行'):
            self.manager.send(self.b, 'B task')
        source = self.manager.directory(self.b)/'source/rtl/unused.v'
        source.parent.mkdir(exist_ok=True, parents=True)
        source.write_text('module unused; endmodule')
        preview = self.manager.cleanup_preview(self.b, ['source/rtl/unused.v'])
        self.manager.cleanup_confirm(self.b, preview['token'])
        self.assertFalse(source.exists())
        self.assertIn(self.a, self.manager.active_ids)
        self.assertFalse(self.manager.runtimes[self.a].stopped.is_set())
        with self.assertRaisesRegex(ValueError, '运行'):
            self.manager.cleanup_preview(self.a, whole=True)

    def test_shutdown_stops_every_job_and_rejects_new_send(self):
        self.manager.send(self.a, 'A task'); self.manager.send(self.b, 'B task')
        self.manager.close()
        self.assertTrue(all(r.stopped.is_set() for r in self.manager.runtimes.values()))
        self.assertFalse(self.manager.busy)
        with self.assertRaisesRegex(ValueError, '关闭'):
            self.manager.send(self.a, 'late task')

    def test_slow_cleanup_does_not_block_another_experiments_stop(self):
        self.manager.send(self.a, 'A task')
        preview = self.manager.cleanup_preview(self.b, whole=True)
        entered, release = threading.Event(), threading.Event()
        errors = []
        def native_delete(*args):
            entered.set()
            release.wait(5)
        def clean():
            try: self.manager.cleanup_confirm(self.b, preview['token'], whole=True)
            except Exception as error: errors.append(error)
        with patch.object(self.manager, 'remove_opencode_session', side_effect=native_delete):
            worker = threading.Thread(target=clean);worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(ValueError, '运行中'):
                    self.manager.send(self.b, 'cannot modify the deletion target')
                before = time.monotonic()
                self.manager.stop(self.a)
                self.assertLess(time.monotonic()-before,.5)
                self.assertTrue(self.manager.jobs[self.a].cancelled.is_set())
            finally:
                release.set();worker.join(3)
        self.assertFalse(errors, errors)
        self.assertFalse(self.manager.directory(self.b).exists())

    def test_software_leases_are_local_and_device_wait_is_cancellable(self):
        a, b = self.manager.directory(self.a), self.manager.directory(self.b)
        with execution_lease(a):
            with execution_lease(b): pass
            with self.assertRaisesRegex(ValueError, '运行'):
                with execution_lease(a): pass
        checked = threading.Event()
        def cancel_check():
            checked.set()
            raise ValueError('cancelled before hardware access')
        with execution_lease():
            with self.assertRaisesRegex(ValueError, 'cancelled'):
                with execution_lease(wait=True, check=cancel_check): pass
        self.assertTrue(checked.is_set())

    def test_polling_and_rename_cannot_recreate_an_experiment_being_deleted(self):
        self.manager._update(self.b, status='passed')
        folder = self.manager.directory(self.b)
        preview = self.manager.cleanup_preview(self.b, whole=True)
        removed, release = threading.Event(), threading.Event()
        errors = []
        original = shutil.rmtree
        def pause_after_source_removal(path, *args, **kwargs):
            original(path, *args, **kwargs)
            if Path(path) == folder/'source':
                removed.set()
                release.wait(5)
        def clean():
            try: self.manager.cleanup_confirm(self.b, preview['token'], whole=True)
            except Exception as error: errors.append(error)
        with patch('workbench.cleanup.shutil.rmtree', side_effect=pause_after_source_removal):
            worker = threading.Thread(target=clean); worker.start()
            try:
                self.assertTrue(removed.wait(2))
                for operation in (lambda: self.manager.get(self.b),
                                  lambda: self.manager.rename(self.b, 'do not resurrect'),
                                  lambda: self.manager.open_source(self.b),
                                  lambda: self.manager.read_tool(self.b, 'list_files', {})):
                    with self.assertRaisesRegex(ValueError, '清理'):
                        operation()
                history = {s['id']: s for s in self.manager.history()}
                self.assertEqual(history[self.b]['title'], 'B')
                self.manager.create('C while B is deleting')
                self.assertFalse((folder/'source').exists())
            finally:
                release.set(); worker.join(3)
        self.assertFalse(errors, errors)
        self.assertFalse(folder.exists())
        self.assertNotIn(self.b, self.manager.maintenance_ids)

    def test_history_and_index_tolerate_a_removed_session_file(self):
        from workbench.agent_tools import read_json
        target = self.manager.directory(self.b)/'session.json'
        def disappear(path, *args, **kwargs):
            return None if Path(path) == target else read_json(path, *args, **kwargs)
        with patch('workbench.teaching.read_json', side_effect=disappear):
            self.assertEqual([s['id'] for s in self.manager.history()], [self.a])
            self.manager.write_session_index()
        self.assertNotIn(self.b, (self.manager.root/'sessions/实验索引.md').read_text(encoding='utf-8'))


if __name__ == '__main__': unittest.main()
