"""Evidence-bound TD projects, post-route simulation and human board actions."""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import uuid
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from tools import td_report_parser
from .agent_tools import read_json, write_json, timestamp
from .config import WORK
from .lease import TaskLease
from .simulation import local_path, run_modelsim, evidence_manifest, evidence_valid
from .board_state import begin_download, finish_download, matches_download
from .output_io import hash_file, log_contains
from .run_inputs import input_state

BOARD = {'id': 'dmz-eg4s20', 'device': 'EG4S20BG256', 'clock_mhz': 50,
         'source': 'Repository DMZ-EG4S20 constraints; confirm board revision and wiring before programming.',
         'pins': {'clk': {'pin': 'R7', 'direction': 'input', 'note': '50 MHz oscillator'},
                  'rst_n': {'pin': 'A9', 'direction': 'input', 'note': 'active low; shared SW0'},
                  'ready_led': {'pin': 'B14', 'direction': 'output', 'note': 'active high'}}}
for group, pins, direction, note in (
    ('key_row', ['D9', 'F9', 'C10', 'E10'], 'output', 'keypad row'),
    ('key_col', ['E11', 'D11', 'C11', 'F10'], 'input', 'keypad column'),
    ('seg', ['A4', 'A6', 'B8', 'E8', 'A7', 'B5', 'A8', 'C8'], 'output', 'active high'),
    ('digit_sel', ['C9', 'B6', 'A5', 'A3'], 'output', 'active low')):
    for index, pin in enumerate(pins):
        BOARD['pins'][f'{group}[{index}]'] = {'pin': pin, 'direction': direction, 'note': note}


def project_digest(folder):
    project = read_json(Path(folder) / 'project/project.json')
    return hashlib.sha256(json.dumps(project, sort_keys=True).encode()).hexdigest() if project else None


@contextmanager
def execution_lease(scope=None, *, wait=False, check=None):
    WORK.mkdir(parents=True, exist_ok=True)
    path = WORK / '.execution.lock'
    if scope is not None:
        # Outside the experiment tree so confirmed cleanup can remove the tree
        # while holding this lease, including on Windows.
        identity = os.path.normcase(str(Path(scope).resolve()))
        path = WORK / '.experiment-locks' / (hashlib.sha256(identity.encode()).hexdigest() + '.lock')
        path.parent.mkdir(parents=True, exist_ok=True)
    lease = TaskLease(path)
    try:
        while True:
            if check:
                check()
            if lease.acquire():
                break
            if not wait:
                raise ValueError('该实验或设备的其他工具任务正在运行，请完成后重试')
            time.sleep(.1)
        if check:
            check()
        yield
    finally:
        lease.release()


