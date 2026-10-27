# RTL Lab — LLM 驱动的 FPGA 教学平台

在本地网页描述电路需求，由 OpenCode 连接用户配置的模型，编写和修改 Verilog RTL、Testbench、约束与运行脚本，再调用本机 EDA 工具，根据真实日志迭代验证。

```text
需求 → RTL / Testbench / 约束 / 脚本
     → ModelSim 功能仿真 → TD 综合、布局布线、比特流
     → ModelSim SDF 后仿真 → 人工确认 SRAM 下载 → 板上观察记录
```

当前源码发布版：**v0.1.0**。基于原项目 LLMFPGAEval 扩展；保留 [BSD 3-Clause 许可证](LICENSE) 和原版权声明。

## 当前支持范围

| 项目 | 支持情况 |
|---|---|
| 平台运行环境 | Windows；网页仅监听本机 `127.0.0.1` |
| FPGA 工具 | Windows Anlogic TD；已验证 TD 4.6.7 |
| 仿真器 | ModelSim；已验证 SE 2020.4 x64 的功能仿真与 SDF 后仿真 |
| 默认板卡 | DMZ-EG4S20，器件 EG4S20BG256，50 MHz 时钟 |
| LLM 执行层 | OpenCode 1.19.4；模型与 API 地址由用户提供 |
| 模型服务 | DeepSeek、兼容 OpenAI 的服务、中转服务，以及已接入的 Responses / Anthropic SDK |
| 并行实验 | 不同实验独立执行和停止；同一个实验内部顺序执行 |
| ASIC 对比 | 可选：通过 SSH 调用 Linux 上的 ORFS，当前验证平台为 Nangate45 |
| 其他主机 / 板卡 | Linux / WSL 本地主机、其他 FPGA 板卡尚未完成本发布版适配验证 |

工具版本不同可能需要调整命令或厂商仿真库；填写新路径不等于已验证新版本兼容性。

## 1. 首次启动网页

1. 安装 **64 位 Python 3.13**（当前验证版本），安装时勾选加入 PATH。代码最低需要 Python 3.10；其他版本未做完整验证。
2. 下载或克隆本仓库，解压到较短的英文目录，例如 `D:\projects\rtl-lab`。本次验证中，网页可在中文目录启动，但 ModelSim SE 2020.4 在该目录下无法创建编译数据库；实际 EDA 实验请使用英文路径。
3. 双击根目录的 **`start.bat`**，或在该目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1
```

4. 保持启动窗口运行，在浏览器打开 **http://127.0.0.1:8765/chat**。

平台后端只依赖 Python 标准库，网页静态文件已经在仓库中，无需 npm 构建前端。尚未安装 TD、ModelSim 或填写 API Key，也可以启动网页并配置环境。

也可直接启动：

```powershell
python app.py serve --port 8765
```

端口被占用时：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1 -Port 8766
```

此时访问 `http://127.0.0.1:8766/chat`。需要指定 Python 时，给 `start.ps1` 传入 `-Python "C:\path\to\python.exe"`。在启动窗口按 `Ctrl+C` 可关闭服务；重启后实验文件和对话历史仍会保留，中断任务需发送消息继续。

## 2. 安装 OpenCode

若已经安装，可以在网页「API 与模型设置 → 本地 OpenCode 路径」填写 **`opencode.exe` 文件路径**。

