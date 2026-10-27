"""Persistent web conversations; verification always comes from tool evidence."""
from __future__ import annotations

import ctypes
import copy
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .agent_tools import ExperimentTools, RUN_LIMIT_FIELDS, RUN_COUNTERS, RUN_LABELS, read_json, timestamp, write_json
from .config import ROOT, bundled_tool_path, local_path
from .opencode import MODEL_MAX_OUTPUT_TOKENS, OpenCodeRuntime
from .fpga_flow import FpgaFlow
from .output_io import RESOURCE_DEFAULTS
from .model_connection import (DEFAULT_CONNECTION, CONNECTION_FIELDS, normalize_connection,
                               credential_target, export_connection, api_base, output_cap)
from .model_catalog import parse_catalog, profile_connection, connection_profile
from .teaching_jobs import ExperimentJob

TOOL_SETTINGS = ('simulation_backend', 'modelsim_root', 'modelsim_license', 'td_root', 'iverilog', 'vvp', *RESOURCE_DEFAULTS)
CONTINUATION_PROMPT = """[平台自动续跑]
上一段回复达到长度上限、为空或停在工具调用后，尚未正常完成。沿用当前会话、已有文件和工具结果，继续完成学生最近的需求。
不要重复读取已知资料或从头规划整个工程；直接执行下一项具体操作，分步保存 RTL、测试和脚本并调用工具验证。若任务已完成，简要说明实际结果。
仍然使用本轮剩余时间和工具配额，不能降低测试标准；确实受阻时给出具体原因和已有证据。"""


def incomplete_reply(entry):
    """OpenCode completing a message does not imply the model finished its work."""
    finish = entry.get("info", {}).get("finish")
    if finish == "length":
        return "模型回复达到输出长度上限，未完整输出"
    if finish == "tool-calls":
        return "助手在工具调用后中断，未继续执行"
    if not any(part.get("type") == "text" and part.get("text", "").strip()
               or part.get("type") == "tool" for part in entry.get("parts", [])):
        return "模型本次只返回了空白内容，没有可展示的回复或工具操作"
    return None


def protect(data, decrypt=False):
    if os.name != "nt":
        raise ValueError("当前只有 Windows 支持加密保存；可使用仅本次运行或环境变量")
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    function = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    ok = function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise OSError("Windows 凭据加密操作失败")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree(target.data)


