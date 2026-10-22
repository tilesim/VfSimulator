"""Parameter records shared by configuration loading and execution."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InstructionProfile:
    profile_id: int
    op: str
    requested_form: str
    resolved_form: str
    dtype: str
    op_class: str
    fu_type: str
    dispatch_exu: str
    latency: int
    allowed_exu_ports: tuple[int, ...]
    lsu_resource: str = ""
    lsu_issue_interval: int = 1
    align_state_latency: int = 1


@dataclass(frozen=True, slots=True)
class VfTiming:
    startup_cost: int = 0
    drain_cost: int = 0


@dataclass(frozen=True, slots=True)
class UarchParameters:
    # Identity and resource capacities
    name: str = ""
    issue_ports: int = 2
    load_ports: int = 2
    store_ports: int = 1
    ub_slots: int = 2
    ub_bandwidth_bytes_per_cycle: int = 512
    IDU_window_width: int = 6
    IDU_issue_width: int = 5
    LDQ_width: int = 24
    vreg_num: int = 68
    physical_predicate_registers: int = 32
    shq_depth: int = 58
    exq_depth: int = 26
    exq_issue_inflight_cap_per_port: int = 7
    canonical_dynamic_instruction_limit: int = 20000

    # Transport and resource visibility
    idu_to_ooo_delay: int = 1
    ooo_to_shq_delay: int = 1
    ooo_to_lsq_delay: int = 1
    exq_recv_delay: int = 1
    shq_to_exq_port_per_cycle: int = 1
    shq_release_delay: int = 1
    idu_visible_preg_delay: int = 0
    idu_visible_shq_delay: int = 0
    idu_post_update_ready_latency: int = 1
    consumer_release_start_offset: int = 4

    # Loop control
    vloop_to_dispatch_delay: int = 2
    idu_dispatch_start_advance: int = 2
    initial_top_block_vloop_start_cycle: int = 19
    nested_vloop_initial_start_gap: int = 1
    loop1_min_feedback_gap: int = 7
    innermost_iter_dispatch_stride: int = 1

    # Scheduling and register policies
    lsu_store_priority_preg_threshold: int = 1
    shq_exq_dispatch_policy: str = "fu_round_robin_exu0_reserve"
    exu0_reserve_lookahead: int = 8
    exu0_reserve_min_count: int = 1
    enable_isu_queue_model: bool = True
    enable_shq_credit_model: bool = True
    enable_credit_visibility_delay: bool = True
    global_shq_preg_gate: bool = False
    use_explicit_idu_credit_bank: bool = False
    exq_capacity_counts_inflight: bool = False
    enforce_same_cycle_src_hazard: bool = False
    enable_cross_fu_ii: bool = True
    enable_exq_greedy_balance: bool = False
    assert_start_release_integrity: bool = True
    predict_exq_issue_with_cross_fu: bool = False
    shq_exq_static_rr: bool = False

    # Barrier timing
    membar_timing: dict | None = None

    # Theoretical scenarios
    theoretical_limit_mode: bool = False
    theoretical_limit_vloop_only: bool = False
    theoretical_limit_legacy_forwarding: bool = False
    theoretical_limit_direct_issue: bool = False
    theoretical_limit_vloop_to_dispatch_delay: int | None = None
    theoretical_limit_idu_dispatch_start_advance: int | None = None
    theoretical_limit_idu_window_width: int | None = None
    theoretical_limit_idu_issue_width: int | None = None
    theoretical_limit_ldq_width: int | None = None
    theoretical_limit_shq_depth: int | None = None
    theoretical_limit_exq_depth: int | None = None
    theoretical_limit_idu_to_ooo_delay: int | None = None
    theoretical_limit_ooo_to_shq_delay: int | None = None
    theoretical_limit_ooo_to_lsq_delay: int | None = None
    theoretical_limit_exq_recv_delay: int | None = None
    theoretical_limit_shq_release_delay: int | None = None
    theoretical_limit_idu_visible_preg_delay: int | None = None
    theoretical_limit_idu_visible_shq_delay: int | None = None
    theoretical_limit_shq_to_exq_port_per_cycle: int | None = None
    theoretical_limit_exq_issue_inflight_cap_per_port: int | None = None


@dataclass(frozen=True, slots=True)
class LoadedParameters:
    isa: dict[str, Any]
    uarch: dict[str, Any]
    base_uarch: UarchParameters
    forwarding: dict[str, Any]
    initiation_interval: dict[str, Any]
    vf_timing: VfTiming
