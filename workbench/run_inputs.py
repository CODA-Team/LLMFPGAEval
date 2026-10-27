"""Compare stage inputs with their saved snapshot, separately from run outcome."""
from pathlib import Path


def input_state(folder, run, stage, files, digest, project_sha256):
    project_changed = run.get('project_sha256') != project_sha256
    if run.get('source_sha256') == digest:
        changed = ['project/project.json'] if project_changed else []
        return {'inputs_current': not changed, 'changed_inputs': changed}

    root = (Path(folder) / 'source').resolve()
    if not root.is_dir():
        # Old index-only records cannot prove which subset of inputs was used.
        return {'inputs_current': False, 'changed_inputs': ['源码快照不可用']}
    try:
        snapshot = {}
        for group in ('rtl', 'tb', 'constraints', 'scripts'):
            for path in (root / group).glob('*'):
                if path.is_file() and path.resolve().is_relative_to(root) and not path.is_symlink():
                    snapshot[path.relative_to(root).as_posix()] = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return {'inputs_current': False, 'changed_inputs': ['源码快照不可用']}

    groups = {'rtl'}
    if stage in ('functional', 'post_route'):
        groups.add('tb')
    if stage == 'implementation':
        groups.add('constraints')
    if stage not in ('functional', 'implementation', 'post_route'):
        groups.update(('tb', 'constraints', 'scripts'))
    scripts = {run['script']} if run.get('script') else set()
    for step in run.get('steps', []):
        if step.get('name') in ('source', 'read_sdc'):
            for value in step.get('args', []):
                path = (Path(folder) / value).resolve()
                if path.is_relative_to(root):
                    scripts.add(path.relative_to(root).as_posix())
    # Older agent scripts allow computed Tcl commands, so absence of a literal
    # "source" token does not prove independence. New journals track the calls.
    if run.get('executor') == 'agent_script' and not run.get('input_tracking_version'):
        groups.update(('scripts', 'constraints'))
    names = {n for n in snapshot.keys() | files.keys() if n.split('/')[0] in groups} | scripts
    changed = sorted(n for n in names if snapshot.get(n) != files.get(n))
    if project_changed:
        changed.append('project/project.json')
    return {'inputs_current': not changed, 'changed_inputs': changed}
