"""Run: python app.py serve | check | run reaction_timer --targets fpga asic."""
import argparse
import json
import sys

from workbench.config import PROJECTS
from workbench.runner import Runner
from workbench.server import serve


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="本地 FPGA / 远程 ASIC 工作台")
    commands = parser.add_subparsers(dest="action", required=True)
    web = commands.add_parser("serve")
    web.add_argument("--port", type=int, default=8765)
    commands.add_parser("check")
    run = commands.add_parser("run")
    run.add_argument("project", choices=PROJECTS)
    run.add_argument("--targets", nargs="+", choices=("fpga", "asic"), default=["fpga", "asic"])
    args = parser.parse_args()
    if args.action == "serve":
        serve(args.port)
        return 0
    runner = Runner()
    if args.action == "check":
        result = runner.check()
        ok = all(result["local"].values()) and result["remote"].get("ok")
    else:
        job = runner.create(args.project, args.targets)
        print("任务 " + job["id"], file=sys.stderr, flush=True)
        result = runner.run(job["id"])
        ok = result["status"] == "completed"
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
