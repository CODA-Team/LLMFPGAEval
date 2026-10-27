import importlib.util
import math
import tempfile
import unittest
from pathlib import Path

_TEMP = Path(__file__).resolve().parents[1] / "work/test-temp"
_TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(_TEMP)


class MetricsTests(unittest.TestCase):
    def api(self):
        self.assertIsNotNone(importlib.util.find_spec('workbench'), 'workbench implementation is missing')
        from workbench.metrics import parse_orfs_metrics
        return parse_orfs_metrics

    def test_orfs_units_and_provenance(self):
        parse = self.api()
        r = parse({'finish__timing__setup__ws': -0.125,
                   'finish__timing__hold__ws': 0.03,
                   'finish__design__instance__area__stdcell': 120.5,
                   'finish__design__instance__count__stdcell': 30,
                   'finish__power__total': 0.002,
                   'finish__timing__fmax': 125000000})
        self.assertEqual(r['power']['total_mw'], 2.0)
        self.assertEqual(r['power']['method'], 'vectorless_estimate')
        self.assertEqual(r['timing']['fmax_mhz'], 125.0)
        self.assertEqual(r['timing']['wns_ns'], -0.125)
        self.assertEqual(r['resources']['cell_area_um2'], 120.5)

    def test_missing_and_nonfinite_are_not_zero(self):
        r = self.api()({'finish__power__total': float('nan'), 'finish__timing__setup__ws': 'Infinity'})
        self.assertIsNone(r['power']['total_mw'])
        self.assertIsNone(r['timing']['wns_ns'])
        self.assertIsNone(r['resources']['cell_count'])

    def test_scaling_overflow_is_unknown_and_drc_uses_routing_stage(self):
        r = self.api()({'finish__power__total': 1e308, 'detailedroute__route__drc_errors': 3})
        self.assertIsNone(r['power']['total_mw'])
        self.assertEqual(r['route_drc_count'], 3)


class ArchiveTests(unittest.TestCase):
    def test_remote_rejects_archive_traversal(self):
        self.assertIsNotNone(importlib.util.find_spec('workbench'), 'workbench implementation is missing')
        import io, zipfile
        from workbench.remote_worker import extract_snapshot
        data = io.BytesIO()
        with zipfile.ZipFile(data, 'w') as z:
            z.writestr('../outside.txt', 'bad')
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                extract_snapshot(data.getvalue(), Path(td) / 'snapshot')
            self.assertFalse((Path(td) / 'outside.txt').exists())

    def test_snapshot_hash_tracks_rtl_not_generated_logs(self):
        self.assertIsNotNone(importlib.util.find_spec('workbench'), 'workbench implementation is missing')
        from workbench.config import snapshot
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root/'src/rtl').mkdir(parents=True)
            (root/'src/rtl/top.v').write_text('module top; endmodule')
            (root/'src/flow.tcl').write_text('puts done')
            (root/'src/run.log').write_text('old')
            p = {'path': str(root/'src'), 'rtl': ['rtl/top.v'], 'top': 'top', 'clock_period_ns': 20}
            a = snapshot(p, root/'one')
            (root/'src/run.log').write_text('new')
            b = snapshot(p, root/'two')
            self.assertEqual(a['rtl_sha256'], b['rtl_sha256'])
            self.assertFalse((root/'one/run.log').exists())
            (root/'src/rtl/top.v').write_text('module top(input clk); endmodule')
            c = snapshot(p, root/'three')
            self.assertNotEqual(a['rtl_sha256'], c['rtl_sha256'])


