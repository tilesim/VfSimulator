"""Microarchitecture configuration resolution; no file I/O or timing queries."""
from __future__ import annotations
from collections.abc import Mapping
from dataclasses import fields, replace
from .types import UarchParameters

MAX_EXU_PORTS = 2

def parse_uarch(values: Mapping, base: UarchParameters | None = None) -> UarchParameters:
    if not isinstance(values, Mapping):
        raise ValueError("uarch must be an object")
    known_fields = {field.name for field in fields(UarchParameters)}
    for name in values:
        if name not in known_fields:
            raise ValueError(f"Unknown uarch field: {name}")
    defaults = base if base is not None else UarchParameters()
    parsed = {}
    for field in fields(UarchParameters):
        name = field.name
        if name not in values:
            continue
        value = values[name]
        default = getattr(defaults, name)
        if name == "membar_timing":
            if not isinstance(value, Mapping):
                raise ValueError("membar_timing must be an object")
            value = dict(value)
        elif type(default) is bool:
            if type(value) is not bool:
                raise ValueError(f"{name} must be a boolean")
        elif type(default) is int or default is None:
            if type(value) is not int:
                raise ValueError(f"{name} must be an integer")
        elif not isinstance(value, str):
            raise ValueError(f"{name} must be a string")
        parsed[name] = value
    cfg = replace(defaults, **parsed)
    if not 0 < cfg.physical_predicate_registers <= 2**31 - 1:
        raise ValueError("physical_predicate_registers must be a positive int32")
    if not 0 < cfg.idu_post_update_ready_latency < 2**63:
        raise ValueError("idu_post_update_ready_latency must be a positive int64")
    return cfg

def resolve_uarch(base: Mapping | UarchParameters, overrides: Mapping | None = None, *,
                  theoretical_vloop_only: bool = False, direct_issue: bool = False) -> UarchParameters:
    cfg = base if isinstance(base, UarchParameters) else parse_uarch(base)
    if overrides:
        cfg = parse_uarch(overrides, cfg)
    if not 0 < cfg.issue_ports <= MAX_EXU_PORTS:
        raise ValueError("The current model supports one or two EXU ports; A6 requires a separate architecture")
    if theoretical_vloop_only or direct_issue:
        cfg = replace(cfg, theoretical_limit_mode=True, theoretical_limit_vloop_only=True)
    if direct_issue:
        cfg = replace(cfg, theoretical_limit_legacy_forwarding=True, theoretical_limit_direct_issue=True)
    return apply_theoretical_limit_overrides(cfg) if cfg.theoretical_limit_mode else cfg

def allowed_exu_ports(tag: str) -> tuple[int, ...]:
    """Decode current architecture port tags once when creating a profile."""
    return (0,) if tag == "EXU0_ONLY" else tuple(range(MAX_EXU_PORTS))

def apply_theoretical_limit_overrides(cfg: UarchParameters) -> UarchParameters:
    huge = 10**18
    changes = {}
    changes['vloop_to_dispatch_delay'] = cfg.theoretical_limit_vloop_to_dispatch_delay if cfg.theoretical_limit_vloop_to_dispatch_delay is not None else 4
    changes['idu_dispatch_start_advance'] = cfg.theoretical_limit_idu_dispatch_start_advance if cfg.theoretical_limit_idu_dispatch_start_advance is not None else 0
    changes['IDU_window_width'] = cfg.theoretical_limit_idu_window_width if cfg.theoretical_limit_idu_window_width is not None else huge
    changes['IDU_issue_width'] = cfg.theoretical_limit_idu_issue_width if cfg.theoretical_limit_idu_issue_width is not None else huge
    changes['LDQ_width'] = cfg.theoretical_limit_ldq_width if cfg.theoretical_limit_ldq_width is not None else huge
    changes['shq_depth'] = cfg.theoretical_limit_shq_depth if cfg.theoretical_limit_shq_depth is not None else huge
    changes['exq_depth'] = cfg.theoretical_limit_exq_depth if cfg.theoretical_limit_exq_depth is not None else huge
    changes['idu_to_ooo_delay'] = cfg.theoretical_limit_idu_to_ooo_delay if cfg.theoretical_limit_idu_to_ooo_delay is not None else 0
    changes['ooo_to_shq_delay'] = cfg.theoretical_limit_ooo_to_shq_delay if cfg.theoretical_limit_ooo_to_shq_delay is not None else 0
    changes['ooo_to_lsq_delay'] = cfg.theoretical_limit_ooo_to_lsq_delay if cfg.theoretical_limit_ooo_to_lsq_delay is not None else 0
    changes['exq_recv_delay'] = cfg.theoretical_limit_exq_recv_delay if cfg.theoretical_limit_exq_recv_delay is not None else 0
    changes['shq_release_delay'] = cfg.theoretical_limit_shq_release_delay if cfg.theoretical_limit_shq_release_delay is not None else 0
    changes['idu_visible_preg_delay'] = cfg.theoretical_limit_idu_visible_preg_delay if cfg.theoretical_limit_idu_visible_preg_delay is not None else 0
    changes['idu_visible_shq_delay'] = cfg.theoretical_limit_idu_visible_shq_delay if cfg.theoretical_limit_idu_visible_shq_delay is not None else 0
    changes['shq_to_exq_port_per_cycle'] = cfg.theoretical_limit_shq_to_exq_port_per_cycle if cfg.theoretical_limit_shq_to_exq_port_per_cycle is not None else huge
    changes['exq_issue_inflight_cap_per_port'] = cfg.theoretical_limit_exq_issue_inflight_cap_per_port if cfg.theoretical_limit_exq_issue_inflight_cap_per_port is not None else 0
    if cfg.theoretical_limit_direct_issue:
        changes.update(enable_isu_queue_model=False, enable_shq_credit_model=False,
                       enable_credit_visibility_delay=False,
                       exq_recv_delay=0, shq_to_exq_port_per_cycle=huge,
                       exq_issue_inflight_cap_per_port=0)
    return replace(cfg, **changes)
