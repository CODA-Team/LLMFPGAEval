"""Compatibility configuration for the original TD command-line wrapper."""
import os
from pathlib import Path
from workbench.config import load_config, local_path

_c = load_config()
TD_ROOT = str(local_path(os.environ.get("TD_HOME", _c["td_root"])))
TD_PROMPT = str(Path(TD_ROOT) / "bin/td_commands_prompt.exe")
TD_ARCH_DIR = str(Path(TD_ROOT) / "arch")
TD_DEVICE_DB = "eagle_s20.db"
TD_LICENSE = str(Path(TD_ROOT) / "license/Anlogic.lic")
TD_CABLE = 0
