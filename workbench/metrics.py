import math


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def parse_orfs_metrics(data):
    """ORFS JSON metrics use ns, um^2, Hz and W, independent of report display units."""
    def n(key):
        return number(data.get("finish__" + key))
    def scale(key, factor):
        value = n(key)
        return None if value is None else number(value * factor)
    return {
        "timing": {
            "wns_ns": n("timing__setup__ws"),
            "whs_ns": n("timing__hold__ws"),
            "tns_ns": n("timing__setup__tns"),
            "fmax_mhz": scale("timing__fmax", 1e-6),
            "stage": "post_route",
        },
        "resources": {
            "cell_count": n("design__instance__count__stdcell"),
            "cell_area_um2": n("design__instance__area__stdcell"),
            "core_area_um2": n("design__core__area"),
            "die_area_um2": n("design__die__area"),
        },
        "power": {
            "total_mw": scale("power__total", 1000),
            "internal_mw": scale("power__internal__total", 1000),
            "switching_mw": scale("power__switching__total", 1000),
            "leakage_mw": scale("power__leakage__total", 1000),
            "method": "vectorless_estimate",
            "scope": "mapped_cells",
            "activity": "ORFS/OpenSTA 默认传播活动率；未标注工作负载 VCD/SAIF",
            "comparable_to_fpga": False,
        },
        "flow_errors": n("flow__errors__count"),
        "route_drc_count": number(data.get("detailedroute__route__drc_errors")),
    }


def unknown_power():
    return {"total_mw": None, "method": "not_measured", "scope": "unknown",
            "activity": None, "comparable_to_asic": False}
