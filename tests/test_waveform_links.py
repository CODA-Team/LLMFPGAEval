import json
import tempfile
import time
import unittest
from pathlib import Path

from tests.test_workbench import _TEMP
from workbench.agent_tools import ExperimentTools, write_json
from workbench.config import DEFAULTS
from workbench.fpga_flow import FpgaFlow


class WaveformLinkTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=_TEMP)
        self.addCleanup(temporary.cleanup)
        self.tools = ExperimentTools(temporary.name, DEFAULTS)
        self.flow = FpgaFlow(self.tools)

    def record(self, stage, **extra):
        parent = 'evidence' if stage == 'functional' else 'fpga/' + stage
        folder = self.tools.folder / parent / '20261026T000000_12345678'
        folder.mkdir(parents=True)
        record = {'id': folder.name, 'stage': stage, 'time': '2026-10-26T00:00:00Z',
                  'status': 'passed', 'ok': True, 'source_sha256': self.tools.digest({}),
                  'project_sha256': None, **extra}
        write_json(folder / 'result.json', record)
        return folder

    def test_legacy_post_route_download_is_discovered_without_rewriting_evidence(self):
        folder = self.record('post_route')
        original = (folder / 'result.json').read_bytes()
        (folder / 'wave.vcd').write_text('$timescale 1ns $end\n#0\n', encoding='ascii')
        summary = self.flow.summary()
        self.assertTrue(summary['post_route'].get('waveform'))
        self.assertTrue(summary['runs']['post_route'][0].get('waveform'))
        self.assertEqual((folder / 'result.json').read_bytes(), original)

    def test_download_visibility_tracks_disk_after_waveform_cleanup(self):
        for stage in ('functional', 'post_route'):
            with self.subTest(stage=stage):
                folder = self.record(stage, waveform=True)
                wave = folder / 'wave.vcd'
                wave.write_text('#0\n', encoding='ascii')
                current = lambda: (self.tools.summary()['latest'] if stage == 'functional'
                                   else self.flow.summary()['post_route'])
                self.assertTrue(current()['waveform'])
                wave.unlink()
                self.assertFalse(current()['waveform'])
                self.assertTrue(json.loads((folder / 'result.json').read_text())['waveform'])

    def test_new_post_route_run_records_waveform_presence(self):
        self.tools.begin_turn('waveform-link')
        folder, result = self.flow._new('post_route')
        (folder / 'wave.vcd').write_text('#0\n', encoding='ascii')
        result.update(status='passed', ok=True)
        finished = self.flow._finish(folder, result, time.monotonic())
        self.assertTrue(finished.get('waveform'))
        self.assertTrue(json.loads((folder / 'result.json').read_text()).get('waveform'))
