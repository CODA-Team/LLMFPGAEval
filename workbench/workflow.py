"""Latest attempted result for each stage; freshness never erases a result."""


def implementation_states(build, active=True):
    if not build:
        return ['pending'] * 4
    if build.get('ok'):
        if build.get('evidence_valid') is False:
            return ['stale'] * 4
        return ['passed', 'passed', 'failed' if build.get('timing_ok') is False else 'passed', 'passed']
    if build.get('status') == 'stale':
        return ['stale'] * 4
    if build.get('executor') == 'fixed_template' and not build.get('steps') and build.get('status') != 'running':
        # Older generated flows did not journal commands. A failed run may have
        # reached any stage; never assert that later stages were not attempted.
        return ['unknown'] * 4
    statuses = ['pending'] * 4
    groups = [({'import_device', 'read_verilog', 'read_sdc', 'optimize_rtl', 'optimize_gate'}, {'optimize_rtl', 'optimize_gate'}),
              ({'place'}, {'place'}),
              ({'route', 'report_timing', 'report_clock_summary'}, {'route'}),
              ({'write_verilog', 'bitgen'}, {'write_verilog', 'bitgen'})]
    steps = build.get('steps', [])
    running = ('running' if active else 'cancelled') if build['status'] == 'running' else 'failed'
    for index, (names, required) in enumerate(groups):
        matches = [s for s in steps if s.get('name') in names]
        if any(s['status'] == 'failed' for s in matches):
            statuses[index] = 'failed'
        elif any(s['status'] == 'running' for s in matches):
            statuses[index] = running
        elif required <= {s['name'] for s in matches if s['status'] == 'passed'}:
            statuses[index] = 'passed'
        elif matches or index == 0 and not any(s.get('name') in set().union(*(g[0] for g in groups)) for s in steps):
            statuses[index] = running
    if build['status'] != 'running' and statuses == ['passed'] * 4:
        statuses[3] = 'failed'
    if statuses[2] == 'passed' and build.get('timing_ok') is False:
        statuses[2] = 'failed'
    if build['status'] != 'running' and build.get('evidence_valid') is False:
        statuses = ['stale' if status == 'passed' else status for status in statuses]
    return statuses


def implementation_runs(builds):
    """A retry only replaces stages it actually reached, including failures."""
    selected = [None] * 4
    for run in builds:
        for i, status in enumerate(implementation_states(run)):
            if status != 'pending':
                selected[i] = run
    return selected


def stage_results(state):
    sim, flow = state.get('simulation', {}), state.get('flow', {})
    active = state.get('status') == 'running'
    messages = state.get('messages', [])
    start = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=0)
    editing = active and any(t.get('name') in ('rtl_write_file', 'rtl_create_project', 'write', 'edit', 'apply_patch')
                             and t.get('status') in ('running', 'pending')
                             for m in messages[start:] for t in m.get('tools', []))
    functional = sim.get('latest')
    prepared = 'passed' if flow.get('project') else 'prepared' if functional else 'ready' if state.get('files') else 'pending'

    def row(stage, run, status=None):
        if status is None:
            if not run:
                status = 'pending'
            elif run.get('status') == 'running':
                status = 'running' if active else 'cancelled'
            elif run.get('ok'):
                status = 'stale' if run.get('evidence_valid') is False else 'passed'
            else:
                status = 'stale' if run.get('status') == 'stale' else 'failed'
        return {'stage': stage, 'status': status, 'run': run}

    rows = [row('project', None, 'running' if editing else prepared), row('functional', functional)]
    builds = flow.get('runs', {}).get('implementation') or ([flow['implementation']] if flow.get('implementation') else [])
    for i, run in enumerate(implementation_runs(builds)):
        rows.append(row('implementation', run, implementation_states(run, active)[i]))
    rows.append(row('post_route', flow.get('post_route')))
    return rows


def stage_states(state):
    return [row['status'] for row in stage_results(state)]