class FpgaFlow:
    def __init__(self, tools):
        self.tools = tools
        self.root = tools.folder / 'fpga'

    def board_info(self):
        return {'board': BOARD, 'project': self.project(), 'flow': self.summary(),
                'run_budget': self.tools.run_budget(),
                'resource_limits': {key: self.tools.config[key] for key in ('simulation_timeout_s', 'implementation_timeout_s', 'log_limit_mib', 'waveform_limit_mib', 'max_source_files', 'max_source_file_kib')},
                'functional_backend': self.tools.config.get('simulation_backend', 'modelsim'),
                'testbench_contract': 'module tb; instantiate design as dut. Same TB for RTL and SDF; reset >=100ns, sample after propagation, not at clock edge.'}

    def project(self):
        return read_json(self.tools.folder / 'project/project.json')

    def create_project(self, top, clock_port, clock_period_ns, pins):
        for name in (top, clock_port):
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
                raise ValueError('顶层和时钟必须为简单 Verilog 标识符')
        if isinstance(clock_period_ns, bool) or not isinstance(clock_period_ns, (int, float)) or not math.isfinite(clock_period_ns) or not 1 <= clock_period_ns <= 1000:
            raise ValueError('时钟周期必须为 1–1000 ns')
        if not isinstance(pins, dict) or not 1 <= len(pins) <= len(BOARD['pins']):
            raise ValueError('请提供每一个顶层端口的板卡资源绑定')
        port_directions = {}
        for port, resource in pins.items():
            if not isinstance(port, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}(?:\[[0-9]{1,3}\])?', port):
                raise ValueError('端口名无效')
            if not isinstance(resource, str) or resource not in BOARD['pins']:
                raise ValueError('未知板卡资源: ' + str(resource))
            base = re.sub(r'\[\d+\]$', '', port)
            direction = BOARD['pins'][resource]['direction']
            if base in port_directions and port_directions[base] != direction:
                raise ValueError('同一总线的各位必须绑定方向一致的板卡资源: ' + base)
            port_directions[base] = direction
        if len(set(pins.values())) != len(pins):
            raise ValueError('一个物理引脚不能绑定多个端口')
        if pins.get(clock_port) != 'clk':
            raise ValueError('主时钟必须绑定板载 clk / R7')
        project = {'top': top, 'clock_port': clock_port, 'clock_period_ns': float(clock_period_ns),
                   'pins': pins, 'board': BOARD['id'], 'device': BOARD['device'], 'format': 'TD Tcl project'}
        write_json(self.tools.folder / 'project/project.json', project)
        self.tools.event('project_created', **project)
        return {'project': project, 'project_sha256': project_digest(self.tools.folder), 'functional_simulation_required': True}

    def _runs(self, stage):
        runs = sorted((read_json(p) for p in (self.root / stage).glob('*/result.json')), key=lambda r: r['time'])
        files = self.tools.files()
        digest, project_sha256 = self.tools.digest(files), project_digest(self.tools.folder)
        for run in runs:
            folder = self.root / stage / run['id']
            # Older post-route records omitted this field; cleanup can also remove the file.
            run['waveform'] = (folder / 'wave.vcd').is_file()
            if stage == 'implementation':
                run.setdefault('executor', 'fixed_template')
            if (run.get('status') == 'running' or not run.get('steps')) and (folder / 'commands.jsonl').is_file():
                from .eda_scripts import command_records
                run['steps'] = command_records(folder)
            run.update(input_state(folder, run, stage, files, digest, project_sha256))
        return runs

    def _new(self, stage):
        identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '_' + uuid.uuid4().hex[:8]
        folder = self.root / stage / identifier
        folder.mkdir(parents=True)
        result = {'id': identifier, 'time': timestamp(), 'stage': stage, 'ok': False, 'status': 'running',
                  'source_sha256': self.tools.digest(self.tools.files()), 'project_sha256': project_digest(self.tools.folder)}
        write_json(folder / 'result.json', result)
        self.tools.event('flow_started', stage=stage, id=identifier)
        return folder, result

    def _finish(self, folder, result, start):
        result['duration_s'] = round(time.monotonic() - start, 3)
        result['waveform'] = (folder / 'wave.vcd').is_file()
        if result['stage'] == 'implementation' and result.get('executor') != 'agent_script':
            from .eda_scripts import command_records
            result['steps'] = command_records(folder)
        self.tools.execution_diagnostics(folder, result)
        files = self.tools.files()
        result.update(input_state(folder, result, result['stage'], files, self.tools.digest(files), project_digest(self.tools.folder)))
        result['inputs_changed_during_run'] = not result['inputs_current']
        write_json(folder / 'result.json', result)
        self.tools.event('flow_finished', stage=result['stage'], id=result['id'], ok=result['ok'])
        return result

    def _current(self, result):
        return bool(result and result.get('ok') and result.get('inputs_current'))

    def _artifacts_valid(self, result):
        if not result or not result.get('artifacts'):
            return False
        folder = self.root / 'implementation' / result['id']
        try:
            return all((folder / n).resolve().is_relative_to(folder.resolve()) and
                       (folder / n).is_file() and hash_file(folder / n) == h for n, h in result['artifacts'].items())
        except OSError:
            return False

    def summary(self):
        project = self.project()
        functional = self.tools.summary()['latest']
        functional_ok = (self._current(functional) and functional.get('engine') == 'modelsim' and bool(project)
                         and evidence_valid(self.tools.folder / 'evidence' / functional['id'], functional.get('evidence_hashes')))
        builds, posts, programs = self._runs('implementation'), self._runs('post_route'), self._runs('programming')
        build = builds[-1] if builds else None
        post = posts[-1] if posts else None
        program = programs[-1] if programs else None
        build_evidence_valid = self._artifacts_valid(build)
        if build:
            build['evidence_valid'] = bool(build_evidence_valid)
        # Also validate older runs still displayed for stages a retry never reached.
        from .workflow import implementation_runs
        retained = {r['id']: r for r in implementation_runs(builds) if r and r is not build}
        for run in retained.values():
            run['evidence_valid'] = self._artifacts_valid(run)
        build_ok = self._current(build) and build_evidence_valid
        if post:
            post['evidence_valid'] = evidence_valid(self.root / 'post_route' / post['id'], post.get('evidence_hashes'))
            post['implementation_current'] = bool(build and post.get('implementation_id') == build['id'])
        post_ok = (build_ok and self._current(post) and post.get('implementation_id') == build['id'] and post.get('sdf_annotated')
                   and post['evidence_valid'])
        program_ok = (post_ok and self._current(program) and program.get('bit_sha256') == build['artifacts']['design.bit']
                      and matches_download(program.get('board_attempt'), self.tools.folder, program['bit_sha256']))
        observation = read_json(self.root / 'hardware-observation.json')
        return {'project': project, 'board': BOARD, 'functional_ok': bool(functional_ok),
                'current_project_sha256': project_digest(self.tools.folder),
                'implementation_evidence_valid': bool(build_evidence_valid),
                'implementation_ok': bool(build_ok), 'post_route_ok': bool(post_ok),
                'ready_to_program': bool(functional_ok and post_ok and build.get('timing_ok') and build.get('board_compatible', True) and project['clock_period_ns'] == 20),
                'programmed_current': bool(program_ok), 'hardware_observation': observation,
                'observation_current': bool(program_ok and observation and observation['programming_id'] == program['id']),
                'implementation': build, 'post_route': post, 'programming': program,
                'runs': {'implementation': builds, 'post_route': posts, 'programming': programs, 'query': self._runs('query')}}

    def _tcl(self, project, files):
        pin_lines = [f'set_pin_assignment {{{port}}} {{LOCATION={BOARD["pins"][res]["pin"]}; IOSTANDARD=LVCMOS33;}}' for port, res in sorted(project['pins'].items())]
        # get_ports returns individual bit pins; all_inputs/all_outputs return
        # whole bus names. Keep the exact bit-binding check, then compare
        # directions using the unique base port names returned by TD.
        inputs = {re.sub(r'\[\d+\]$', '', p) for p, r in project['pins'].items() if BOARD['pins'][r]['direction'] == 'input'}
        outputs = {re.sub(r'\[\d+\]$', '', p) for p, r in project['pins'].items() if BOARD['pins'][r]['direction'] == 'output'}
        def tcl_list(values):
            return '[list ' + ' '.join('{' + v + '}' for v in sorted(values)) + ']'
        lines = [
            'import_device eagle_s20.db -package BG256',
            'read_verilog -file {' + ' '.join('source/' + n for n in files if n.startswith('rtl/')) + '} -top ' + project['top'],
            *pin_lines, 'optimize_rtl', 'read_sdc clock.sdc',
            'set actual [lsort [lrange [get_ports *] 1 end]]',
            'set expected ' + tcl_list(project['pins']),
            'if {$actual ne $expected} {error "PORT_BINDING_MISMATCH actual=$actual expected=$expected"}',
            'set actual_inputs [lsort [lrange [all_inputs] 1 end]]',
            'set expected_inputs ' + tcl_list(inputs),
            'if {$actual_inputs ne $expected_inputs} {error "PORT_DIRECTION_MISMATCH inputs actual=$actual_inputs expected=$expected_inputs"}',
            'set actual_outputs [lsort [lrange [all_outputs] 1 end]]',
            'set expected_outputs ' + tcl_list(outputs),
            'if {$actual_outputs ne $expected_outputs} {error "PORT_DIRECTION_MISMATCH outputs actual=$actual_outputs expected=$expected_outputs"}',
            'puts "ALL_PORTS_BOUND"', 'optimize_gate', 'place', 'route',
            'report_clock_summary -file clock_summary.rpt', 'report_timing -file timing.rpt -sdf route.sdf',
            'write_verilog post_route.v -back', 'bitgen -bit design.bit', 'puts "TD_FLOW_COMPLETED"', 'exit', '']
        tracked = {'import_device', 'read_verilog', 'set_pin_assignment', 'optimize_rtl', 'read_sdc',
                   'optimize_gate', 'place', 'route', 'report_clock_summary', 'report_timing', 'write_verilog', 'bitgen'}
        journal = Path(__file__).with_name('td_journal.tcl').read_text(encoding='utf-8')
        return journal + '\n' + '\n'.join('wb_td ' + line if line.split(' ', 1)[0] in tracked else line for line in lines)

    def implement(self):
        self.tools.active()
        if not self.summary()['functional_ok']:
            raise ValueError('需要当前源码与工程约束的 ModelSim 功能仿真通过后再实现')
        with execution_lease(self.tools.folder):
            files = self.tools.files()
            self.tools._validate_source(files)
            project = self.project()
            self.tools.reserve_run('implementation')
            folder, result = self._new('implementation')
            result['executor'] = 'fixed_template'
            start = time.monotonic()
            for name, content in files.items():
                path = folder / 'source' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
            write_json(folder / 'project.json', project)
            (folder / 'clock.sdc').write_text(f'create_clock -name clk -period {project["clock_period_ns"]} [get_ports {{{project["clock_port"]}}}]\n', encoding='ascii')
            (folder / 'flow.tcl').write_text(self._tcl(project, files), encoding='ascii')
            try:
                executable = local_path(self.tools.config['td_root']) / 'bin/td_commands_prompt.exe'
                code, log, reason = self.tools._run([str(executable), 'flow.tcl'], folder, folder / 'td.log', timeout=self.tools.config['implementation_timeout_s'])
                names = ('design.bit', 'post_route.v', 'route.sdf', 'timing.rpt', 'clock_summary.rpt', 'flow.tcl', 'clock.sdc', 'project.json', 'td.log', 'commands.jsonl') + tuple('source/' + n for n in files)
                artifacts = {n: hash_file(folder / n) for n in names if (folder / n).is_file() and (folder / n).stat().st_size > 0}
                ok = (code == 0 and not reason and len(artifacts) == len(names)
                      and log_contains(folder / 'td.log', r'ALL_PORTS_BOUND')
                      and log_contains(folder / 'td.log', r'TD_FLOW_COMPLETED')
                      and not log_contains(folder / 'td.log', r'\b(?:ERROR|FATAL)\b'))
                timing = td_report_parser.parse_timing(folder / 'timing.rpt') if 'timing.rpt' in artifacts else {}
                period = timing.get('min_period_ns')
                timing['fmax_mhz'] = 1000 / period if period and period > 0 else None
                timing_text = (folder / 'timing.rpt').read_text(errors='replace') if 'timing.rpt' in artifacts else ''
                timing_ok = bool(re.search(r'0 errors detected\s*:\s*0 setup errors.*?0 hold errors', timing_text)) and not re.search(r'[1-9]\d* errors detected', timing_text)
                result.update(ok=ok, status=reason or ('passed' if ok else 'failed'), exit_code=code,
                              log_tail=log[-16000:], artifacts=artifacts, timing=timing, timing_ok=timing_ok,
                              resources=td_report_parser.parse_resource(folder / 'td.log'),
                              scope='single_clock_internal_paths; no external input/output delay constraints')
            except (OSError, ValueError) as error:
                result.update(status='tool_error', log_tail=str(error))
            return self._finish(folder, result, start)

    def post_route(self):
        self.tools.active()
        summary = self.summary()
        if not summary['implementation_ok']:
            raise ValueError('需要当前源码的完整 TD 实现及未改变的网表、SDF、bit 文件')
        with execution_lease(self.tools.folder):
            files = self.tools.files()
            self.tools._validate_source(files)
            self.tools.reserve_run('post_route')
            build = summary['implementation']
            built = self.root / 'implementation' / build['id']
            folder, result = self._new('post_route')
            result['implementation_id'] = build['id']
            start = time.monotonic()
            rtl_files, tb_files = [], []
            for name, text in files.items():
                path = folder / 'source' / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding='utf-8')
                if name.startswith('tb/'):
                    tb_files.append(path)
                elif name.startswith('rtl/'):
                    rtl_files.append(path)
            # Copy exact validated artifacts into this run; no mutable relative paths.
            for name in ('post_route.v', 'route.sdf'):
                shutil.copyfile(built / name, folder / name)
            try:
                result.update(run_modelsim(self.tools.config, folder, rtl_files, tb_files, self.tools._run,
                                           {'netlist': folder / 'post_route.v', 'sdf': folder / 'route.sdf'}))
                if not self._artifacts_valid(build):
                    result.update(ok=False, status='stale')
                result['timing_model_note'] = '保留厂商模型负时序检查等警告；后仿真通过不等于硬件实测或完整时序签核。'
                result['simulation_scope'] = 'tb.dut: routed netlist + SDF; standalone TB helper instances may use original RTL from rtl_reference.'
            except OSError as error:
                result.update(status='tool_error', log_tail=str(error))
            result['evidence_hashes'] = evidence_manifest(folder)
            return self._finish(folder, result, start)

    def program(self, confirmation, board_confirmed):
        if confirmation != 'PROGRAM SRAM' or board_confirmed is not True:
            raise ValueError('烧录前必须确认板卡型号、引脚、电压及供电，并输入 PROGRAM SRAM')
        with execution_lease(self.tools.folder), execution_lease(wait=True, check=self.tools.active):
            summary = self.summary()
            if not summary['ready_to_program']:
                raise ValueError('当前版本必须通过功能仿真、TD 实现、内部时序检查和 SDF 后仿真')
            build = summary['implementation']
            folder, result = self._new('programming')
            start = time.monotonic()
            bit = self.root / 'implementation' / build['id'] / 'design.bit'
            target = folder / 'design.bit'
            shutil.copyfile(bit, target)
            result.update(bit_sha256=hash_file(target), implementation_id=build['id'], board_confirmed=True)
            if result['bit_sha256'] != build['artifacts']['design.bit']:
                raise ValueError('bit 文件校验失败')
            (folder / 'program.tcl').write_text('download -bit {design.bit} -cable 0\nexit\n', encoding='ascii')
            result['board_attempt'] = begin_download(self.tools.folder, result['bit_sha256'])
            try:
                exe = local_path(self.tools.config['td_root']) / 'bin/td_commands_prompt.exe'
                code, log, reason = self.tools._run([str(exe), 'program.tcl'], folder, folder / 'program.log', timeout=self.tools.config['programming_timeout_s'])
                ok = code == 0 and not reason and 'download success' in log.lower() and not re.search(r'\b(?:error|fatal)\b', log, re.I)
                result.update(ok=ok, status=reason or ('passed' if ok else 'failed'), log_tail=log[-16000:], exit_code=code,
                              hardware_functional='not_tested')
            except OSError as error:
                result.update(status='tool_error', log_tail=str(error))
            result = self._finish(folder, result, start)
            finish_download(result['board_attempt'], result['ok'])
            return result

    def record_observation(self, passed, procedure, observed):
        with execution_lease(self.tools.folder), execution_lease(wait=True, check=self.tools.active):
            return self._record_observation(passed, procedure, observed)

    def _record_observation(self, passed, procedure, observed):
        summary = self.summary()
        if not summary['programmed_current']:
            raise ValueError('需要当前版本烧录成功后才能记录板上观察')
        if not isinstance(passed, bool) or any(not isinstance(x, str) or not x.strip() or len(x) > 4000 for x in (procedure, observed)):
            raise ValueError('请填写实际测试步骤和观察结果，并选择通过或失败')
        result = {'time': timestamp(), 'passed': passed, 'procedure': procedure, 'observed': observed,
                  'method': 'human_observation', 'programming_id': summary['programming']['id'],
                  'bit_sha256': summary['programming']['bit_sha256']}
        write_json(self.root / 'hardware-observation.json', result)
        return result


FLOW_SCHEMAS = [
    {'name': 'board_info', 'description': 'Read actual supported board resources, pin directions and current workflow status. Never invent extra pins.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'create_project', 'description': 'Create TD Tcl project. Bind every top bit port to a named board resource from board_info. Clock uses clk/R7; period 20ns for the board. Re-run functional simulation after constraint changes.', 'inputSchema': {'type': 'object', 'properties': {'top': {'type': 'string'}, 'clock_port': {'type': 'string'}, 'clock_period_ns': {'type': 'number'}, 'pins': {'type': 'object', 'additionalProperties': {'type': 'string'}}}, 'required': ['top', 'clock_port', 'clock_period_ns', 'pins'], 'additionalProperties': False}},
    {'name': 'implement', 'description': 'Run real TD synthesis, placement, routing, timing report, netlist/SDF and bitstream export. Requires current ModelSim functional pass and complete pin bindings. Max 3 per turn.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
    {'name': 'post_route', 'description': 'Run same locked TB on physical TD netlist with explicit SDF and EG4 models. Requires current implementation. TB top tb and DUT instance dut. Max 3 per turn. Do not claim board verification.', 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}},
]
