import json
import mimetypes
import re
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import PROJECTS, load_config, save_config
from .runner import Runner
from .agent_tools import ExperimentTools
from .teaching import TeachingManager, TOOL_SETTINGS

STATIC = Path(__file__).with_name("static")


def make_server(runner, port=8765):
    teaching = TeachingManager(runner.work / "teaching", load_config)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def respond(self, status, value):
            raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(raw)

        def local_host(self):
            return self.headers.get("Host") in (
                "127.0.0.1:" + str(self.server.server_port),
                "localhost:" + str(self.server.server_port))

        def do_GET(self):
            if not self.local_host():
                return self.respond(403, {"error": "仅支持本机访问"})
            url = urllib.parse.urlsplit(self.path)
            query = urllib.parse.parse_qs(url.query)
            try:
                if url.path == "/api/teaching":
                    return self.respond(200, {"token": self.server.token, "settings": teaching.public_settings(),
                                             "sessions": teaching.history(), "active_id": teaching.active_id,
                                             "active_ids": teaching.active_ids, "maintenance_ids": teaching.maintenance_ids})
                if url.path == "/api/teaching/session":
                    return self.respond(200, teaching.get(query["id"][0]))
                if url.path == '/api/teaching/storage':
                    return self.respond(200, teaching.storage(query['id'][0]))
                if url.path == "/api/teaching/file":
                    return self.respond(200, teaching.read_tool(query["id"][0], "read_file", {"path": query["path"][0]}))
                if url.path == '/api/teaching/read-run':
                    return self.respond(200, teaching.read_tool(query['id'][0], 'read_run', {
                        'stage': query['stage'][0], 'run_id': query['run'][0], 'file': query['file'][0],
                        'start_line': int(query.get('start_line', ['1'])[0]), 'max_lines': int(query.get('max_lines', ['80'])[0])}))
                if url.path in ("/api/teaching/evidence", "/api/teaching/flow-file"):
                    folder = teaching.directory(query["id"][0])
                    run, name = query["run"][0], query["file"][0]
                    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}_[0-9a-f]{8}", run):
                        raise ValueError("仿真记录无效")
                    allowed = ("result.json", "compile.log", "simulation.log", "wave.vcd", "commands.jsonl", "driver.tcl")
                    if url.path == '/api/teaching/flow-file':
                        stage = query['stage'][0]
                        if stage not in ('implementation', 'post_route', 'programming', 'query'):
                            raise ValueError('流程阶段无效')
                        allowed += ('project.json', 'flow.tcl', 'clock.sdc', 'td.log', 'timing.rpt', 'clock_summary.rpt', 'design.bit', 'post_route.v', 'route.sdf', 'program.log')
                    if name.startswith('source/'):
                        teaching.read_tool(query['id'][0], 'path', {'path':name[len('source/'):]})
                    elif name not in allowed:
                        raise ValueError("证据文件无效")
                    path = folder / "evidence" / run / name if url.path.endswith('/evidence') else folder / 'fpga' / stage / run / name
                    if not path.resolve().is_relative_to(folder) or not path.is_file():
                        return self.respond(404, {"error": "证据文件不存在"})
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Content-Disposition", "attachment; filename=" + Path(name).name)
                    self.end_headers()
                    with path.open("rb") as source:
                        while chunk := source.read(65536):
                            self.wfile.write(chunk)
                    return
                if url.path == "/api/state":
                    return self.respond(200, {"token": self.server.token, "config": load_config(),
                                             "projects": list(PROJECTS.values()), "jobs": runner.history(),
                                             "busy": runner.busy})
                if url.path == "/api/artifacts":
                    folder = runner.directory(query["job"][0])
                    runner.get(query["job"][0])
                    files = [{"path": p.relative_to(folder).as_posix(), "size": p.stat().st_size}
                             for p in sorted(folder.rglob("*")) if p.is_file() and p.suffix in
                             (".log", ".json", ".rpt", ".bit", ".gds", ".v", ".sdc", ".tcl")]
                    return self.respond(200, files)
                if url.path == "/api/file":
                    folder = runner.directory(query["job"][0]).resolve()
                    path = (folder / query["path"][0]).resolve()
                    if not path.is_relative_to(folder) or not path.is_file():
                        return self.respond(404, {"error": "文件不存在"})
                    content_type = "text/plain; charset=utf-8" if path.suffix in (".log", ".json", ".rpt", ".v", ".sdc", ".tcl") else "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(path.stat().st_size))
                    self.send_header("X-Content-Type-Options", "nosniff")
                    if content_type == "application/octet-stream":
                        self.send_header("Content-Disposition", "attachment; filename*=UTF-8''" + urllib.parse.quote(path.name))
                    self.end_headers()
                    with path.open("rb") as source:
                        while chunk := source.read(65536):
                            self.wfile.write(chunk)
                    return
                routes = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css",
                          "/chat": "chat.html", "/chat.js": "chat.js", "/chat.css": "chat.css", "/eda-view.js": "eda-view.js",
                          "/chat-conversation.js": "chat-conversation.js", "/workspace-layout.js": "workspace-layout.js",
                          "/workspace-layout.css": "workspace-layout.css"}
                if url.path not in routes:
                    return self.respond(404, {"error": "未找到"})
                path = STATIC / routes[url.path]
                raw = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", (mimetypes.guess_type(path)[0] or "text/plain") + "; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)
            except (ValueError, KeyError, FileNotFoundError) as exc:
                self.respond(400, {"error": str(exc)})

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed_origins = ("http://127.0.0.1:" + str(self.server.server_port), "http://localhost:" + str(self.server.server_port))
            authorized = (self.local_host() and self.headers.get("X-Workbench-Token") == self.server.token
                          and (origin is None or origin in allowed_origins))
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1:
                    raise ValueError("请求大小无效")
                # Authenticate before accepting large payloads. Small rejected
                # bodies are drained so Windows can deliver the 403 response.
                if not authorized and length > 65536:
                    self.close_connection = True
                    return self.respond(403, {"error": "本地会话校验失败，请刷新工作台"})
                self.connection.settimeout(10)
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("请求内容不完整")
            except (ValueError, OSError) as exc:
                return self.respond(400, {"error": str(exc)})
            if not authorized:
                return self.respond(403, {"error": "本地会话校验失败，请刷新工作台"})
            try:
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError("请求必须为 JSON 对象")
                if self.path == "/api/teaching/config":
                    return self.respond(200, teaching.configure(body))
                if self.path == '/api/teaching/connection-import':
                    from .model_connection import parse_connection_json
                    return self.respond(200, parse_connection_json(body.get('config')))
                if self.path == '/api/teaching/rename':
                    return self.respond(200, teaching.rename(body.get('id'), body.get('title')))
                if self.path in ('/api/teaching/cleanup-preview', '/api/teaching/delete-preview'):
                    return self.respond(200, teaching.cleanup_preview(body.get('id'), body.get('paths'),
                                                                      whole=self.path.endswith('/delete-preview')))
                if self.path in ('/api/teaching/cleanup', '/api/teaching/delete'):
                    return self.respond(200, teaching.cleanup_confirm(body.get('id'), body.get('token'),
                                                                      whole=self.path.endswith('/delete')))
                if self.path == '/api/teaching/open-source':
                    return self.respond(200, teaching.open_source(body.get('id')))
                if self.path == "/api/teaching/check":
                    return self.respond(200, teaching.check())
                if self.path == "/api/teaching/create":
                    return self.respond(201, teaching.create(body.get("title", "新实验")))
                if self.path == "/api/teaching/send":
                    return self.respond(202, teaching.send(body.get("id"), body.get("message"), body.get("allow_testbench_changes", False)))
                if self.path == "/api/teaching/stop":
                    return self.respond(200, teaching.stop(body.get("id")))
                if self.path == '/api/teaching/program':
                    return self.respond(200, teaching.board_action(body.get('id'), body))
                if self.path == '/api/teaching/observation':
                    return self.respond(200, teaching.board_action(body.get('id'), body, observation=True))
                if self.path == '/api/teaching/tools':
                    with teaching.lock, runner.lock:
                        if teaching.busy or runner.busy:
                            raise ValueError('请等待当前任务完成再修改工具路径')
                        if set(body) - set(TOOL_SETTINGS):
                            raise ValueError('工具配置字段无效')
                        save_config(body)
                        return self.respond(200, teaching.public_settings())
                if self.path == "/api/jobs":
                    job = runner.create(body.get("project"), body.get("targets"))
                    threading.Thread(target=runner.run, args=(job["id"],), daemon=True).start()
                    return self.respond(202, job)
                if self.path == "/api/config":
                    with teaching.lock, runner.lock:
                        if runner.busy or teaching.busy:
                            raise ValueError("请等待任务完成再修改配置")
                        return self.respond(200, save_config(body))
                if self.path == "/api/check":
                    return self.respond(200, runner.check())
                if self.path == "/api/program":
                    return self.respond(200, runner.program(body.get("job"), body.get("confirmation")))
                if self.path == "/api/power":
                    return self.respond(200, runner.record_power(body.get("job"), body))
                return self.respond(404, {"error": "未知操作"})
            except (ValueError, TypeError, KeyError, OSError) as exc:
                self.respond(400, {"error": teaching.redact(str(exc))})
            except Exception as exc:
                self.respond(500, {"error": teaching.redact(str(exc))})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.daemon_threads = True
    server.teaching = teaching
    return server


def serve(port=8765):
    server = make_server(Runner(), port)
    print("RTL 工作台: http://127.0.0.1:" + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.teaching.close()
        server.server_close()
