"""根据 Verilog 顶层端口自动生成 .adc 引脚约束模板。

⚠️ 注意：.adc 的**具体关键字**以你安装的 TD 版本为准（不同版本有差异）。
    最稳妥：先在 TD GUI 里做一次引脚约束，然后 File 里导出/查看它生成的 .adc 格式，
    再回来微调本脚本里的 `set_pin_assignment` 那一行。
    拿到板子后把 LOCATION = X 替换成实际引脚号。

用法：
    python anlogic/gen_adc.py 你的dut.v  >  top.adc
"""
import os
import sys

# 让本脚本能 import 项目根目录的 verilog_utils
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from verilog_utils import parse_module_name, parse_ports


def gen_adc(verilog):
    top = parse_module_name(verilog)
    ports = parse_ports(verilog)
    lines = ["# 引脚约束模板（自动生成，请把 LOCATION = X 改成实际引脚号）",
             f"# 顶层模块：{top}"]
    if ports:
        for p in ports:
            if p["width"] > 1:
                comment = f"  # {p['dir']} [{p['width']-1}:0]"
            else:
                comment = f"  # {p['dir']}"
            lines.append(f"set_pin_assignment {{ {p['name']} }} {{ LOCATION = X }}{comment}")
    else:
        lines.append("# 未能自动解析端口，请手动填写。")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python anlogic/gen_adc.py <dut.v>")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        verilog = f.read()
    print(gen_adc(verilog))
