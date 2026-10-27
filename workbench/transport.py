import base64
import json
import subprocess
from pathlib import Path
from .config import require_remote_config, local_path


def remote_execute(config, payload):
    require_remote_config(config)
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
               "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
               "-p", str(config["ssh_port"])]
    if config["ssh_identity"]:
        command += ["-i", str(local_path(config["ssh_identity"]))]
    command += [config["ssh_host"], "python3 -"]
    source = Path(__file__).with_name("remote_worker.py").read_text(encoding="utf-8")
    data = dict(payload, config=config)
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    source += "\nentry(json.loads(base64.b64decode('" + encoded + "')))\n"
    result = subprocess.run(command, input=source, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=None if config['timeout_s'] is None else config["timeout_s"] + 90)
    marker = "@@WB_RESULT@@"
    if marker not in result.stdout:
        raise RuntimeError("SSH/ORFS 调用失败。先在终端验证 ssh 登录及主机指纹。\n" +
                           (result.stderr + result.stdout)[-3500:])
    response = json.loads(result.stdout.rsplit(marker, 1)[1].strip())
    if result.returncode != 0:
        response["ok"] = False
        response["ssh_exit_code"] = result.returncode
    return response
