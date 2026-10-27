"""Disk evidence may be large; previews and validation do not load whole files."""
import hashlib
import re
from pathlib import Path

RESOURCE_DEFAULTS = {
    'simulation_timeout_s': None, 'implementation_timeout_s': None, 'programming_timeout_s': None,
    'max_source_files': None, 'max_source_file_kib': None,
    'log_limit_mib': 1024, 'waveform_limit_mib': 1024,
}


def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def log_tail(path, size=16000):
    with Path(path).open('rb') as source:
        source.seek(max(0, source.seek(0, 2) - size))
        return source.read(size).decode('utf-8', errors='replace').replace('\r\n', '\n')


def log_contains(path, pattern, flags=0):
    expression = re.compile(pattern, flags)
    with Path(path).open('r', encoding='utf-8', errors='replace') as source:
        return any(expression.search(line) for line in source)


def text_page(path, start_line=1, max_lines=80):
    if type(start_line) is not int or start_line < 1 or type(max_lines) is not int or not 1 <= max_lines <= 200:
        raise ValueError('start_line >= 1; max_lines 1–200 (per response; request next_line for more)')
    selected, size, total, full, truncated = [], 0, 0, False, False
    with Path(path).open('r', encoding='utf-8', errors='replace') as source:
        for total, line in enumerate(source, 1):
            if total < start_line or full:
                continue
            line = line.rstrip('\r\n')
            if len(selected) >= max_lines or (selected and size + len(line) > 16000):
                full = True
                continue
            selected.append(line[:16000]); size += len(selected[-1])
            truncated = truncated or len(line) > 16000
    next_line = start_line + len(selected)
    return {'content': '\n'.join(selected), 'start_line': start_line, 'total_lines': total,
            'next_line': next_line, 'has_more': next_line <= total, 'line_truncated': truncated}