class ExecutionTests(unittest.TestCase):
    def test_program_validation_excludes_other_runner_power_update(self):
        from unittest.mock import patch
        from workbench.runner import Runner, file_hash
        with tempfile.TemporaryDirectory() as td:
            first = Runner(Path(td))
            second = None
            try:
                job = first.create("reaction_timer", ["fpga"])
                folder = first.directory(job["id"])
                bit = folder / "test.bit"
                bit.write_bytes(b"test bitstream")
                evidence = folder / "measurement.txt"
                evidence.write_text("test evidence")
                job.update(status="completed", simulation={"ok": True},
                           fpga={"ok": True, "bit": "test.bit", "bit_sha256": file_hash(bit)})
                first.save(job)
                first.busy = False
                second = Runner(Path(td))
                values = {"total_mw": 10, "frequency_mhz": 50, "method": "measured",
                          "scope": "fpga_chip", "activity": "test", "evidence_path": str(evidence)}
                original_hash = file_hash
                def while_hashing(path):
                    if Path(path).resolve() == bit.resolve():
                        with self.assertRaises(ValueError):
                            second.record_power(job["id"], values)
                    return original_hash(path)
                with patch("workbench.runner.file_hash", side_effect=while_hashing), patch("workbench.runner.execute", return_value=(0, "download success", 0)):
                    self.assertTrue(first.program(job["id"], "PROGRAM SRAM")["ok"])
                self.assertFalse(first.busy)
                self.assertFalse(second.busy)
                second.record_power(job["id"], values)
                saved = first.get(job["id"])
                self.assertEqual(saved["fpga"]["power"]["total_mw"], 10)
                self.assertTrue(saved["programming"]["ok"])
            finally:
                first.busy = False
                if second is not None:
                    second.busy = False

    def test_power_evidence_update_excludes_programming(self):
        import shutil
        from unittest.mock import patch
        from workbench.runner import Runner, file_hash
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(Path(td))
            job = runner.create("reaction_timer", ["fpga"])
            try:
                folder = runner.directory(job["id"])
                bit = folder / "test.bit"
                bit.write_bytes(b"bits")
                evidence = folder / "measurement.txt"
                evidence.write_text("measurement record")
                job.update(status="completed", simulation={"ok": True},
                           fpga={"ok": True, "bit": "test.bit", "bit_sha256": file_hash(bit)})
                runner.save(job)
                runner.busy = False
                original = shutil.copyfile
                def while_copying(*args, **kwargs):
                    with self.assertRaises(ValueError):
                        runner.program(job["id"], "PROGRAM SRAM")
                    return original(*args, **kwargs)
                with patch("workbench.runner.shutil.copyfile", side_effect=while_copying), patch("workbench.runner.execute", side_effect=AssertionError("interleaved program")):
                    result = runner.record_power(job["id"], {"total_mw": 10, "frequency_mhz": 50,
                        "method": "measured", "scope": "fpga_chip", "activity": "test",
                        "evidence_path": str(evidence)})
                self.assertEqual(result["total_mw"], 10)
                self.assertFalse(runner.busy)
            finally:
                runner.busy = False

    def test_storage_failure_releases_task_lease(self):
        from unittest.mock import patch
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(Path(td))
            try:
                job = runner.create("reaction_timer", ["fpga"])
                with patch.object(runner, "save", side_effect=OSError("disk full")):
                    with self.assertRaises(OSError):
                        runner.run(job["id"])
                self.assertFalse(runner.busy)
            finally:
                runner.busy = False

    def test_failed_rtl_simulation_cannot_program_hardware(self):
        from unittest.mock import patch
        from workbench.runner import Runner, file_hash
        with tempfile.TemporaryDirectory() as td:
            runner = Runner(Path(td))
            job = runner.create("reaction_timer", ["fpga"])
            try:
                bit = runner.directory(job["id"]) / "test.bit"
                bit.write_bytes(b"bitstream")
                job.update(status="failed", simulation={"ok": False},
                           fpga={"ok": True, "bit": "test.bit", "bit_sha256": file_hash(bit)})
                runner.save(job)
                runner.busy = False
                with patch("workbench.runner.execute", side_effect=AssertionError("unsafe hardware execution")):
                    with self.assertRaises(ValueError):
                        runner.program(job["id"], "PROGRAM SRAM")
                self.assertFalse(runner.busy, "validation failure must release the task lease")
            finally:
                runner.busy = False

    def test_timeout_stops_descendant_process(self):
        import sys, time
        from workbench.runner import execute
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            child = root / "child.py"
            marker = root / "child-finished"
            child.write_text("import time\nfrom pathlib import Path\ntime.sleep(2)\nPath(" + repr(str(marker)) + ").write_text('done')\n")
            parent = root / "parent.py"
            parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable," + repr(str(child)) + "])\ntime.sleep(20)\n")
            code, _, _ = execute([sys.executable, str(parent)], root, root / "run.log", .5)
            self.assertEqual(code, 124)
            time.sleep(2)
            self.assertFalse(marker.exists(), "a timed-out task left a child running")

    def test_second_runner_preserves_active_job_and_cannot_start_another(self):
        from workbench.runner import Runner
        with tempfile.TemporaryDirectory() as td:
            first = Runner(Path(td))
            second = None
            try:
                job = first.create("reaction_timer", ["fpga"])
                second = Runner(Path(td))
                self.assertEqual(second.get(job["id"])["status"], "queued")
                with self.assertRaises(ValueError):
                    second.create("reaction_timer", ["fpga"])
            finally:
                first.busy = False
                if second is not None:
                    second.busy = False

    def test_zero_exit_with_td_error_is_failed(self):
        self.assertIsNotNone(importlib.util.find_spec('workbench.runner'), 'runner missing')
        from workbench.runner import td_success
        with tempfile.TemporaryDirectory() as td:
            bit = Path(td) / 'result.bit'
            bit.write_bytes(b'bits')
            self.assertFalse(td_success(0, 'RUN-8418 ERROR: license missing', bit))
            self.assertFalse(td_success(0, 'FLOW FINISHED', Path(td) / 'missing.bit'))
            self.assertTrue(td_success(0, 'FLOW FINISHED', bit))

    def test_ssh_configuration_cannot_inject_options_or_shell(self):
        from workbench.config import DEFAULTS, validate_config
        for host in ['-oProxyCommand=bad', 'user@host;touch /bad', 'user@host\nbad']:
            with self.assertRaises(ValueError):
                validate_config(dict(DEFAULTS, ssh_host=host))

    def test_simulation_requires_explicit_completion_and_no_failure(self):
        self.assertIsNotNone(importlib.util.find_spec('workbench.runner'), 'runner missing')
        from workbench.runner import sim_success
        self.assertFalse(sim_success(0, 'PASS: first check'))
        self.assertFalse(sim_success(0, 'ERROR: wrong result\nALL_TESTS_PASSED'))
        self.assertFalse(sim_success(1, 'ALL_TESTS_PASSED'))
        self.assertTrue(sim_success(0, 'PASS: first check\nALL_TESTS_PASSED'))


if __name__ == '__main__':
    unittest.main()
