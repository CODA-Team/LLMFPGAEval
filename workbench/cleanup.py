"""Confirmed experiment-local deletion; previews never mutate files."""
import hashlib
import json
import os
import secrets
import shutil
import stat
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .agent_tools import read_json, write_json, timestamp
from .fpga_flow import execution_lease

CONTROL_FILES = {'session.json', 'events.jsonl', 'tool-config.json', 'opencode.json', 'control.json',
                 'cancel.flag', 'locked-testbench.json', '实验说明.txt'}


def no_link(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 1024:
        raise ValueError('清理不跟随符号链接或目录联接：' + path.name)
    return info


def file_kind(name):
    path = PurePosixPath(name)
    if path.suffix.lower() in ('.vcd', '.wlf', '.fst'): return 'waveform'
    if path.parts[0] == 'source': return 'source'
    if path.suffix.lower() in ('.log', '.rpt') or path.name == 'transcript': return 'log'
    if path.parts[0] in ('evidence', 'fpga', 'project'): return 'evidence'
    return 'other'


class ExperimentStorage:
    def __init__(self, manager):
        self.manager = manager
        self.pending = {}

    def folder(self, identifier):
        folder = self.manager.directory(identifier)
        root = self.manager.root / 'sessions'
        no_link(root)
        if not folder.is_dir(): raise ValueError('实验不存在')
        no_link(folder)
        if folder.resolve().parent != root.resolve(): raise ValueError('实验路径无效')
        no_link(folder/'session.json')
        if not (folder/'session.json').is_file(): raise ValueError('实验不存在')
        return folder

    def scan(self, folder):
        files, signature, directories = [], [], [folder]
        while directories:
            parent = directories.pop()
            for child in parent.iterdir():
                info = no_link(child)
                if not child.resolve().is_relative_to(folder.resolve()): raise ValueError('文件超出实验目录')
                name = child.relative_to(folder).as_posix()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    raise ValueError('不支持清理特殊文件：' + name)
                signature.append((name, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode))
                if stat.S_ISDIR(info.st_mode):
                    directories.append(child)
                else:
                    protected = name in CONTROL_FILES or (child.name == 'result.json' and name.startswith(('evidence/', 'fpga/')))
                    files.append({'path':name, 'bytes':info.st_size, 'modified_ns':info.st_mtime_ns,
                                  'kind':file_kind(name), 'deletable':not protected,
                                  'reason':'实验或运行记录；随整个实验一起删除' if protected else ''})
        files.sort(key=lambda item:(-item['bytes'], item['path']))
        return files, {row[0]:row for row in signature}

    def idle(self, identifier):
        if self.manager.is_active(identifier):
            raise ValueError('该实验正在运行，请停止并等待工具结束后再清理或删除')
        state = read_json(self.manager.directory(identifier)/'session.json')
        if state and state.get('status') == 'running':
            raise ValueError('该实验仍在运行，请等待结束后再删除')

    def inventory(self, identifier):
        with self.manager.lock:
            self.manager.require_available(identifier)
            folder = self.folder(identifier)
            state = read_json(folder/'session.json')
        files, _ = self.scan(folder)
        return {'id':identifier, 'title':state['title'], 'directory':str(folder), 'files':files,
                'bytes':sum(f['bytes'] for f in files), 'file_count':len(files),
                'busy':self.manager.is_active(identifier) or state.get('status') == 'running'}

    @contextmanager
    def reserved(self, identifier):
        # Reserve only this experiment. Slow scans/native history deletion must
        # not hold the manager lock and delay another experiment's Stop request.
        with self.manager.lock:
            folder = self.folder(identifier)
            self.idle(identifier)
            state = read_json(folder/'session.json')
            self.manager.maintenance[identifier] = {key: state[key] for key in
                ('id', 'title', 'created_at', 'updated_at', 'status')}
        try:
            with execution_lease(folder):
                yield folder
        finally:
            with self.manager.lock:
                self.manager.maintenance.pop(identifier, None)

    def select(self, files, paths):
        if not isinstance(paths, list) or not paths: raise ValueError('请至少选择一个文件')
        by_name = {f['path']:f for f in files}
        chosen = {}
        for name in paths:
            if (not isinstance(name,str) or not name or '\\' in name or ':' in name or '\0' in name
                    or PurePosixPath(name).is_absolute() or any(p in ('', '.', '..') for p in name.split('/'))):
                raise ValueError('文件路径无效')
            if name not in by_name or not by_name[name]['deletable']:
                raise ValueError('文件不存在或属于受保护的实验记录：' + name)
            chosen[name] = by_name[name]
        return list(chosen.values())

    @staticmethod
    def fingerprint(signature, names=None):
        rows = sorted(signature.values()) if names is None else sorted(signature[n] for n in names)
        return hashlib.sha256(json.dumps(rows).encode()).hexdigest()

    def preview(self, identifier, paths=None, whole=False):
        with self.reserved(identifier) as folder:
            files, signature = self.scan(folder)
            chosen = files if whole else self.select(files, paths)
            state = read_json(folder/'session.json')
            impacts = sorted({('source' if f['path'].startswith('source/') else 'evidence')
                              for f in chosen if f['kind'] != 'waveform'})
            preview = {'id':identifier, 'title':state['title'], 'directory':str(folder), 'whole':whole,
                       'token':secrets.token_urlsafe(32), 'file_count':len(chosen),
                       'bytes':sum(f['bytes'] for f in chosen), 'paths':[f['path'] for f in chosen], 'impacts':impacts}
            self.pending[identifier] = {**preview, 'fingerprint':self.fingerprint(signature, None if whole else preview['paths'])}
            return preview

    def confirm(self, identifier, token, whole=False):
        with self.reserved(identifier) as folder:
            preview = self.pending.get(identifier)
            if not isinstance(token, str) or not preview or not secrets.compare_digest(token, preview['token']) or whole != preview['whole']:
                raise ValueError('请先预览并确认要删除的内容')
            files, signature = self.scan(folder)
            if self.fingerprint(signature, None if whole else [n for n in preview['paths'] if n in signature]) != preview['fingerprint']:
                self.pending.pop(identifier, None)
                raise ValueError('文件已发生变化，请重新选择并确认删除')
            self.pending.pop(identifier)
            if whole: return self.delete_experiment(folder, preview)
            self.manager.stop_runtime(identifier)
            removed, errors, total = [], [], 0
            by_name = {f['path']:f for f in files}
            for name in preview['paths']:
                try:
                    no_link(folder/name)
                    if not (folder/name).resolve().is_relative_to(folder.resolve()): raise ValueError('文件超出实验目录')
                    (folder/name).unlink()
                    removed.append(name)
                    total += by_name[name]['bytes']
                except OSError as error:
                    errors.append({'path':name, 'error':str(error)})
            with (folder/'events.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps({'time':timestamp(), 'type':'files_deleted', 'paths':removed, 'bytes':total, 'errors':errors},ensure_ascii=False)+'\n')
            self.manager.source_snapshots.pop(identifier, None)
            return {'deleted_files':len(removed), 'bytes':total, 'errors':errors, 'deleted_experiment':False}

    def delete_experiment(self, folder, preview):
        # Save the small control records so a locked-file failure remains visible and retryable.
        state = read_json(folder/'session.json')
        tool_config = read_json(folder/'tool-config.json', {})
        self.manager.remove_opencode_session(state, folder)
        state['opencode_session'] = None
        self.manager.stop_runtime(preview['id'])
        self.folder(preview['id'])
        self.scan(folder)  # Recheck every absolute target and reject reparse points before rmtree.
        errors = []
        def failed(function, path, exc):
            errors.append({'path':str(Path(path).relative_to(folder)), 'error':str(exc[1])})
        # Keep control records until payload removal succeeds. A locked session.json
        # must not strand the experiment without tool-config.json (or vice versa).
        for child in folder.iterdir():
            if child.name in CONTROL_FILES: continue
            no_link(child)
            if child.resolve().parent != folder.resolve(): raise ValueError('文件超出实验目录')
            if child.is_dir():
                shutil.rmtree(child, onerror=failed)
            else:
                try: child.unlink()
                except OSError as error: errors.append({'path':child.name,'error':str(error)})
        if not errors:
            controls = sorted(folder.iterdir(), key=lambda p:(p.name in ('session.json','tool-config.json'),p.name))
            for child in controls:
                try:
                    no_link(child)
                    child.unlink()
                except OSError as error:
                    errors.append({'path':child.name,'error':str(error)})
                    break
        if not errors:
            try: folder.rmdir()
            except OSError as error: errors.append({'path':'.','error':str(error)})
        self.manager.source_snapshots.pop(preview['id'], None)
        self.manager.native_calls.pop(preview['id'], None)
        if folder.exists():
            # Recover independently and never overwrite an existing locked control
            # file just to restore identical contents. A stale native ID is retryable.
            for name, value in (('session.json',state),('tool-config.json',tool_config)):
                if not (folder/name).exists() or (name == 'session.json' and read_json(folder/name).get('opencode_session')):
                    try: write_json(folder/name,value)
                    except OSError as error: errors.append({'path':name,'error':str(error)})
            try: self.manager.write_identity(state)
            except OSError as error: errors.append({'path':'实验说明.txt','error':str(error)})
            remaining, _ = self.scan(folder)
            names = {f['path'] for f in remaining}
            removed = [f for f in preview['paths'] if f not in names]
            result = {'deleted_experiment':False, 'deleted_files':len(removed), 'bytes':None, 'errors':errors}
        else:
            result = {'deleted_experiment':True, 'deleted_files':preview['file_count'], 'bytes':preview['bytes'], 'errors':errors}
        with self.manager.lock:
            self.manager.write_session_index()
        return result
