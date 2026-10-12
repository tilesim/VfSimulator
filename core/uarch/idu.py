from __future__ import annotations
from core.params.types import UarchParameters
from collections import deque
from core.common.instruction import DynamicInstruction
from core.uarch.address_state import AddressStateTracker

class IDU:

    def __init__(self, uarch: UarchParameters, pdb, loops, value_storage):
        self.window_width = uarch.IDU_window_width
        self.issue_width = uarch.IDU_issue_width
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.global_shq_preg_gate = uarch.global_shq_preg_gate
        self.vf_startup_cost = pdb.vf_timing.startup_cost
        self.idu_dispatch_start_advance = uarch.idu_dispatch_start_advance
        self.loops = loops
        self.address_states = AddressStateTracker(uarch.idu_post_update_ready_latency)
        self.window = deque()
        self.dispatch_log = []
        self.address_block_log = []
        self.resource_block_log = []
        self.value_storage = value_storage

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

    def dispatch(self, cycle, budget):
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
