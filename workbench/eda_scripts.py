"""Execute agent-authored EDA scripts; retain inputs, native commands and evidence."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
import shutil
from datetime import datetime, timezone
from pathlib import Path

from tools import td_report_parser
from .agent_tools import read_json, write_json, timestamp
from .fpga_flow import FpgaFlow, BOARD, execution_lease, hash_file, project_digest
from .simulation import local_path, modelsim_verdict, evidence_manifest
from .output_io import text_page, log_contains

TD_COMMANDS = ('import_device read_verilog set_pin_assignment optimize_rtl optimize_gate place route '
               'report_clock_summary report_timing write_verilog bitgen all_inputs all_outputs '
               'get_ports get_clocks get_pins get_nets get_cells create_clock create_generated_clock '
               'set_input_delay set_output_delay set_false_path set_multicycle_path set_clock_uncertainty '
               'set_clock_latency set_max_delay set_min_delay help').split()
SIM_COMMANDS = 'vlib vlog vsim run help'.split()
OUTPUTS = ('design.bit', 'post_route.v', 'route.sdf', 'timing.rpt', 'clock_summary.rpt')

SCRIPT_SCHEMAS = [
    {'name': 'templates', 'description': 'Read small reference templates and runner conventions. Does NOT write files. Adapt them with write_file, then execute your saved scripts with run_tool.',
     'inputSchema': {'type': 'object', 'properties': {'path': {'type': 'string'}}, 'additionalProperties': False}},
    {'name': 'run_tool', 'description': 'Execute YOUR saved Tcl/DO script in actual TD or ModelSim. Reads source snapshot, returns real command records, tests/errors/reports and run_id. No hidden engineering script generation. Tools: TD implementation/query; ModelSim functional/post_route/query. Functional, implementation and post_route attempts default to unlimited; board_info.run_budget reports user-configured per-turn limits and usage. Failed attempts also count. Query also defaults to unlimited and has its own optional per-turn limit. Each stage retains its own latest run. Input edits mark affected evidence as out of date without clearing results; TB edits do not require TD reimplementation. Use inputs_current/changed_inputs and referenced implementation_id to decide which stages to rerun. Script Tcl supports loops/procs/source; no shell, network, outside paths or programming. Allowed TD: ' + ' '.join(TD_COMMANDS) + ' read_sdc source. ModelSim: ' + ' '.join(SIM_COMMANDS) + '. Use templates for supported options and output paths.',
     'inputSchema': {'type': 'object', 'properties': {'tool': {'type': 'string', 'enum': ['td', 'modelsim']}, 'stage': {'type': 'string', 'enum': ['functional', 'implementation', 'post_route', 'query']}, 'script': {'type': 'string'}}, 'required': ['tool', 'stage', 'script'], 'additionalProperties': False}},
    {'name': 'read_run', 'description': 'Read a page of raw run evidence: td.log, simulation.log, commands.jsonl, timing.rpt, clock_summary.rpt, result.json, post_route.v, route.sdf or source/<editable file>. Inspect real errors before changing files. Default 80 lines, max 200; use next_line to continue.',
     'inputSchema': {'type': 'object', 'properties': {'stage': {'type': 'string', 'enum': ['functional', 'implementation', 'post_route', 'query', 'programming']}, 'run_id': {'type': 'string'}, 'file': {'type': 'string'}, 'start_line': {'type': 'integer', 'minimum': 1}, 'max_lines': {'type': 'integer', 'minimum': 1, 'maximum': 200}}, 'required': ['stage', 'run_id', 'file'], 'additionalProperties': False}},
]


def word(value):
    text = str(value)
    for old, new in (('\\', '\\\\'), ('"', '\\"'), ('$', '\\$'), ('[', '\\['), (']', '\\]'), ('\n', '\\n'), ('\r', '\\r')):
        text = text.replace(old, new)
    return '"' + text + '"'


def tlist(items):
    return '[list ' + ' '.join(word(x) for x in items) + ']'


def command_records(folder):
    path = folder / 'commands.jsonl'
    records = {}
    if path.is_file():
        with path.open(encoding='utf-8', errors='replace') as source:
            for line in source:
                try:
                    item = json.loads(line)
                    records[item['id']] = item
                except (ValueError, KeyError):
                    continue
    return list(records.values())


def compact(value):
    """MCP replies omit historical runs and bulky hashes; UI retains full evidence."""
    if isinstance(value, list):
        return [compact(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in ('runs', 'evidence_hashes'):
            continue
        if key == 'artifacts':
            result[key] = list(item)
        elif key == 'log_tail':
            result[key] = item[-3500:]
            if len(item) > 3500:
                result['log_truncated'] = True
        elif key == 'warnings':
            result[key] = item[:4]
        elif key == 'errors':
            result[key] = item[:8]
        elif key == 'steps':
            result[key] = [{'name': s['name'], 'status': s['status']} for s in item]
        else:
            result[key] = compact(item)
    return result


class EdaScripts:
    def __init__(self, tools):
        self.tools = tools
        self.flow = FpgaFlow(tools)

    def _driver(self, folder, tool, stage, script, files):
        models = sorted((local_path(self.tools.config['td_root']) / 'sim/eg').glob('*.v')) if stage == 'post_route' else []
        paths = [('source/' + n, n.split('/')[0]) for n in files]
        paths += [(p.as_posix(), 'model') for p in models]
        if stage == 'post_route':
            paths += [('post_route.v', 'netlist'), ('route.sdf', 'sdf')]
        settings = {'engine': tool, 'stage': stage, 'entry': 'source/' + script,
                    'design_top': (self.flow.project() or {}).get('top', 'lab_top')}
        lines = [f'set {key} {word(value)}' for key, value in settings.items()]
        for key, values in (
            ('commands', TD_COMMANDS if tool == 'td' else SIM_COMMANDS),
            ('rtl_files', ['source/' + n for n in files if n.startswith('rtl/')]),
            ('tb_files', ['source/' + n for n in files if n.startswith('tb/')]),
            ('td_models', [p.as_posix() for p in models])):
            lines.append(f'set {key} {tlist(values)}')
        for path, kind in paths:
            lines.append('set allowed_inputs([file normalize ' + word(path) + ']) ' + word(kind))
        lines.append(Path(__file__).with_name('eda_bridge.tcl').read_text(encoding='utf-8'))
        (folder / 'driver.tcl').write_text('\n'.join(lines), encoding='utf-8')
        (folder / 'empty.sdc').write_text('# timing initialization\n', encoding='ascii')

    def _command(self, tool):
        if tool == 'td':
            return [str(local_path(self.tools.config['td_root']) / 'bin/td_commands_prompt.exe'), 'driver.tcl'], {}
        root = local_path(self.tools.config['modelsim_root'])
        binary = root / ('win64/vsim.exe' if os.name == 'nt' else 'linux_x86_64/vsim')
        license_path = self.tools.config.get('modelsim_license', '').strip()
        if not license_path and (root / 'licenses/LICENSE.TXT').is_file():
            license_path = str(root / 'licenses/LICENSE.TXT')
        if license_path and '@' not in license_path:
            license_path = str(local_path(license_path))
        env = {'MGLS_LICENSE_FILE': license_path, 'LM_LICENSE_FILE': license_path} if license_path else {}
        return [str(binary), '-c', '-do', 'driver.tcl'], env

    def run_tool(self, tool, stage, script):
        self.tools.active()
        if (tool, stage) not in {('td', 'implementation'), ('td', 'query'), ('modelsim', 'functional'), ('modelsim', 'post_route'), ('modelsim', 'query')}:
            raise ValueError('tool/stage: td implementation/query; modelsim functional/post_route/query')
        if not isinstance(script, str) or not script.startswith('scripts/'):
            raise ValueError('entry must be scripts/*.tcl or scripts/*.do')
        self.tools.path(script)
        files = self.tools.files()
        if script not in files:
            raise ValueError('请先用 write_file 保存运行脚本: ' + script)
        self.tools._validate_source(files)
        state = self.flow.summary()
        if stage == 'implementation' and not state['functional_ok']:
            raise ValueError('先完成当前 RTL、Testbench 和功能仿真脚本的 ModelSim 功能仿真')
        if stage == 'post_route' and not state['implementation_ok']:
            raise ValueError('后仿真需要当前版本通过 TD 实现，且网表/SDF 未改变')
        if stage in ('functional', 'post_route') and not all(any(n.startswith(g) for n in files) for g in ('rtl/', 'tb/')):
            raise ValueError('先保存 RTL 和 Testbench（tb/dut）')
        with execution_lease(self.tools.folder):
            control = self.tools.reserve_run(stage)
            if stage == 'functional':
                run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
                folder = self.tools.folder / 'evidence' / run_id
                folder.mkdir(parents=True)
                result = {'id': run_id, 'time': timestamp(), 'stage': stage, 'ok': False, 'status': 'running',
                          'turn_id': control.get('turn_id'), 'round': control['simulations'], 'verification': 'generated_self_tests',
                          'source_sha256': self.tools.digest(files), 'project_sha256': project_digest(self.tools.folder)}
                self.tools.event('simulation_started', id=run_id, round=control['simulations'])
            else:
                folder, result = self.flow._new(stage)
            result.update(engine='td' if tool == 'td' else 'modelsim', script=script, executor='agent_script',
                          input_tracking_version=1,
                          files=list(files), tests_passed=[],
                          testbench_sha256=self.tools.digest({n: c for n, c in files.items() if n.startswith('tb/')}))
            start = time.monotonic()
            try:
                for name, text in files.items():
                    path = folder / 'source' / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text, encoding='utf-8')
                write_json(folder / 'project.json', self.flow.project())
                if stage == 'post_route':
                    build = state['implementation']
                    result['implementation_id'] = build['id']
                    built = self.flow.root / 'implementation' / build['id']
                    for name in ('post_route.v', 'route.sdf'):
                        shutil.copyfile(built / name, folder / name)
                self._driver(folder, tool, stage, script, files)
                command, env = self._command(tool)
                log_name = 'td.log' if tool == 'td' else 'simulation.log'
                result['invocation'] = command
                write_json(folder / 'result.json', result)
                code, log, reason = self.tools._run(command, folder, folder / log_name, env=env,
                                                   timeout=self.tools.config['implementation_timeout_s' if tool == 'td' else 'simulation_timeout_s'])
                steps = command_records(folder)
                result.update(steps=steps, exit_code=code, log_tail=log[-16000:])
                passed = [s['name'] for s in steps if s['status'] == 'passed']
                bridge_failed = log_contains(folder / log_name, r'EDA_SCRIPT_ERROR:')
                if stage == 'implementation':
                    self._implementation(folder, result, code, log, reason, passed, bridge_failed)
                elif stage in ('functional', 'post_route'):
                    result.update(modelsim_verdict(code, log, reason, require_sdf=stage == 'post_route', log_path=folder / log_name))
                    ran = 'vsim' in passed and any(s['name'] == 'run' for s in steps)
                    result['compiled'] = 'vlog' in passed and 'vsim' in passed
                    if not ran or bridge_failed:
                        result.update(ok=False, status=reason or 'script_failed')
                    if stage == 'post_route':
                        routed = any(s['name'] == 'vlog' and s['status'] == 'passed' and
                                     any((folder / arg).resolve() == (folder / 'post_route.v').resolve() for arg in s['args']) for s in steps)
                        if not routed:
                            result.update(ok=False, status='missing_netlist')
                    if result['ok']:
                        for s in steps:
                            if s['name'] == 'run' and s['status'] == 'running':
                                s['status'] = 'passed'
                else:
                    ok = code == 0 and not reason and not bridge_failed and log_contains(folder / log_name, r'EDA_SCRIPT_COMPLETED')
                    result.update(ok=ok, status=reason or ('passed' if ok else 'failed'))
                result['log_file'] = log_name
                result['directory'] = str(folder.resolve())
                result['read_more'] = {'stage': stage, 'run_id': result['id'], 'file': log_name, 'start_line': 1, 'max_lines': 80}
            except (OSError, ValueError) as error:
                result.update(ok=False, status='tool_error', log_tail=str(error))
            result['evidence_hashes'] = evidence_manifest(folder)
            if stage == 'functional':
                return self.tools._finish_simulation(folder, result, start)
            return self.flow._finish(folder, result, start)

    def _implementation(self, folder, result, code, log, reason, passed, bridge_failed):
        names = OUTPUTS + ('driver.tcl', 'empty.sdc', 'commands.jsonl', 'project.json', 'td.log') + tuple('source/' + n for n in result['files'])
        artifacts = {n: hash_file(folder / n) for n in names if (folder / n).is_file() and (folder / n).stat().st_size}
        required = ['read_verilog', 'optimize_rtl', 'optimize_gate', 'place', 'route']
        position = 0
        for name in passed:
            if position < len(required) and name == required[position]:
                position += 1
        complete = position == len(required) and all(n in passed for n in ('report_timing', 'report_clock_summary', 'write_verilog', 'bitgen'))
        ok = code == 0 and not reason and not bridge_failed and complete and len(artifacts) == len(names) and not log_contains(folder / 'td.log', r'\b(?:ERROR|FATAL)\b')
        timing = td_report_parser.parse_timing(folder / 'timing.rpt') if 'timing.rpt' in artifacts else {}
        period = timing.get('min_period_ns')
        timing['fmax_mhz'] = 1000 / period if period and period > 0 else None
        text = (folder / 'timing.rpt').read_text(errors='replace') if 'timing.rpt' in artifacts else ''
        timing_ok = bool(re.search(r'0 errors detected\s*:\s*0 setup errors.*?0 hold errors', text)) and not re.search(r'[1-9]\d* errors detected', text)
        project = self.flow.project()
        actual_pins, clocks, actual_top = {}, [], None
        for step in result['steps']:
            if step['status'] != 'passed':
                continue
            args = step['args']
            if step['name'] == 'set_pin_assignment':
                match = re.search(r'LOCATION=([A-Z][0-9]+);', args[1])
                if match:
                    actual_pins[args[0]] = match[1]
            elif step['name'] == 'create_clock':
                clocks.append(args)
            elif step['name'] == 'read_verilog':
                actual_top = args[-1]
        expected_pins = {p: BOARD['pins'][r]['pin'] for p, r in project['pins'].items()}
        clock_ok = len(clocks) == 1 and '-period' in clocks[0]
        if clock_ok:
            try:
                clock_ok = float(clocks[0][clocks[0].index('-period') + 1]) == project['clock_period_ns']
                targets = [arg for arg in clocks[0] if arg.startswith('pins:')]
                clock_ok = clock_ok and targets == ['pins: ' + project['clock_port']]
            except (ValueError, IndexError):
                clock_ok = False
        board_ok = actual_pins == expected_pins and clock_ok and actual_top == project['top']
        result.update(ok=ok, status=reason or ('passed' if ok else 'failed'), artifacts=artifacts,
                      timing=timing, timing_ok=timing_ok, board_compatible=board_ok,
                      board_check='Recorded pin/clock/top commands must match the declared board project before programming.',
                      resources=td_report_parser.parse_resource(folder / 'td.log'),
                      scope='Reported constrained paths; external IO timing requires explicit input/output delays.')

    def read_run(self, stage, run_id, file, start_line=1, max_lines=80):
        if stage not in ('functional', 'implementation', 'post_route', 'query', 'programming') or not re.fullmatch(r'\d{8}T\d{6}_[a-f0-9]{8}', run_id or ''):
            raise ValueError('无效阶段或运行 ID')
        allowed = {'td.log', 'simulation.log', 'compile.log', 'commands.jsonl', 'driver.tcl', 'result.json', 'execution_issue.json', 'timing.rpt', 'clock_summary.rpt', 'post_route.v', 'route.sdf', 'flow.tcl', 'clock.sdc', 'project.json', 'simulate.do', 'program.log', 'download.log'}
        if file.startswith('source/'):
            self.tools.path(file[len('source/'):])
        elif file not in allowed:
            raise ValueError('不允许读取该运行文件')
        root = self.tools.folder / ('evidence' if stage == 'functional' else 'fpga/' + stage) / run_id
        path = (root / file).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
            raise ValueError('运行文件不存在或路径越界')
        if isinstance(start_line, bool) or not isinstance(start_line, int) or start_line < 1 or isinstance(max_lines, bool) or not isinstance(max_lines, int) or not 1 <= max_lines <= 200:
            raise ValueError('start_line >= 1; max_lines 1–200')
        return {'stage': stage, 'run_id': run_id, 'file': file, **text_page(path, start_line, max_lines)}
