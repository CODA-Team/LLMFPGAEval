import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_workbench import _TEMP
from workbench.config import load_config
from workbench.agent_tools import ExperimentTools
from workbench.teaching import TeachingManager


class ExperimentNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manager = TeachingManager(self.root, load_config)
        self.addCleanup(self.manager.close)

    def test_rename_preserves_id_source_hash_and_history(self):
        old = self.manager.create('原实验')
        identifier = old['id']
        tools = ExperimentTools(self.manager.directory(identifier))
        tools.call('write_file', {'path':'rtl/counter.v','content':'module counter; endmodule'})
        before = tools.digest(tools.files())
        self.manager._update(identifier, messages=[{'role':'user','text':'设计一个计数器'}])
        value = self.manager.rename(identifier, '四位计数器')
        self.assertEqual(value['id'], identifier)
        self.assertEqual(value['title'], '四位计数器')
        self.assertEqual(value['source_path'], str(tools.source))
        self.assertEqual(tools.digest(tools.files()), before)
        self.assertEqual(value['messages'][0]['text'], '设计一个计数器')
        self.assertIn('四位计数器', (tools.folder/'实验说明.txt').read_text(encoding='utf-8'))
        index = (self.root/'sessions/实验索引.md').read_text(encoding='utf-8')
        self.assertIn(identifier, index)
        self.assertIn('四位计数器', index)

    def test_legacy_sessions_gain_labels_without_renaming_directories(self):
        identifier = self.manager.create('已有实验')['id']
        folder = self.manager.directory(identifier)
        (folder/'实验说明.txt').unlink()
        restarted = TeachingManager(self.root, load_config)
        self.addCleanup(restarted.close)
        self.assertTrue(folder.is_dir())
        self.assertIn('已有实验', (folder/'实验说明.txt').read_text(encoding='utf-8'))
        self.assertEqual(restarted.history()[0]['id'], identifier)

    def test_open_directory_uses_only_the_selected_experiment(self):
        identifier = self.manager.create('打开源码')['id']
        with patch('workbench.teaching.os.startfile', create=True) as opener:
            result = self.manager.open_source(identifier)
            opener.assert_called_once_with(str(self.manager.directory(identifier)/'source'))
        self.assertTrue(result['opened'])
        with self.assertRaises(ValueError):
            self.manager.open_source('../outside')
        with self.assertRaises(ValueError):
            self.manager.open_source('0'*32)

    def test_invalid_rename_does_not_change_the_session(self):
        identifier = self.manager.create('保留名称')['id']
        for title in ('', ' ', None, 'x'*101):
            with self.subTest(title=title), self.assertRaises(ValueError):
                self.manager.rename(identifier, title)
        self.assertEqual(self.manager.get(identifier)['title'], '保留名称')