也可先安装 Node.js/npm，再运行仓库提供的安装脚本：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-opencode.ps1
```

脚本从 npm 安装已验证的 `opencode-ai@1.19.4` 到仓库内 `.tools/opencode/`，不修改全局 OpenCode 安装。平台默认查找：

```text
.tools/opencode/node_modules/opencode-ai/bin/opencode.exe
```

在平台启动后才安装时，重启平台，或直接在网页填入上述程序的完整路径。安装 OpenCode 与 SDK 可能需要网络访问；Node.js/npm 用于安装，不是 Python 网页服务的依赖。

官方入口：[OpenCode](https://opencode.ai/docs/)、[Node.js](https://nodejs.org/)、[Python](https://www.python.org/downloads/windows/)。

## 3. 配置模型 API

打开「API 与模型设置」，填写 **“显示名称 → 模型配置对象”** 的 JSON。例如：

```json
{
  "DeepSeek-Flash": {
    "provider_id": "deepseek-official",
    "model_id": "deepseek-flash",
    "base_url": "https://api.deepseek.com",
    "api_key_env": "DEEPSEEK_API_KEY",
    "npm": "@ai-sdk/openai-compatible",
    "usage_adapter": "deepseek-v1",
    "settings": {}
  }
}
```

在 JSON 下方选择模型，并在 **API Key 输入框**填写自己的密钥。模型 ID 和可用权限以服务商实际提供的为准。

- 官方 API 和中转服务都可以使用；按服务商说明修改 `base_url`、`model_id` 和 SDK。服务需要支持工具调用。
- 多模型示例见 [docs/examples/models.json](docs/examples/models.json)。这是平台接收的格式，平台会转换成 OpenCode 配置。
- `settings` 默认留空，不主动指定推理强度或输出上限。需要时自行填写模型支持的参数，例如 `reasoningEffort`、`max_tokens`；不同模型接受的取值可能不同。
- `api_key_env` 填环境变量名称，不填密钥。也可在启动平台前设置该环境变量，供后端读取。
- 可选择在当前 Windows 账户下加密保存密钥。加密凭据不随源码包分发，换电脑或账户应重新填写。
- 「检查连接」检查模型列表可访问性；模型实际生成、工具调用和用量以一次真实实验为准。

## 4. 配置 TD、ModelSim 与许可证

TD 和 ModelSim **需要用户自行安装并配置可用许可证**，仓库不包含工具、安装包、许可证或设备驱动。

打开「TD 与仿真器设置」：

| 字段 | 应填写什么 | 示例 |
|---|---|---|
| TD 安装目录 | 包含 `bin/td_commands_prompt.exe`、`sim/eg` 的根目录 | `E:/EDA/TD_4.6.7` |
| ModelSim 安装目录 | 包含 `win64/vsim.exe`、`vlog.exe`、`vlib.exe` 的根目录 | `E:/EDA/modelsim2020.4` |
| ModelSim 许可证 | 有效许可证文件路径，或许可证服务器 | `E:/licenses/modelsim.lic` / `27000@license-server` |
| 仿真后端 | 完整 FPGA 流程选择 ModelSim | `modelsim` |
| Icarus / vvp | 可选辅助 RTL 仿真器的可执行文件 | `E:/EDA/iverilog/bin/iverilog.exe` |

TD 4.6.7 的许可证通常放在其安装目录的 `license/Anlogic.lic`。ModelSim 许可证留空时，会尝试安装目录下的 `licenses/LICENSE.TXT`，也可以使用工具支持的许可证环境变量。`Unable to checkout a license` 表示许可证未能成功签出，需要检查自己的授权配置。

下载与支持：[安路 TD](https://www.anlogic.com/support/tools-downloads)、[Siemens](https://www.siemens.com/en-us/support/documentation-downloads/)。旧版工具所需的 VC++ 等运行库、USB/JTAG 驱动按厂商安装说明配置。

### 路径规则

- 平台文件按仓库位置定位，不要求放在任何指定盘符。
- 工具可以安装在仓库外，网页填写绝对路径即可。
- 配置中的相对路径以**仓库根目录**为基准，而不是命令行当前目录或某次仿真的工作目录。
- `.tools/` 仅是可选工具存放位置，不会被打包。已有旧布局安装也可通过网页指定。
- 网页保存工具设置到 `workbench.local.json`。公开示例见 [workbench.example.json](workbench.example.json)，其中路径需要按自己的电脑修改；已有配置不要直接覆盖。

## 5. 开始第一个实验

点击「新建实验」并命名，例如“计数器”。在主对话中输入：

> 为 DMZ-EG4S20 设计一个模 10 计数器，使用 50 MHz 时钟和低有效复位。编写自检 Testbench，验证复位、计数与回绕。先完成功能仿真并报告实际结果。

点击「发送」执行当前需求；点击「启动全流程」会要求模型继续完成功能仿真、TD 实现和 SDF 后仿真。

右侧各阶段可以展开查看日志、测试结果及下载文件。模型负责读取资料、修改 RTL/TB/约束/脚本和根据工具日志修复；平台负责实际执行、记录输入快照及展示结果。

| 阶段 | 结果含义 |
|---|---|
| 01 工程与约束 | 顶层、板卡资源、端口映射和脚本准备 |
| 02 RTL 功能仿真 | RTL 在所运行的 Testbench 下通过或失败 |
| 03–06 TD 实现 | 综合、布局、布线、时序、网表及比特流 |
| 07 SDF 后仿真 | 带实际布局布线延迟的网表仿真 |
| 08 SRAM 下载 | 人工核对板卡与连接后，明确确认本次下载 |
| 09 板上观察 | 人工操作硬件并记录观察结论 |

修改文件后，历史阶段结果和下载入口保留，并标注与当前输入是否一致。只重跑某阶段会更新该阶段展示；旧运行文件仍保留。Testbench 改动通常可以复用 TD 实现，但新的测试内容需重跑对应仿真。

软件仿真通过不等同于硬件验证完成。下载前核对实际板卡型号、供电、电平、引脚和 JTAG。当前网页下载流程写入 FPGA SRAM，掉电丢失；共享下载器上的操作会串行执行。

### 多实验并行与运行上限

A 实验运行时，可以切换或新建 B 并发送需求。每个实验独立运行、保存结果和停止；同一实验的修改与执行保持顺序。

单轮时间、Agent 步数、各阶段运行次数和并行实验数默认不额外限制，可在「API 与模型设置 → 运行与并行上限」填写上限。日志和波形的磁盘上限在「TD 与仿真器设置」中调整，留空可取消；源码默认值为每类 1024 MiB。模型服务自身的请求、上下文、输出和账户额度仍然适用。

## 6. 可选：远程 Linux ORFS

**只做 FPGA 实验可跳过本节。** 默认没有配置任何服务器，也不会自动连接开发者的服务器。

「实现与对比」入口目前提供内置反应计时器和四位计算器的 FPGA/ASIC 对比。聊天中任意新生成的实验尚未自动接入该 ASIC 入口。

1. 在 Linux 服务器安装并验证 ORFS、所选平台库和 `python3`。当前核对过指标口径的 ASIC 平台为 `nangate45`。
2. 在本机先通过系统 OpenSSH 验证登录，例如 `ssh eda-server`。
3. 打开「实现与对比 → 环境配置」，填写自己的服务器和目录：

```json
{
  "ssh_host": "eda-server",
  "ssh_port": 22,
  "ssh_identity": "",
  "orfs_root": "/home/student/tools/OpenROAD-flow-scripts",
  "remote_work_root": "/home/student/rtl-lab-runs",
  "remote_library_path": "",
  "platform": "nangate45",
  "threads": 4
}
```

`ssh_host` 可用 `user@host` 或 SSH 配置别名。`ssh_identity` 可留空以复用 SSH 配置和 ssh-agent；需要时填写自己的私钥路径。平台不复制私钥。

当前任务使用 `BatchMode=yes`，需要可用的非交互式密钥/agent 认证，**尚无 SSH 密码输入界面**。首次连接需先在终端确认主机指纹；加密私钥需先由 ssh-agent 解锁。远程工作目录必须是专用绝对路径，当前不支持含空格的 Linux 目录。

该对比入口还使用 Icarus 对同一份 RTL 执行共有功能测试。它与聊天实验的 ModelSim/SDF 流程分开记录。FPGA LUT/FF、ASIC 单元面积、时序和功耗分别显示，不能把不同单位或不同活动条件的指标直接当成等价测量。ASIC 向量无关功耗估计与板上实测功耗也会区分。

## 7. 数据、备份与清理

```text
workbench.local.json                 本机工具与远程服务器设置
work/teaching/settings.json          模型连接和运行设置
work/teaching/private/               当前账户加密凭据
work/teaching/runtime/               OpenCode 会话数据库与运行数据
work/teaching/sessions/<id>/
  实验说明.txt                      实验名、需求与源码位置
  session.json                      对话及实验状态
  source/                           RTL、TB、约束和运行脚本
  evidence/                         功能仿真输入快照、日志和波形
  fpga/                             实现、后仿真、下载及观察记录
