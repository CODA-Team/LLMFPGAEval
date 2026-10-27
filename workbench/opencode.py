"""Managed OpenCode agent with native file tools and recorded EDA execution."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .lease import TaskLease
from pathlib import Path

from .agent_tools import write_json
from .config import local_path
from .model_connection import MODEL_MAX_OUTPUT_TOKENS, normalize_connection, provider_definition, output_cap

# DeepSeek Chat Completions API maximum, checked 2026-10-25:
# https://api-docs.deepseek.com/api/create-chat-completion/
# max_tokens includes reasoning and visible output. Omitting it does NOT mean
# unlimited: both the API and OpenCode have smaller defaults.

SYSTEM_PROMPT = """你是 RTL LAB 教学助手，像工程师一样亲自编写文件、操作 EDA 工具、阅读日志并迭代；用中文向学生解释证据。
平台提供文件读写、工具执行和结果记录。RTL、Testbench、引脚/时钟约束、TD Tcl 和 ModelSim DO 运行脚本都由你生成和修改。
先用 rtl_list_files/rtl_board_info 检查现有文件和板卡，rtl_templates 读取基础语法范例。模板只读，不会替你生成工程文件或覆盖脚本。
工作目录是当前实验的 source。用原生 read/grep/glob 查找、分段读取文件，edit/write/apply_patch 修改源码，也可用 rtl_write_file 保存。文件放在 rtl/*.v（TD 可综合 Verilog 2001）、tb/*.sv、constraints/*.tcl/.sdc、scripts/*.tcl/.do；已有正确文件不必重写。
rtl_create_project 只记录板卡顶层/端口资源映射，用于展示和上板核对；真正实现使用你写的 constraints/pins.tcl、clock.sdc、scripts/implement.tcl。两者保持一致。
完整软件流程：一次准备好源码、约束和三个运行脚本 → 记录工程 → rtl_run_tool(modelsim,functional,scripts/functional.do) → rtl_run_tool(td,implementation,scripts/implement.tcl) → rtl_run_tool(modelsim,post_route,scripts/post_route.do)。参数使用工具 schema 的 tool/stage/script。
脚本由你决定 read_verilog、约束、optimize_rtl/optimize_gate、place、route、report_timing、bitgen，以及 vlib/vlog/vsim/run 的调用顺序。可写 Tcl 变量、循环、过程和条件，source 仅限本次输入快照中的脚本。
按模板使用 runner 提供的 rtl_files、tb_files、td_models、design_top。TD 输出名为 design.bit/post_route.v/route.sdf/timing.rpt/clock_summary.rpt。需要查询时写脚本并使用 stage=query；不暴露 shell/网络/任意路径/烧录。
收到错误后检查真实日志。原始日志完整保存在当前实验 ../evidence/<id>/ 或 ../fpga/<stage>/<id>/，run_tool 返回实际 directory；用原生 grep 搜索错误、read 按行读取上下文，rtl_read_run 也是可选分页入口。返回片段长度不会限制磁盘日志。命令参数不支持时依据工具帮助和日志修正，不要猜测成功。
把任务拆成可执行的小步：确定接口和最小设计后就保存文件、调用工具，再根据结果迭代。不要在一次回复里反复推演整个工程而不落盘；简短说明后立即执行下一项具体操作。
若运行脚本有错，直接修改 scripts/*.tcl/.do；若约束有错，修改 constraints/*；只有日志或测试指向逻辑缺陷时才修改 RTL。平台不会覆盖你写的脚本，也没有固定端口方向自检。不要为规避脚本错误给常量输出增加无关逻辑。
所有输入文件参与版本校验。改脚本/约束/RTL 后重新功能仿真，再实现和后仿真；提前准备所有脚本能避免多余重跑。
默认仿真器是 ModelSim，不是 TD 内置模拟器；TD 提供工程、实现和 EG4 模型。Icarus 为学生明确选择的辅助模式，不能解锁上板流程。
若 board_info 的 functional_backend=icarus，本轮辅助功能仿真用额外开放的 rtl_simulate（编译 RTL/TB），尊重学生的选择。完整 TD/SDF 流程仍需在网页切回 ModelSim；不要无视 Icarus 设置启动 ModelSim。
Testbench 顶层必须叫 tb，设计实例必须叫 dut，并用 .端口名(信号) 连接，不能依赖端口位置（TD 网表可能重排端口）。后仿真复用同一 Testbench，加载布局布线网表、SDF 和 glbl。
板载主时钟 50MHz，周期 20ns，reset 至少保持 100ns，之后于下降沿释放；有寄存器的输出需留传播延迟后采样（20ns 时钟的简单例子在上升沿后15ns检查），不要在触发沿立即比较。
先确定板卡顶层接口；只使用 board_info 提供的引脚资源，不能为任意宽数据端口编造引脚。不适合直接映射板卡的抽象模块，先完成模块仿真，解释还需板级包装与接口选择，不要自作主张改变需求。
测试应根据需求独立计算预期值，覆盖复位、正常行为、边界值。用 !== 比较，失败用 $fatal(1, 具体信息)。
每项真正完成的检查输出 TEST_PASS <名称>，全部检查结束输出一行 ALL_TESTS_PASSED 再 $finish。
不要仅打印通过信息，不得通过跳过断言来让测试通过。可使用 $dumpfile("wave.vcd") 和 $dumpvars 保存波形。
原生文件工具可读取当前实验的源码和运行结果，只修改 source 内文件；平台负责记录差异、保留各阶段运行结果，并单独标明相关输入是否已改变。实际 EDA 运行用 rtl_run_tool，以便保留输入快照和网页阶段记录。不要修改历史运行证据、平台控制文件或尝试编程板卡。
仿真失败后依据真实日志定位错误，修改相应文件，再调用 rtl_run_tool，直到通过或达到上限。
生成的 Testbench 与 RTL、约束、运行脚本一样可直接修改，编译成功后也不会锁定。若测试本身错误，先根据需求和真实日志解释依据，再立即修改并重跑；不需要学生解锁，也不要仅分析后停止。
修复 Testbench 必须保留需求、独立期望值和有效检查，不能降低标准或删除失败断言来凑通过。每次修改和旧运行快照均保留，只重跑需要验证的阶段。仅改 TB 不需要重新实现；02/07 各自保留其测试结果，验证修改后的测试才重跑相应阶段。
首次功能仿真前先用 rtl_create_project 记录顶层和引脚映射，避免稍后登记工程使已有结果失效。当前工具不支持 include、文件 IO、$system；使用简单自包含 RTL。
功能仿真、TD 实现和后仿真的次数、助手步骤与单轮时长均默认不额外限制；用户可在网页分别设置上限。board_info.run_budget 返回本轮实际次数上限、已用及剩余额度，null 表示不限制；失败的实际运行也计数，自动续跑不重置额度。工具会返回真实报告。达到实际配置的上限或需求缺失时如实汇报，不虚构结果。
后仿真：独立模块检查的原始 RTL 编到 rtl_reference；厂商模型（不加 -sv）、物理网表和 TB 编到 work。vsim 使用 -L work -L rtl_reference -sdfmax /tb/dut=route.sdf work.tb work.glbl；必须确认真实 SDF 加载，不隐藏模型警告。区分 RTL 子模块自检和网表 DUT 检查覆盖。
程序下载成功与板上功能正确是两个结果；你不能调用烧录工具，也不能声称观察到了真实板卡。通过后提示学生核对板卡、在网页输入 PROGRAM SRAM，并按测试步骤观察实物后记录。
修改代码后，旧仿真结果仍描述它自己的输入快照，不能当作已验证新代码；按 inputs_current/changed_inputs 选择复测阶段。解释结论时引用实际测试名和失败信号值。
测试通过只表示生成的自检用例通过，不代表形式证明、TD 时序或板上验证通过。
若学生只问解释，可直接基于已有证据回答，不必改文件。不要重复无必要地改写已完成的源文件。
"""


def agent_config(folder, model="deepseek-flash", max_output_tokens=None, max_agent_steps=None, connection=None):
    connection = normalize_connection(connection, {'model':model})
    provider = connection['provider_id']
    root = Path(folder).resolve().as_posix()
    source = Path(folder).resolve() / 'source'
    # OpenCode 1.x matches edit permissions against path.relative(worktree,...).
    # Without Git its worktree is '/', which is the current drive root on Windows.
    worktree = next((p for p in source.parents if (p / '.git').exists()), Path(source.anchor))
    relative = source.relative_to(worktree)
    edit_patterns = {pattern: 'allow' for pattern in
                     (source.as_posix() + '/*', str(source) + os.sep + '*',
                      relative.as_posix() + '/*', str(relative) + os.sep + '*')}
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": provider + '/' + model,
        "small_model": provider + '/' + model,
        "enabled_providers": [provider],
        "provider": {provider: provider_definition(connection, max_output_tokens)},
        "default_agent": "rtl_teacher",
        "permission": {"*": "deny", "rtl_*": "allow", "read": "allow", "grep": "allow",
                       "glob": "allow", "todowrite": "allow", "doom_loop": "allow",
                       "edit": {"*": "deny", **edit_patterns},
                       "external_directory": {"*": "deny", root + "/*": "allow"}},
        "agent": {"rtl_teacher": {"description": "RTL teaching and self-test repair", "mode": "primary",
                                  "prompt": SYSTEM_PROMPT,
                                  **({"temperature":connection['model_settings']['temperature']} if 'temperature' in connection['model_settings'] else {}),
                                  **({"top_p":connection['model_settings'].get('top_p',connection['model_settings'].get('topP'))} if any(k in connection['model_settings'] for k in ('top_p','topP')) else {}),
                                  **({"steps": max_agent_steps} if max_agent_steps is not None else {})},
                  "title": {"disable": True}},
        "mcp": {"rtl": {"type": "local", "enabled": True, "timeout": 240000,
                        "command": [sys.executable, "-u", str(Path(__file__).with_name("agent_mcp.py")), str(folder)]}},
        "autoupdate": False, "share": "disabled", "snapshot": False,
        "lsp": False, "formatter": False,
    }


class OpenCodeRuntime:
    def __init__(self, root, log_directory=None):
        self.root = Path(root)
        self.log_directory = Path(log_directory) if log_directory else self.root
        self.lifecycle_lock = threading.RLock()
        self.process = None
        self.output = None
        self.folder = None
        self.tool_config_digest = None
        self.model_config = None
        self.url = None
        self.password = secrets.token_urlsafe(32)
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def start(self, folder, executable, key, model, max_output_tokens=None, max_agent_steps=None, connection=None, cancelled=None):
        with self.lifecycle_lock:
            self.root.mkdir(parents=True, exist_ok=True)
            startup = TaskLease(self.root / '.startup.lock')
            try:
                # OpenCode's initial SQLite migrations cannot race another server.
                # Release immediately after startup; agent requests remain parallel.
                while not startup.acquire():
                    if cancelled is not None and cancelled.wait(.1):
                        raise RuntimeError('已取消 OpenCode 启动')
                    if cancelled is None:
                        time.sleep(.1)
                return self._start(folder, executable, key, model, max_output_tokens, max_agent_steps, connection, cancelled)
            finally:
                startup.release()

    def _start(self, folder, executable, key, model, max_output_tokens=None, max_agent_steps=None, connection=None, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise RuntimeError('已取消 OpenCode 启动')
        folder = Path(folder).resolve()
        tool_digest = hashlib.sha256((folder / 'tool-config.json').read_bytes()).hexdigest()
        selected = normalize_connection(connection, {'model': model})
        model_config = (model, max_output_tokens, max_agent_steps, json.dumps(selected, sort_keys=True))
        if (self.process and self.process.poll() is None and self.folder == folder
                and self.tool_config_digest == tool_digest and self.model_config == model_config):
            return
        self.stop()
        executable = str(local_path(executable))
        if not Path(executable).is_file():
            raise ValueError("未找到 OpenCode，请在 API 设置中指定 opencode.exe")
        self.root.mkdir(parents=True, exist_ok=True)
        configuration = folder / "opencode.json"
        write_json(configuration, agent_config(folder, model, max_output_tokens, max_agent_steps, selected))
        env = os.environ.copy()
        for kind in ("CONFIG", "DATA", "STATE", "CACHE"):
            directory = self.root / kind.lower()
            directory.mkdir(exist_ok=True)
            env["XDG_" + kind + "_HOME"] = str(directory)
        env["OPENCODE_CONFIG"] = str(configuration)
        env["OPENCODE_SERVER_PASSWORD"] = self.password
        env["OPENCODE_SERVER_USERNAME"] = "opencode"
        env.pop('DEEPSEEK_API_KEY', None)
        env['RTL_LAB_API_KEY'] = key
        env[selected['api_key_env']] = key
        # OpenCode also caps requests at 32,000 independently of model.limit.
        # Set its supported override, including when the user leaves the cap blank.
        output = max_output_tokens or output_cap(selected['model_settings']) or selected['model_output_tokens']
        if output is not None:
            env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = str(output)
        else:
            env.pop('OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX', None)
        env["NO_PROXY"] = "127.0.0.1,localhost" + ("," + env["NO_PROXY"] if env.get("NO_PROXY") else "")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = "http://127.0.0.1:" + str(port)
        self.log_directory.mkdir(parents=True, exist_ok=True)
        self.output = (self.log_directory / "server.log").open("ab")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        self.process = subprocess.Popen([executable, "serve", "--hostname", "127.0.0.1", "--port", str(port)],
                                        cwd=folder / "source", env=env, stdin=subprocess.DEVNULL,
                                        stdout=self.output, stderr=subprocess.STDOUT, **options)
        self.folder = folder
        self.tool_config_digest = tool_digest
        self.model_config = model_config
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            if cancelled is not None and cancelled.is_set():
                self.stop()
                raise RuntimeError('已取消 OpenCode 启动')
            if self.process.poll() is not None:
                raise RuntimeError("OpenCode 启动失败，请检查本地 runtime/server.log")
            try:
                if self.request("GET", "/global/health", timeout=2).get("healthy"):
                    return
            except (OSError, ValueError, RuntimeError):
                pass
            time.sleep(.25)
        self.stop()
        raise RuntimeError("OpenCode 启动超时")

    def request(self, method, path, body=None, timeout=20):
        if not self.url:
            raise RuntimeError("OpenCode 尚未启动")
        raw = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        auth = base64.b64encode(("opencode:" + self.password).encode()).decode()
        req = urllib.request.Request(self.url + path, data=raw, method=method,
                                     headers={"Authorization": "Basic " + auth, "Content-Type": "application/json"})
        try:
            with self.http.open(req, timeout=timeout) as response:
                content = response.read()
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            content = error.read().decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError("OpenCode HTTP " + str(error.code) + ": " + content) from None

    def stop(self):
        with self.lifecycle_lock:
            self._stop()

    def _stop(self):
        if self.process and self.process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                import signal
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.output:
            self.output.close()
        self.output = self.process = self.folder = self.url = None
