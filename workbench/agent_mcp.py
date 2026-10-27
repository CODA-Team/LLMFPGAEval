"""Small stdio MCP transport. Board programming is never exposed to the agent."""
import json
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workbench.agent_tools import ExperimentTools, TOOL_SCHEMAS
from workbench.fpga_flow import FLOW_SCHEMAS
from workbench.eda_scripts import SCRIPT_SCHEMAS, compact

OUTPUT_LOCK = threading.Lock()


def emit(value):
    with OUTPUT_LOCK:
        print(json.dumps(value, ensure_ascii=False), flush=True)


@contextmanager
def progress(request, interval=10):
    """Keep MCP's transport alive without imposing a tool execution deadline.

    OpenCode supplies progressToken and resets its request timer on each tick.
    The runner owns optional execution timeouts and the user cancellation flag.
    """
    token = request.get('params', {}).get('_meta', {}).get('progressToken')
    stopped = threading.Event()
    def heartbeat():
        count = 0
        while not stopped.wait(interval):
            count += 1
            emit({'jsonrpc': '2.0', 'method': 'notifications/progress',
                  'params': {'progressToken': token, 'progress': count, 'message': 'EDA tool is still running; logs are being saved on disk.'}})
    worker = threading.Thread(target=heartbeat, daemon=True) if token is not None else None
    if worker:
        worker.start()
    try:
        yield
    finally:
        stopped.set()
        if worker:
            worker.join()

# Legacy fixed adapters remain available to old local integrations only.
AGENT_SCHEMAS = [s for s in TOOL_SCHEMAS + FLOW_SCHEMAS if s['name'] not in ('simulate', 'implement', 'post_route')] + SCRIPT_SCHEMAS


def available_schemas(tools):
    if tools.config.get('simulation_backend') == 'icarus':
        auxiliary = dict(next(s for s in TOOL_SCHEMAS if s['name'] == 'simulate'))
        auxiliary['description'] = 'Explicitly selected auxiliary Icarus functional check of RTL/TB. Uses installed iverilog/vvp; cannot qualify a TD/ModelSim/board flow. Other files are not HDL inputs.'
        return AGENT_SCHEMAS + [auxiliary]
    return AGENT_SCHEMAS


def serve(folder):
    tools = ExperimentTools(folder)
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "rtl-lab", "version": "1.0.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": available_schemas(tools)}
            elif method == "tools/call":
                params = request["params"]
                try:
                    if params['name'] not in {s['name'] for s in available_schemas(tools)}:
                        raise ValueError('工具未开放；使用 templates/write_file/run_tool/read_run')
                    arguments = params.get("arguments", {})
                    if params['name'] == 'read_file':
                        arguments = {'start_line': 1, 'max_lines': 80, **arguments}
                    with progress(request):
                        value = tools.call(params["name"], arguments)
                    result = {"content": [{"type": "text", "text": json.dumps(compact(value), ensure_ascii=False)}]}
                except (ValueError, KeyError, OSError, TypeError) as error:
                    result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
            else:
                emit({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown method"}})
                continue
            emit({"jsonrpc": "2.0", "id": request["id"], "result": result})
        except Exception as error:
            if "id" in request:
                emit({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32602, "message": str(error)}})


if __name__ == "__main__":
    serve(sys.argv[1])
