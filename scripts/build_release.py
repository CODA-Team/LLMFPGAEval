"""Build a source-only release from an explicit allowlist; never copy runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from pathlib import Path

REQUIRED_FILES = (
    '.gitignore', 'README.md', 'LICENSE', 'app.py', 'config.py', 'start.ps1', 'start.bat',
    'workbench.example.json', 'docs/RELEASE.md', 'docs/examples/models.json',
    'scripts/build_release.py', 'scripts/install-opencode.ps1',
)
SOURCE_TREES = {
    'workbench': {'.py', '.tcl', '.html', '.css', '.js'},
    'anlogic': {'.py', '.tcl'},
    'tools': {'.py', '.md'},
    'exp_calculator': {'.v', '.sv', '.vh', '.svh', '.sdc', '.adc', '.tcl', '.sh', '.md'},
    'exp4_reaction_timer': {'.v', '.sv', '.vh', '.svh', '.sdc', '.adc', '.tcl', '.sh', '.md'},
    'tests': {'.py', '.cjs'},
}
SKIP_DIRS = {'.git', '.tools', '.venv', 'node_modules', '__pycache__', 'work', 'build', 'dist',
             '.codex', '.agents', '.superpowers', 'licenses', 'private'}
SECRET = re.compile(rb'(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|'
                    rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----)')
MAX_SOURCE_BYTES = 8 * 1024 * 1024  # Packaging guard only; not an EDA runtime limit.


def no_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
        raise ValueError('Source release does not follow links or junctions: '+str(path))
    return info


def collect_sources(root):
    root = Path(root).absolute()
    no_link(root)
    files = set()
    for name in REQUIRED_FILES:
        path = root/name
        for parent in reversed(path.relative_to(root).parents):
            no_link(root/parent)
        no_link(path)
        if not path.is_file():
            raise ValueError('Required release file missing: '+name)
        files.add(name)
    for directory, extensions in SOURCE_TREES.items():
        tree = root/directory
        if not tree.exists():
            continue
        no_link(tree)
        for parent, directories, names in os.walk(tree, followlinks=False):
            directories[:] = [name for name in directories if name.lower() not in SKIP_DIRS]
            for name in directories:
                no_link(Path(parent)/name)
            for name in names:
                path = Path(parent)/name
                relative = path.relative_to(root).as_posix()
                fixture = relative.startswith('tests/fixtures/') and path.suffix.lower() in {'.log', '.rpt', '.md'}
                if path.suffix.lower() in extensions or fixture:
                    no_link(path)
                    files.add(relative)
    sources = {}
    for name in sorted(files):
        path = root/name
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError('Unexpected large source file; review before release: '+name)
        data = path.read_bytes()
        data.decode('utf-8-sig')  # Source distribution contains text files only.
        if SECRET.search(data):
            raise ValueError('Possible credential in release file: '+name)
        sources[name] = data
    return sources


def build_release(root, output, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9][A-Za-z0-9.-]*)?', version):
        raise ValueError('Version must be a filename-safe semantic version, such as 0.1.0')
    root, output = Path(root).resolve(), Path(output).resolve()
    if any(output.is_relative_to(root/name) for name in SOURCE_TREES):
        raise ValueError('Release output must be outside the included source directories')
    package = 'rtl-lab-v'+version
    folder, archive = output/package, output/(package+'-source.zip')
    checksum = archive.with_suffix('.zip.sha256')
    for path in (folder, archive, checksum):
        if path.exists():
            raise FileExistsError('Refusing to overwrite existing release: '+str(path))
    sources = collect_sources(root)
    manifest = {'name': 'RTL Lab', 'version': version, 'type': 'source-only',
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}}
    sources['RELEASE_MANIFEST.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode('utf-8')
    output.mkdir(parents=True, exist_ok=True)
    folder.mkdir()
    for name, data in sources.items():
        target = folder/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package_file:
        for name, data in sources.items():
            entry = zipfile.ZipInfo(package+'/'+name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            package_file.writestr(entry, data)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(digest+'  '+archive.name+'\n', encoding='ascii')
    return {'folder': str(folder), 'archive': str(archive), 'checksum_file': str(checksum),
            'sha256': digest, 'files': len(sources), 'source_bytes': sum(map(len, sources.values())),
            'archive_bytes': archive.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', default='0.1.0')
    parser.add_argument('--output', default='dist', help='Output directory (relative to source root)')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = Path(args.output).expanduser()
    if not output.is_absolute():
        output = root/output
    print(json.dumps(build_release(root, output, args.version), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