class TeachingManager:
    def __init__(self, root, config_loader):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config_loader = config_loader
        self.lock = threading.RLock()
        self.jobs = {}
        self.runtimes = {}
        self.maintenance = {}
        self.closing = False
        self.source_snapshots = {}
        self.native_calls = {}
        from .cleanup import ExperimentStorage
        self.storage_manager = ExperimentStorage(self)
        self.settings = {**DEFAULT_CONNECTION, "max_output_tokens": None,
                         "max_agent_steps": None, "turn_timeout_minutes": None,
                         "max_continuations": None, "max_message_chars": None, "max_parallel_experiments": None,
                         **{field: None for field in RUN_LIMIT_FIELDS.values()},
                         "executable": bundled_tool_path('opencode/node_modules/opencode-ai/bin/opencode.exe')}
        self.settings.update(read_json(self.root / "settings.json", {}))
        self.settings['executable'] = str(local_path(self.settings['executable']))
        self.settings.update(normalize_connection(self.settings))
        if self.settings.get('model_config'):
            catalog = parse_catalog(self.settings['model_config'])
            alias = self.settings.get('active_profile', next(iter(catalog)))
            self.settings.update(profile_connection(alias, catalog[alias]))
            self.settings['max_output_tokens'] = output_cap(self.settings['model_settings'])
        else:
            alias = self.settings['provider_name']
            catalog = {alias:connection_profile(self.settings)}
        self.settings.update(model_config=catalog, active_profile=alias)
        self.saved_keys = {}
        vault = self.root / 'private/model-keys.dpapi'
        if vault.exists():
            try:
                self.saved_keys = json.loads(protect(vault.read_bytes(), decrypt=True).decode())
            except (OSError, ValueError):
                pass
        saved = self.root / "private/deepseek.dpapi"
        if saved.exists():
            try:
                self.saved_keys.setdefault(self.settings['api_key_env'], protect(saved.read_bytes(), decrypt=True).decode())
            except (OSError, ValueError):
                pass
        self.keys = dict(self.saved_keys)
        self.key = self.keys.get(self.settings['api_key_env']) or os.environ.get(self.settings['api_key_env'], '')
        if not self.key and credential_target(self.settings) == credential_target(DEFAULT_CONNECTION):
            self.key = os.environ.get('RTL_LAB_API_KEY', '')
        for path in (self.root / "sessions").glob("*/session.json"):
            state = read_json(path)
            if state["status"] == "running":
                state.update(status="interrupted", error="服务重启，本轮已中断；可发送消息继续。")
                write_json(path, state)
            self.write_identity(state)
        self.write_session_index()

    @property
    def busy(self):
        with self.lock:
            return bool(self.jobs or self.maintenance)

    @property
    def active_ids(self):
        with self.lock:
            return list(self.jobs)

    @property
    def active_id(self):
        # Compatibility for old clients; new clients consume active_ids.
        return next(iter(self.active_ids), None)

    def is_active(self, identifier):
        with self.lock:
            return identifier in self.jobs or identifier in self.maintenance

    @property
    def maintenance_ids(self):
        with self.lock:
            return list(self.maintenance)

    def require_available(self, identifier):
        # Call while holding self.lock so a cleanup cannot start between this
        # check and a handler that creates directories or writes metadata.
        if identifier in self.maintenance:
            raise ValueError('该实验文件正在清理，请稍后重试')

    def runtime_for(self, identifier):
        with self.lock:
            self.directory(identifier)  # Validate the identifier before constructing paths.
            if identifier not in self.runtimes:
                self.runtimes[identifier] = OpenCodeRuntime(self.root / 'runtime',
                    log_directory=self.root / 'runtime/instances' / identifier)
            return self.runtimes[identifier]

    def stop_runtime(self, identifier):
        runtime = self.runtimes.get(identifier)
        if runtime:
            runtime.stop()

    def public_settings(self):
        return {**self.settings, "key_present": bool(self.key), "key_saved": self.settings['api_key_env'] in self.saved_keys,
                'profile_credentials': {name:self.credential_status(name) for name in
                                        ({entry['api_key_env'] for entry in self.settings['model_config'].values()} | self.keys.keys())},
                'connection_json': export_connection(self.settings),
                "tools": {k: self.config_loader()[k] for k in TOOL_SETTINGS},
                "installed": local_path(self.settings["executable"]).is_file(), "busy": self.busy,
                "limits": {**{RUN_COUNTERS[stage]: self.settings[field] for stage, field in RUN_LIMIT_FIELDS.items()},
                           "agent_steps": self.settings["max_agent_steps"],
                           "turn_timeout_s": self.settings["turn_timeout_minutes"] * 60
                           if self.settings["turn_timeout_minutes"] is not None else None,
                           "automatic_continuations": self.settings['max_continuations'],
                           "model_max_output_tokens": self.settings['model_output_tokens'],
                           "effective_output_tokens": self.settings["max_output_tokens"] or self.settings['model_output_tokens']}}

    def credential_status(self, name):
        local = self.keys.get(name) or (self.key if name == self.settings['api_key_env'] else None)
        environment = os.environ.get(name)
        return {'present':bool(local or environment), 'saved':name in self.saved_keys,
                'source':'saved' if name in self.saved_keys else 'environment' if environment and local in (None, environment)
                         else 'memory' if local else 'missing'}

    def redact(self, text):
        for key in {self.key, *self.keys.values()}:
            if key:
                text = text.replace(key, "[REDACTED]")
        return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}", "[REDACTED]", text)

    def configure(self, body):
        with self.lock:
            if self.busy:
                raise ValueError("请停止当前实验后再修改 API 设置")
            if set(body) - {"model", "executable", "api_key", "remember_key", "clear_key", "max_output_tokens", 'model_config', 'active_profile',
                            "max_agent_steps", "turn_timeout_minutes", "max_continuations", "max_message_chars", "max_parallel_experiments", *RUN_LIMIT_FIELDS.values(), *CONNECTION_FIELDS}:
                raise ValueError("API 配置字段无效")
            catalog_request = 'model_config' in body or 'active_profile' in body
            catalog = parse_catalog(body.get('model_config', self.settings['model_config']))
            alias = body.get('active_profile', self.settings.get('active_profile'))
            if 'active_profile' not in body and alias not in catalog:
                alias = next(iter(catalog))
            if not isinstance(alias, str) or alias not in catalog:
                raise ValueError('当前模型名称不在 JSON 配置中')
            connection = profile_connection(alias, catalog[alias]) if catalog_request else normalize_connection(self.settings, body)
            executable = body.get("executable", self.settings["executable"])
            if not isinstance(executable, str):
                raise ValueError("请指定已安装的 opencode.exe")
            executable = str(local_path(executable))
            if not Path(executable).is_file() or Path(executable).suffix.lower() != ".exe":
                raise ValueError("请指定已安装的 opencode.exe")
            max_output_tokens = output_cap(connection['model_settings']) if catalog_request else body.get("max_output_tokens", self.settings["max_output_tokens"])
            if max_output_tokens is not None and (type(max_output_tokens) is not int
                    or max_output_tokens < 1 or connection['model_output_tokens'] is not None
                    and max_output_tokens > connection['model_output_tokens']):
                raise ValueError('单次输出上限应留空或为正整数，且不能超过填写的模型最大输出额度')
            budgets = {}
            for field, label in (("max_agent_steps", "助手步骤上限"), ("turn_timeout_minutes", "单轮时长上限（分钟）"),
                                 ('max_continuations', '自动续跑次数上限'), ('max_message_chars', '单条需求字符上限'),
                                 ('max_parallel_experiments', '并行实验上限'),
                                 *((field, RUN_LABELS[stage] + "次数上限") for stage, field in RUN_LIMIT_FIELDS.items())):
                value = body.get(field, self.settings[field])
                if value is not None and (type(value) is not int or not 1 <= value <= 2147483647):
                    raise ValueError(label + "应留空或填写正整数")
                budgets[field] = value
            key = body.get("api_key", "")
            if not isinstance(key, str) or len(key) > 512 or any(c in key for c in "\r\n\0"):
                raise ValueError("API Key 格式无效")
            if any(field in body and not isinstance(body[field], bool) for field in ("remember_key", "clear_key")):
                raise ValueError("密钥保存选项无效")
            if (not catalog_request and credential_target(connection) != credential_target(self.settings) and self.key
                    and not key.strip() and not body.get('clear_key')):
                raise ValueError('服务或 API 地址已改变，请填写该服务的 API Key；不会把原有 Key 自动发送给另一家服务')
            name = connection['api_key_env']
            next_keys, next_saved = dict(self.keys), dict(self.saved_keys)
            if self.key:
                next_keys[self.settings['api_key_env']] = self.key
            next_key = key.strip() or next_keys.get(name) or os.environ.get(name, '')
            if body.get('clear_key'):
                next_keys.pop(name, None)
                next_saved.pop(name, None)
                next_key = ''
            elif next_key:
                next_keys[name] = next_key
            if key.strip() or 'remember_key' in body:
                if body.get('remember_key') and next_key:
                    next_saved[name] = next_key
                else:
                    next_saved.pop(name, None)
            saved = self.root / "private/deepseek.dpapi"
            vault = self.root / 'private/model-keys.dpapi'
            # Validate and encrypt before mutating the running configuration.
            vault_bytes = protect(json.dumps(next_saved).encode()) if next_saved else None
            active_bytes = protect(next_saved[name].encode()) if name in next_saved else None
            for runtime in self.runtimes.values():
                runtime.stop()
            for path, raw in ((vault,vault_bytes),(saved,active_bytes)):
                if raw is not None:
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(raw)
                else:
                    path.unlink(missing_ok=True)
            self.key = next_key
            self.keys, self.saved_keys = next_keys, next_saved
            self.settings.update(**connection, executable=executable, max_output_tokens=max_output_tokens, **budgets)
            if not catalog_request:
                catalog[alias] = connection_profile(self.settings)
            self.settings.update(model_config=catalog, active_profile=alias)
            write_json(self.root / "settings.json", self.settings)
            return self.public_settings()

    def check(self):
        if not self.key:
            raise ValueError("请先填写 API Key")
        headers = dict(self.settings['api_headers'])
        request = urllib.request.Request(api_base(self.settings) + '/models', headers=headers)
        if self.settings['api_protocol'] == 'anthropic':
            request.add_unredirected_header('x-api-key', self.key)
            request.add_header('anthropic-version', '2023-06-01')
        else:
            request.add_unredirected_header('Authorization', 'Bearer ' + self.key)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                data = json.load(response)
                models = [item['id'] for item in data.get('data', []) if isinstance(item, dict) and isinstance(item.get('id'), str)]
        except urllib.error.HTTPError as error:
            raise ValueError(self.settings['provider_name'] + ' 模型列表请求失败（HTTP ' + str(error.code) +
                             '），请核对 API 地址、协议、Key 与附加请求头；部分网关不提供模型列表接口') from None
        except (urllib.error.URLError, json.JSONDecodeError) as error:
            raise ValueError('连接检查失败：' + self.redact(str(error))) from None
        return {"ok": True, "models": models, "selected_available": self.settings["model"] in models,
                'provider_name': self.settings['provider_name'], 'inference_tested': False}

    def directory(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("实验标识无效")
        path = self.root / "sessions" / identifier
        if not path.resolve().is_relative_to(self.root / "sessions") or path.is_symlink():
            raise ValueError("实验路径无效")
        return path

    def create(self, title="新实验"):
        with self.lock:
            return self._create(title)

    def _create(self, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 100:
            raise ValueError("实验名称需要 1–100 个字符")
        identifier = uuid.uuid4().hex
        folder = self.directory(identifier)
        ExperimentTools(folder, self.config_loader())
        state = {"id": identifier, "title": title.strip(), "created_at": timestamp(), "updated_at": timestamp(),
                 "status": "ready", "messages": [], "opencode_session": None, "error": None, "usage": {}, "turn": 0}
        write_json(folder / "session.json", state)
        self.write_identity(state)
        self.write_session_index()
        return self.get(identifier)

    def write_identity(self, state):
        folder = self.directory(state['id'])
        requirement = next((m.get('text', '') for m in state.get('messages', []) if m.get('role') == 'user' and m.get('origin') != 'platform'), '')
        text = (f"实验名称：{state['title']}\n实验 ID：{state['id']}\n创建时间：{state['created_at']}\n"
                f"源码目录：{folder / 'source'}\n\nsource：RTL、Testbench、约束和运行脚本\n"
                "evidence：功能仿真历史\nfpga：实现、后仿真和下载记录\n"
                "session.json：会话与实验信息\n\n修改实验名称可在网页完成；内部 ID 保持不变。\n")
        if requirement:
            text += '\n首条需求：\n' + self.redact(requirement) + '\n'
        (folder / '实验说明.txt').write_text(text, encoding='utf-8')

    def write_session_index(self):
        directory = self.root / 'sessions'
        directory.mkdir(exist_ok=True)
        lines = ['# 实验索引', '', '可在网页按名称选择实验，再点击“打开源码目录”。内部 ID 不随重命名改变。', '',
                 '| 实验名称 | 实验 ID | 源码目录 |', '|---|---|---|']
        for path in sorted(directory.glob('*/session.json')):
            item = read_json(path)
            if item is None:
                continue  # An unrelated experiment may be finishing deletion.
            title = item['title'].replace('|', '\\|').replace('\n', ' ')
            lines.append(f"| {title} | {item['id']} | `{path.parent / 'source'}` |")
        (directory / '实验索引.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')

    def rename(self, identifier, title):
        if not isinstance(title, str) or not title.strip() or len(title) > 100 or '\0' in title:
            raise ValueError('实验名称需要 1–100 个字符')
        with self.lock:
            self.require_available(identifier)
            if not (self.directory(identifier) / 'session.json').is_file():
                raise ValueError('实验不存在')
            self._update(identifier, title=' '.join(title.split()))
            self.write_identity(read_json(self.directory(identifier) / 'session.json'))
            self.write_session_index()
        return self.get(identifier)

    def open_source(self, identifier):
        with self.lock:
            self.require_available(identifier)
            folder = self.directory(identifier)
            source = folder / 'source'
            if not (folder / 'session.json').is_file() or not source.is_dir() or not source.resolve().is_relative_to(folder.resolve()):
                raise ValueError('实验源码目录不存在或路径无效')
            if os.name != 'nt':
                raise ValueError('当前平台只支持在 Windows 打开资源管理器，请复制源码路径')
            os.startfile(str(source))
            return {'opened': True, 'path': str(source)}

    def history(self):
        with self.lock:
            return self._history()

    def _history(self):
        results = list(self.maintenance.values())
        for path in (self.root / "sessions").glob("*/session.json"):
            if path.parent.name in self.maintenance:
                continue  # Keep its cached row without recreating deleted source/.
            state = read_json(path)
            if state is None:
                continue
            if state["status"] == "passed" and not ExperimentTools(path.parent).summary()["verified_current"]:
                state["status"] = "needs_test"
            results.append({key: state[key] for key in ("id", "title", "created_at", "updated_at", "status")})
        return sorted(results, key=lambda x: x["updated_at"], reverse=True)

    def get(self, identifier):
        with self.lock:
            return self._get(identifier)

    def _get(self, identifier):
        self.require_available(identifier)
        folder = self.directory(identifier)
        with self.lock:
            state = read_json(folder / "session.json")
        if state is None:
            raise ValueError("实验不存在")
        state['source_path'] = str(folder / 'source')
        state['experiment_path'] = str(folder)
        tools = ExperimentTools(folder)
        state["simulation"] = tools.summary()
        state['flow'] = FpgaFlow(tools).summary()
        if state["status"] == "passed" and not state["simulation"]["verified_current"]:
            state["status"] = "needs_test"
        state["files"] = [{"path": p, "bytes": len(c.encode())} for p, c in tools.files().items()]
        events = []
        event_file = folder / "events.jsonl"
        if event_file.exists():
            for line in event_file.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # A concurrent append may still be finishing.
        state["events"] = events[-100:]
        from .workflow import stage_results
        state['stage_results'] = stage_results(state)
        state['stage_states'] = [row['status'] for row in state['stage_results']]
        return state

    def storage(self, identifier):
        return self.storage_manager.inventory(identifier)

    def cleanup_preview(self, identifier, paths=None, whole=False):
        return self.storage_manager.preview(identifier, paths, whole)

    def cleanup_confirm(self, identifier, token, whole=False):
        return self.storage_manager.confirm(identifier, token, whole)

    def read_tool(self, identifier, name, arguments):
        with self.lock:
            self.require_available(identifier)
            folder = self.storage_manager.folder(identifier)
            tools = ExperimentTools(folder)
            return tools.path(arguments['path']) if name == 'path' else tools.call(name, arguments)

    def remove_opencode_session(self, state, folder):
        sid = state.get('opencode_session')
        if not sid: return
        if not isinstance(sid,str) or not re.fullmatch(r'ses_[A-Za-z0-9]+',sid):
            raise ValueError('OpenCode 会话标识无效，实验文件尚未删除')
        runtime = self.runtime_for(state['id'])
        started = not (runtime.process and runtime.process.poll() is None)
        if started:
            runtime.start(folder, self.settings['executable'], self.key or '', self.settings['model'],
                               self.settings['max_output_tokens'], self.settings['max_agent_steps'], self.settings)
        try:
            try:
                native = runtime.request('GET', '/session/' + sid)
            except RuntimeError as error:
                if str(error).startswith('OpenCode HTTP 404:'): return
                raise
            if Path(native.get('directory', '')).resolve() != (folder/'source').resolve():
                raise ValueError('OpenCode 会话不属于该实验，未执行删除')
            runtime.request('DELETE', '/session/' + sid)
        finally:
            if started or runtime.folder == folder: runtime.stop()

    def _update(self, identifier, **changes):
        with self.lock:
            path = self.directory(identifier) / "session.json"
            state = read_json(path)
            state.update(changes, updated_at=timestamp())
            write_json(path, state)
            return state

    def store_messages(self, identifier, raw):
        messages = []
        usage = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "reported_cost_usd": None,
                 'source':'opencode', 'usage_adapter':self.settings['usage_adapter']}
        costs = []
        for entry in raw:
            info = entry.get("info", {})
            role = info.get("role")
            if role not in ("user", "assistant"):
                continue
            texts, calls = [], []
            for part in entry.get("parts", []):
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "tool":
                    state = part.get("state", {})
                    calls.append({"name": part.get("tool"), "status": state.get("status"),
                                  "input": state.get("input", {}), "output": str(state.get("output", state.get("error", "")))[:16000]})
            parts = entry.get("parts", [])
            text_parts = [part for part in parts if part.get("type") == "text"]
            completed = bool(info.get("time", {}).get("completed"))
            message = {"id": info.get("id"), "role": role, "text": "\n".join(texts), "tools": calls}
            if role == "assistant":
                message.update(finish=info.get("finish"), completed=completed,
                               activity="reasoning" if not completed and any(
                                   part.get("type") == "reasoning" for part in parts) else None)
            elif text_parts and all(part.get("synthetic") for part in text_parts):
                message["origin"] = "platform"
            messages.append(message)
            if role == "assistant":
                tokens = info.get("tokens", {})
                for key in ("input", "output", "reasoning"):
                    usage[key] += tokens.get(key, 0) or 0
                usage["cache_read"] += tokens.get("cache", {}).get("read", 0) or 0
                if isinstance(info.get("cost"), (float, int)):
                    costs.append(info["cost"])
        if any(cost > 0 for cost in costs):
            usage["reported_cost_usd"] = sum(costs)
        clean = json.loads(self.redact(json.dumps(messages, ensure_ascii=False)))
        # Native OpenCode edits participate in the same change history and source
        # hashes as MCP writes. EDA runs still snapshot the actual files on disk.
        seen = self.native_calls.setdefault(identifier, set())
        native = {part.get('id', entry['info']['id'] + ':' + str(i))
                  for entry in raw for i, part in enumerate(entry.get('parts', []))
                  if part.get('type') == 'tool' and part.get('tool') in ('write', 'edit', 'apply_patch')
                  and part.get('state', {}).get('status') == 'completed'}
        tools = ExperimentTools(self.directory(identifier))
        files = tools.files()
        old = self.source_snapshots.get(identifier, files)
        if native - seen:
            import difflib
            import hashlib
            for path in sorted(set(old) | set(files)):
                if old.get(path) != files.get(path):
                    content = files.get(path, '')
                    diff = ''.join(difflib.unified_diff(old.get(path, '').splitlines(True), content.splitlines(True), fromfile=path, tofile=path))
                    tools.event('file_changed', path=path, diff=diff[:24000],
                                sha256=hashlib.sha256(content.encode()).hexdigest(), editor='opencode')
        seen.update(native)
        self.source_snapshots[identifier] = files
        self._update(identifier, messages=clean, usage=usage)

    def send(self, identifier, message, allow_testbench_changes=False):
        if not isinstance(message, str) or not message.strip():
            raise ValueError("请输入需求或消息")
        if self.settings['max_message_chars'] is not None and len(message) > self.settings['max_message_chars']:
            raise ValueError(f"消息超过已设置的 {self.settings['max_message_chars']} 字符上限，可在网页设置中调整")
        if not isinstance(allow_testbench_changes, bool):
            raise ValueError("Testbench 修改选项无效")
        with self.lock:
            if self.closing:
                raise ValueError('服务正在关闭，暂不能开始新实验')
            if self.is_active(identifier):
                raise ValueError("该实验运行中，请停止或等待本轮完成；可以启动其他实验")
            limit = self.settings['max_parallel_experiments']
            if limit is not None and len(self.jobs) >= limit:
                raise ValueError(f'已达到设置的 {limit} 个并行实验上限，请等待完成或调整设置')
            if not self.key:
                raise ValueError("请先在 API 设置中填写 API Key")
            folder = self.directory(identifier)
            state = read_json(folder / "session.json")
            if not state:
                raise ValueError("实验不存在")
            tools = ExperimentTools(folder, self.config_loader())
            self.source_snapshots[identifier] = tools.files()
            turn = state.get("turn", 0) + 1
            tools.begin_turn(str(turn), allow_testbench_changes,
                             run_limits={stage: self.settings[field] for stage, field in RUN_LIMIT_FIELDS.items()})
            tools.event('model_selected', profile=self.settings['active_profile'],
                        connection=connection_profile(self.settings))
            state["messages"].append({"role": "user", "text": message.strip(), "tools": []})
            self._update(identifier, status="running", error=None, messages=state["messages"], turn=turn,
                         continuation=None)
            self.write_identity(read_json(folder / 'session.json'))
            job = ExperimentJob(self.runtime_for(identifier), copy.deepcopy(self.settings), self.key)
            job.worker = threading.Thread(target=self._run, args=(identifier, message.strip()), daemon=True)
            self.jobs[identifier] = job
            try:
                job.worker.start()
            except Exception:
                self.jobs.pop(identifier, None)
                self._update(identifier, status='failed', error='未能启动实验工作线程，请重试。')
                raise
        return self.get(identifier)

    def _run(self, identifier, message):
        job = self.jobs[identifier]
        runtime, settings, cancelled = job.runtime, job.settings, job.cancelled
        folder = self.directory(identifier)
        tools = ExperimentTools(folder)
        finished = False
        max_steps = settings["max_agent_steps"]
        timeout_minutes = settings["turn_timeout_minutes"]
        try:
            if cancelled.is_set():
                self._update(identifier, status='cancelled', error='已停止，可继续发送消息。')
                return
            runtime.start(folder, settings["executable"], job.key, settings["model"],
                          settings["max_output_tokens"], max_steps, settings, cancelled)
            if cancelled.is_set():
                self._update(identifier, status="cancelled", error="已停止，可继续发送消息。")
                return
            state = read_json(folder / "session.json")
            sid = state.get("opencode_session")
            if not sid:
                sid = runtime.request("POST", "/session", {"title": state["title"]})["id"]
                self._update(identifier, opencode_session=sid)
            before = runtime.request("GET", "/session/" + sid + "/message")
            if cancelled.is_set():
                self._update(identifier, status='cancelled', error='已停止，可继续发送消息。')
                return
            previous = {item["info"]["id"] for item in before}
            turn_previous = previous.copy()
            continuations = 0
            runtime.request("POST", "/session/" + sid + "/prompt_async", {
                "agent": "rtl_teacher", "model": {"providerID": settings['provider_id'], "modelID": settings["model"]},
                "parts": [{"type": "text", "text": message}]})
            deadline = time.monotonic() + timeout_minutes * 60 if timeout_minutes is not None else None
            while deadline is None or time.monotonic() < deadline:
                if cancelled.wait(.7):
                    self._update(identifier, status="cancelled", error="已停止，可继续发送消息。")
                    return
                raw = runtime.request("GET", "/session/" + sid + "/message")
                if raw:
                    self.store_messages(identifier, raw)
                assistants = [item for item in raw if item["info"].get("role") == "assistant" and item["info"]["id"] not in previous]
                errors = [item["info"]["error"] for item in assistants if item["info"].get("error")]
                if errors:
                    raise RuntimeError(json.dumps(errors[-1], ensure_ascii=False))
                status = runtime.request("GET", "/session/status").get(sid, {}).get("type", "idle")
                steps = sum(item["info"].get("role") == "assistant" and item["info"]["id"] not in turn_previous for item in raw)
                if max_steps is not None and steps > max_steps:
                    raise RuntimeError(f"本轮达到 {max_steps} 个助手步骤上限，已停止。现有文件和工具结果已保存。")
                if assistants and status == "idle" and assistants[-1]["info"].get("time", {}).get("completed"):
                    last = assistants[-1]
                    if last["info"].get("finish") in ("content-filter", "error", "unknown"):
                        raise RuntimeError("模型未正常结束回复：" + last["info"]["finish"])
                    issue = incomplete_reply(last)
                    if issue:
                        limit = settings['max_continuations']
                        if limit is not None and continuations >= limit:
                            raise RuntimeError(issue + f"；已达到设置的自动续跑 {limit} 次上限。现有文件和工具结果已保存，可继续发送消息。")
                        if max_steps is not None and steps >= max_steps:
                            raise RuntimeError(issue + f"；本轮已达到 {max_steps} 个助手步骤上限，现有结果已保存。")
                        # Continue the same OpenCode conversation without resetting
                        # the wall-clock deadline or any EDA attempt quotas.
                        if cancelled.is_set() or (deadline is not None and time.monotonic() >= deadline):
                            continue
                        continuations += 1
                        previous = {item["info"]["id"] for item in raw}
                        self._update(identifier, continuation={"attempt": continuations, "reason": issue,
                                                              "message_id": last["info"]["id"]})
                        runtime.request("POST", "/session/" + sid + "/prompt_async", {
                            "agent": "rtl_teacher", "model": {"providerID": settings['provider_id'], "modelID": settings["model"]},
                            "parts": [{"type": "text", "text": CONTINUATION_PROMPT, "synthetic": True}]})
                        continue
                    verified = tools.summary()["verified_current"]
                    self._update(identifier, status="passed" if verified else "ready")
                    finished = True
                    return
            self._update(identifier, status="timeout", error=f"本轮达到 {timeout_minutes} 分钟上限，可查看当前文件后继续。")
        except Exception as error:
            self._update(identifier, status="cancelled" if cancelled.is_set() else "failed",
                         error=self.redact(str(error))[:3000])
        finally:
            # A disconnected HTTP poll does not stop the remote agent. Close the
            # owned process tree before another turn can clear its cancellation flag.
            cleaned = True
            if not finished:
                tools.cancel()
            try:
                runtime.stop()
            except Exception as error:
                cleaned = False
                self._update(identifier, status="failed", error="停止工具进程失败，请点击停止重试：" + self.redact(str(error))[:1000])
            if cleaned:
                with self.lock:
                    if self.jobs.get(identifier) is job:
                        self.jobs.pop(identifier)

    def stop(self, identifier):
        with self.lock:
            job = self.jobs.get(identifier)
            if job is None:
                raise ValueError("该实验当前没有运行")
            job.cancelled.set()
            ExperimentTools(self.directory(identifier)).cancel()
            if not job.worker or not job.worker.is_alive():
                job.runtime.stop()
                self.jobs.pop(identifier, None)
                self._update(identifier, status="cancelled", error="已停止，可继续发送消息。")
        return {"ok": True, "message": "停止请求已发送"}

    def board_action(self, identifier, body, observation=False):
        with self.lock:
            if self.closing:
                raise ValueError('服务正在关闭')
            if self.is_active(identifier):
                raise ValueError('请等待或停止该实验后操作板卡')
            folder = self.directory(identifier)
            if not (folder / 'session.json').is_file():
                raise ValueError('实验不存在')
            tools = ExperimentTools(folder)
            tools.begin_turn('human-board-action')
            job = ExperimentJob(self.runtime_for(identifier), copy.deepcopy(self.settings), self.key, kind='hardware')
            job.worker = threading.current_thread()
            self.jobs[identifier] = job
        try:
            flow = FpgaFlow(tools)
            if observation:
                return flow.record_observation(body.get('passed'), body.get('procedure'), body.get('observed'))
            return flow.program(body.get('confirmation'), body.get('board_confirmed'))
        finally:
            with self.lock:
                if self.jobs.get(identifier) is job:
                    self.jobs.pop(identifier)

    def close(self):
        with self.lock:
            self.closing = True
            jobs = list(self.jobs.items())
        for identifier, job in jobs:
            job.cancelled.set()
            ExperimentTools(self.directory(identifier)).cancel()
        for _, job in jobs:
            if job.worker and job.worker is not threading.current_thread() and job.worker.is_alive():
                job.worker.join(timeout=3)
        for runtime in list(self.runtimes.values()):
            runtime.stop()
        for _, job in jobs:
            if job.worker and job.worker is not threading.current_thread() and job.worker.is_alive():
                job.worker.join(timeout=3)
