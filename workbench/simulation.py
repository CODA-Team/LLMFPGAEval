"""ModelSim adapter; exit status alone is not a simulation verdict."""
from __future__ import annotations

import os
import hashlib
import re
import io
import shutil
from pathlib import Path

from .config import ROOT, local_path
from .output_io import hash_file


def evidence_manifest(folder):
    folder = Path(folder)
    candidates = list((folder / 'source').rglob('*')) + [folder / n for n in
        ('compile.log', 'simulation.log', 'simulate.do', 'post_route.v', 'route.sdf', 'driver.tcl', 'commands.jsonl', 'project.json')]
    candidates.append(folder / 'execution_issue.json')
    return {p.relative_to(folder).as_posix(): hash_file(p)
            for p in candidates if p.is_file()}


def evidence_valid(folder, manifest):
    if not manifest:
        return False
    folder = Path(folder).resolve()
    try:
        return all((folder / n).resolve().is_relative_to(folder) and (folder / n).is_file()
                   and hash_file(folder / n) == digest for n, digest in manifest.items())
    except OSError:
        return False


def modelsim_verdict(code, log, reason=None, require_sdf=False, log_path=None):
    # `log` is a short preview. Verdicts scan all on-disk lines, including early
    # failures and the final test markers even when the transcript is very large.
    checks, warnings, error_lines = [], [], []
    marker = errors = license_error = sdf_loaded = sdf_bad = False
    warning_count = 0
    source = Path(log_path).open(encoding='utf-8', errors='replace') if log_path is not None else io.StringIO(log)
    with source:
        for raw in source:
            line = re.sub(r'^# ?', '', raw).rstrip('\r\n')
            match = re.match(r'^TEST_PASS\s+([^\r\n]+)$', line)
            if match:
                checks.append(match[1])
            marker = marker or bool(re.match(r'^ALL_TESTS_PASSED\s*$', line))
            if (re.match(r'^\s*(?:\*\*\s*)?(?:ERROR|FATAL|FAIL|FAILED|TEST_FAIL)\b', line, re.I)
                    or re.match(r'^Errors:\s*[1-9]\d*', line, re.I)):
                errors = True
                if len(error_lines) < 100:
                    error_lines.append(line[:2000])
            license_error = license_error or bool(re.search(r'unable to checkout a license|license checkout failed|invalid license|no valid license|license server.*(?:failed|cannot)', line, re.I))
            sdf_loaded = sdf_loaded or 'SDF Backannotation Successfully Completed.' in line
            sdf_bad = sdf_bad or bool(re.search(r'(?:SDF|backannotation).*(?:failed|failure|not found|cannot|unable|unmatched|not annotated)', line, re.I))
            if line.startswith('** Warning:'):
                warning_count += 1
                if len(warnings) < 100:
                    warnings.append(line[:2000])
    ok = code == 0 and bool(checks) and marker and not errors and not reason and not license_error
    if require_sdf:
        ok = ok and sdf_loaded and not sdf_bad
    status = reason or ('license_error' if license_error else 'passed' if ok else
                        'failed' if code != 0 or errors or (require_sdf and (not sdf_loaded or sdf_bad)) else 'inconclusive')
    return {'ok': bool(ok), 'status': status, 'exit_code': code, 'tests_passed': checks,
            'warnings': warnings, 'warning_count': warning_count, 'errors': error_lines,
            'sdf_annotated': sdf_loaded, 'log_tail': log[-16000:]}


def run_modelsim(config, folder, rtl_files, tb_files, run, post_route=None):
    folder = Path(folder)
    root = local_path(config['modelsim_root'])
    binary = root / ('win64' if os.name == 'nt' else 'linux_x86_64')
    suffix = '.exe' if os.name == 'nt' else ''
    def exe(name):
        return str(binary / (name + suffix))
    env = {}
    license_path = config.get('modelsim_license', '').strip()
    if not license_path and (root / 'licenses/LICENSE.TXT').is_file():
        license_path = str(root / 'licenses/LICENSE.TXT')
    if license_path:
        # FlexNet also accepts port@host and lists of license servers.
        if '@' not in license_path:
            license_path = str(local_path(license_path))
        env = {'MGLS_LICENSE_FILE': license_path, 'LM_LICENSE_FILE': license_path}
    commands = [[exe('vlib'), 'work']]
    if post_route:
        # Standalone submodule checks in the unchanged TB still need their RTL.
        # Keep it out of work so it cannot overwrite the routed top. Explicit
        # work-first lookup below binds tb.dut to the netlist and resolves only
        # missing helper modules from this reference library.
        if rtl_files:
            commands.append([exe('vlib'), 'rtl_reference'])
            commands.append([exe('vlog'), '-sv', '-work', 'rtl_reference', *map(str, rtl_files)])
        libraries = sorted((local_path(config['td_root']) / 'sim/eg').glob('*.v'))
        if not libraries:
            raise OSError('TD EG4 simulation library not found: sim/eg/*.v')
        # Vendor Verilog contains identifiers which are keywords in SystemVerilog.
        commands.append([exe('vlog'), '-work', 'work', *map(str, libraries)])
        commands.append([exe('vlog'), '-work', 'work', str(post_route['netlist'])])
        commands.append([exe('vlog'), '-sv', '-work', 'work', *map(str, tb_files)])
    else:
        commands.append([exe('vlog'), '-sv', '-work', 'work', *map(str, rtl_files), *map(str, tb_files)])
    result = {'engine': 'modelsim', 'stage': 'post_route' if post_route else 'functional',
              'ok': False, 'tests_passed': [], 'compiled': False}
    (folder / 'compile.log').write_bytes(b'')
    for index, command in enumerate(commands):
        log_path = folder / f'compile-{index}.log'
        code, log, reason = run(command, folder, log_path, env=env)
        with log_path.open('rb') as source, (folder / 'compile.log').open('ab') as target:
            shutil.copyfileobj(source, target, length=65536)
        result.update(compile_exit_code=code, log_tail=log[-16000:])
        verdict = modelsim_verdict(code, log, reason, log_path=log_path)
        if code != 0 or reason or verdict['errors'] or verdict['status'] == 'license_error':
            result['errors'] = verdict['errors']
            result['status'] = reason or ('license_error' if verdict['status'] == 'license_error' else 'compile_failed')
            return result
    result['compiled'] = True
    (folder / 'simulate.do').write_text('onerror {quit -code 1}\nonbreak {quit -code 1}\nrun -all\nquit -code 2\n', encoding='ascii')
    command = [exe('vsim'), '-c', '-t', '1ps', '-onfinish', 'exit']
    if post_route:
        if rtl_files:
            command += ['-L', 'work', '-L', 'rtl_reference']
        command += ['-sdfmax', '/tb/dut=' + str(post_route['sdf'])]
    command += ['work.tb']
    if post_route:
        command += ['work.glbl']
    command += ['-do', 'simulate.do']
    code, log, reason = run(command, folder, folder / 'simulation.log', env=env)
    result.update(modelsim_verdict(code, log, reason, require_sdf=bool(post_route), log_path=folder / 'simulation.log'))
    if result['status'] == 'inconclusive':
        result['explanation'] = '需要自检后输出 TEST_PASS <名称>，全部通过后输出 ALL_TESTS_PASSED。'
    return result