work/runs/                          内置 FPGA/ASIC 对比任务
```

右侧「打开源码目录」或「复制路径」可定位当前实验，不必通过 UUID 猜测设计名称。重命名不会改变内部目录 ID。

「清理文件」可选择删除波形、日志等文件；左侧实验的「删除」会要求确认，并删除整个实验及对应 OpenCode 会话。只禁止清理当前正在运行的实验，其他实验可继续执行。删除不进入回收站，重要资料请先备份。

要保留续聊能力，应在停止运行并退出平台后备份整个 `work/teaching/`，包含实验和 OpenCode 运行数据。跨电脑迁移时重新配置工具路径与凭据；Windows 加密密钥不能当成可跨账户使用的普通配置文件。仅分享设计时，优先复制相应 `source/` 和需要的报告，检查内容后再公开。

波形可从阶段结果下载，再用适当的波形查看器打开。当前网页提供结果/日志与下载入口，不是完整交互式波形编辑器。

## 8. 开发与源码发布

```text
app.py                    平台入口
workbench/                后端、工具适配、网页和基础模板
anlogic/                  TD 命令行兼容包装
tools/                    报告解析与兼容 CLI
exp4_reaction_timer/       内置反应计时器示例
exp_calculator/           内置计算器示例
tests/                    后端、UI 与工具集成测试
scripts/                  安装与打包辅助脚本
docs/examples/            无密钥模型配置示例
```

不需要真实 EDA 工具的基础检查：

```powershell
python -m unittest tests.test_portability tests.test_release_package tests.test_model_connection tests.test_model_catalog tools.test_parser
node --test tests/ui*.test.cjs
```

第二条需要 Node.js。`tests/test_modelsim.py`、`test_fpga_flow.py`、`test_eda_scripts.py` 等集成测试需要本机已配置的工具和许可证，不能把工具缺失当成平台已通过完整流程。

生成新的源码包：

```powershell
python scripts/build_release.py --version 0.1.0 --output dist
```

输出干净源码目录、ZIP 和 SHA-256 校验文件。脚本按明确的源码目录/文件清单收集内容，排除工具、实验、凭据、安装包和内部开发记录；已有同名输出会拒绝覆盖。详见 [发布说明](docs/RELEASE.md)。

上传 GitHub 时，以干净源码目录为仓库根目录，保留 `.gitignore` 和 `LICENSE`。检查 `git status` 和暂存文件后再提交；API Key、许可证、`work/`、`.tools/` 和 `workbench.local.json` 不应提交。源码 ZIP 是平台代码发行包，使用者仍需安装和配置外部 EDA 工具。

## 常见问题

- **网页打不开**：确认启动窗口仍在运行、端口与地址一致；本平台不是托管网站，关机后需再次启动。
- **找不到 OpenCode / TD / ModelSim**：检查网页中的本机路径；工具目录与可执行文件字段不同，按第 2、4 节填写。
- **ModelSim 报 `sqlite3_open` / `unable to open database file`，路径中出现问号**：确认项目父目录不含中文等非 ASCII 字符，将整个项目放到较短的英文路径后重新运行；只改网页里的工具路径不会改变实验工作目录。
- **只看到功能仿真通过**：查看后续阶段的真实运行状态；完整软件流程还需要 TD 实现与 SDF 后仿真。
- **后仿真警告很多或波形很大**：展开实际日志；只记录需要的层级/信号，按需调整磁盘上限，不能以隐藏断言或时序错误代替修复。
- **更换板卡或 TD 版本**：先核对器件库、厂商仿真模型、引脚和命令兼容性，再用小设计验证；当前板卡资源不是通用引脚配置。
- **多个实验都变慢**：并行任务共享 CPU、内存、许可证和模型服务额度；可设置并行上限，单独停止不需要的实验。
