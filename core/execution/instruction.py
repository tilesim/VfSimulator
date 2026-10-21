from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from core.params.types import InstructionProfile

if TYPE_CHECKING:
    from core.uarch.registers import AlignGeneration, AlignProducerRecord


@dataclass(frozen=True)
class DynamicInstruction:
    """Prepared Canonical instruction with explicit dynamic identity and control metadata."""

    type: str
    op: str = ""
    form: str = ""
    instruction_class: str = "control"
    inst_id: int = -1
    pc: int = -1
    stream_seq: int = -1
    src: list[str] = field(default_factory=list)
    dst: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    memory_accesses: list[dict] = field(default_factory=list)
    static_instruction_id: str | None = None
    iteration_path: list[dict] = field(default_factory=list)
    src_value_instances: list[dict] = field(default_factory=list)
    dst_value_instances: list[dict] = field(default_factory=list)
    src_value_instance_release: list[bool] = field(default_factory=list)
    dst_value_instance_keep: list[bool] = field(default_factory=list)
    top_block_id: int = 0
    is_last_in_top_block: bool = False
    loop_stack: list[int] = field(default_factory=list)
    iter_stack: list[int] = field(default_factory=list)
    block_key_by_level: list = field(default_factory=list)
    block_end_levels: list[int] = field(default_factory=list)
    loop_depth: int = 0
    in_loop: bool = False
    unroll_factor: int = 1
    unroll_lane: int | None = None
    unroll_group: int | None = None
    orig_iter_base: int | None = None
    lane: int | None = None
    barrier: str = ""
    source_location: dict | None = None

    @property
    def op_class(self):
        return self.instruction_class.upper()


@dataclass
class Uop:
    """Renamed instruction and its execution state, shared by queues and units."""

    inst_id: int
    op: str
    form: str
    src: List[Any]
    dst: List[Any]
    preg_src: List[Optional[str]]
    preg_dst: List[str]
    profile: Optional[InstructionProfile] = None
    ub_transfer_bytes: int = 0
    state: str = "blocked"
    ready_cycle: int = 0
    start_cycle: Optional[int] = None
    done_cycle: Optional[int] = None
    blocked_reason: Optional[str] = None
    producer_op_for_store: Optional[str] = None
    producer_form_for_store: Optional[str] = None
    producer_start_for_store: Optional[int] = None
    store_dependencies_resolved: bool = False
    align_state_operation: Optional[str] = None
    align_state_id: Optional[str] = None
    align_generation: Optional[AlignGeneration] = None
    align_producer_record: Optional[AlignProducerRecord] = None
    align_previous_producer: Optional[AlignProducerRecord] = None
    top_block_id: int = 0
    iter_stack: List[Any] = field(default_factory=list)
    is_last_in_top_block: bool = False
    stream_seq: int = -1
    static_instruction_id: Optional[str] = None
    iteration_path: List[Dict[str, Any]] = field(default_factory=list)
    src_value_instances: List[Dict[str, Any]] = field(default_factory=list)
    dst_value_instances: List[Dict[str, Any]] = field(default_factory=list)
    exu_port: Optional[int] = None
    shq_ready_cycle: int = 0
    lsq_ready_cycle: int = 0
    preg_src_gen: list[int | None] = field(default_factory=list)
    shq_tracked: bool = False
    exq_recv_cycle: int = 0
    exq_pred_issue: int = 0
    admission_seq: int = 0

    @property
    def op_class(self):
        return self.profile.op_class
