from __future__ import annotations
from vfsimulator.core.params.types import UarchParameters
from collections import deque
from vfsimulator.core.execution.instruction import DynamicInstruction
from dataclasses import dataclass

@dataclass(frozen=True)
class DispatchBudget:
    vector: int
    predicate: int
    shq_queue: int
    lsq: int
    shq: int

class AddressStateTracker:
    def __init__(self, update_latency: int):
        if type(update_latency) is not int or not 0 < update_latency < 2**63:
            raise ValueError("idu_post_update_ready_latency must be a positive int64")
        self.update_latency = update_latency
        self.updates: dict[str, dict] = {}

    def dependencies(self, accesses) -> list[dict]:
        states = {a["address_state_id"] for a in accesses
                  if a.get("address_state_id") is not None}
        return [dict(self.updates[s]) for s in sorted(states) if s in self.updates]

    def can_dispatch(self, accesses, cycle: int) -> bool:
        return all(cycle >= d["ready_cycle"] for d in self.dependencies(accesses))

    def notify_dispatch(self, inst_id: int, accesses, cycle: int) -> None:
        states = {a["address_state_id"] for a in accesses
                  if a.get("address_state_id") is not None
                  and a.get("update_mode") == "post_update"}
        if states and cycle > 2**63 - 1 - self.update_latency:
            raise ValueError("Address ready_cycle exceeds int64")
        for state in states:
            self.updates[state] = {
                "address_state_id": state,
                "producer_inst_id": inst_id,
                "producer_dispatch_cycle": cycle,
                "ready_cycle": cycle + self.update_latency,
            }


class IDU:
    """Decode window, ordered dispatch and pointer-update timing."""

    def __init__(self, uarch: UarchParameters, db, loops, value_storage):
        self.window_width = uarch.IDU_window_width
        self.issue_width = uarch.IDU_issue_width
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.address_states = AddressStateTracker(uarch.idu_post_update_ready_latency)
        self.window = deque()
        self.loops = loops
        self.value_storage = value_storage
        self.global_shq_preg_gate = uarch.global_shq_preg_gate
        self.vf_startup_cost = db.vf_timing.startup_cost
        self.idu_dispatch_start_advance = uarch.idu_dispatch_start_advance
        self.dispatch_log = []
        self.address_block_log = []
        self.resource_block_log = []

    def empty(self) -> bool:
        return len(self.window) == 0

    def can_accept(self):
        if self.theoretical_limit_mode:
            return True
        return len(self.window) < self.window_width

    def accept(self, inst: DynamicInstruction):
        if not isinstance(inst, DynamicInstruction):
            raise TypeError('IDU accepts only prepared Canonical instructions')
        if self.theoretical_limit_mode or len(self.window) < self.window_width:
            self.window.append(inst)

    def dispatch(self, cycle: int, budget: DispatchBudget) -> list[DynamicInstruction]:
        if not self.window:
            return []
        dispatch_start_gate = max(0, int(self.vf_startup_cost) - int(self.idu_dispatch_start_advance))
        if cycle < dispatch_start_gate:
            return []
        dispatched = []
        address_dependencies = []
        if self.theoretical_limit_mode:
            credits = 10 ** 18
            shq_queue_free = 10 ** 18
            lsq_free = 10 ** 18
            shq_free = 10 ** 18
            issue_budget = len(self.window)
        else:
            credits = budget.vector
            shq_queue_free = budget.shq_queue
            lsq_free = budget.lsq
            shq_free = budget.shq
            issue_budget = self.issue_width
        this_cycle_credits = credits
        predicate_credits = 10 ** 18 if self.theoretical_limit_mode else budget.predicate
        this_cycle_predicate_credits = predicate_credits
        this_cycle_shq_queue = shq_queue_free
        this_cycle_lsq = lsq_free
        this_cycle_shq = shq_free
        if shq_queue_free <= 0 and lsq_free <= 0:
            return []
        if self.global_shq_preg_gate and (int(credits) <= 0 or int(shq_free) <= 0):
            return []
        for inst in list(self.window):
            if len(dispatched) >= issue_budget:
                break
            op = inst.op
            if not self.loops.can_dispatch(inst, cycle):
                break
            is_load = inst.op_class == 'LOAD'
            is_store = inst.op_class == 'STORE'
            if is_load:
                if lsq_free <= 0:
                    break
            elif is_store:
                if lsq_free <= 0:
                    break
                if shq_free <= 0:
                    break
            else:
                if shq_queue_free <= 0:
                    break
                if shq_free <= 0:
                    break
            dst_count = 0
            for d in inst.dst:
                if self.value_storage.is_register(d):
                    dst_count += 1
            predicate_count = sum((self.value_storage.is_predicate(d) for d in inst.dst))
            if credits < dst_count or predicate_credits < predicate_count:
                self.resource_block_log.append({
                    'cy': cycle,
                    'event': 'blocked',
                    'blocked_reason': 'vector_credit' if credits < dst_count else 'predicate_credit',
                    'inst_id': inst.inst_id,
                    'static_instruction_id': inst.static_instruction_id,
                    'iteration_path': inst.iteration_path,
                    'stream_seq': int(inst.stream_seq),
                    'op': op,
                    'vreg': credits,
                    'predicate_phys_free': predicate_credits
                })
                break
            accesses = inst.memory_accesses
            dependencies = self.address_states.dependencies(accesses)
            if not self.address_states.can_dispatch(accesses, cycle):
                self.address_block_log.append({
                    'cy': cycle,
                    'event': 'blocked',
                    'blocked_reason': 'address_state',
                    'inst_id': inst.inst_id,
                    'static_instruction_id': inst.static_instruction_id,
                    'iteration_path': inst.iteration_path,
                    'stream_seq': int(inst.stream_seq),
                    'op': op,
                    'address_dependencies': dependencies
                })
                break
            dispatched.append(inst)
            self.address_states.notify_dispatch(inst.inst_id, accesses, cycle)
            address_dependencies.append(dependencies)
            credits -= dst_count
            predicate_credits -= predicate_count
            if inst.op_class in ('LOAD', 'STORE'):
                lsq_free -= 1
                if inst.op_class in ('COMPUTE', 'STORE'):
                    shq_free -= 1
            elif inst.op_class == 'COMPUTE':
                shq_queue_free -= 1
                shq_free -= 1
        for inst, dependencies in zip(dispatched, address_dependencies):
            self.window.popleft()
            self.dispatch_log.append({
                'cy': cycle,
                'event': 'dispatch',
                'address_dependencies': dependencies,
                'inst_id': inst.inst_id,
                'static_instruction_id': inst.static_instruction_id,
                'iteration_path': inst.iteration_path,
                'stream_seq': int(inst.stream_seq),
                'op': inst.op,
                'dst': inst.dst,
                'src': inst.src,
                'top_block_id': int(inst.top_block_id),
                'vreg': this_cycle_credits,
                'predicate_phys_free': this_cycle_predicate_credits,
                'SHQ_QUEUE': this_cycle_shq_queue,
                'LSQ': this_cycle_lsq,
                'SHQ': this_cycle_shq
            })
            self.loops.notify_dispatch(inst, cycle)
        return dispatched
