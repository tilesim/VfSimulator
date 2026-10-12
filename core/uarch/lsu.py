from __future__ import annotations
from core.params.types import UarchParameters
from typing import Any, Dict, List, Optional, Set, Tuple
from core.common.instruction import Uop, AlignGeneration, AlignProducerRecord

class LSU:

    def __init__(self, uarch: UarchParameters, pdb, registers, shq, trace, control):
        self.registers = registers
        self.shq = shq
        self.trace = trace
        self.control = control
        self.db = pdb
        self.load_ports = uarch.load_ports
        self.store_ports = uarch.store_ports
        self.ub_slots = uarch.ub_slots
        self.ub_bandwidth_bytes_per_cycle = uarch.ub_bandwidth_bytes_per_cycle
        self.lsu_store_priority_preg_threshold = uarch.lsu_store_priority_preg_threshold
        self.running = {}
        self.vf_startup_cost = pdb.vf_timing.startup_cost
        self.lsq_depth = uarch.LDQ_width
        self.enable_shq_credit_model = uarch.enable_shq_credit_model
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.ub_budget_cycle = -1
        self.ub_bytes_issued = 0
        self.LSQ: List[Uop] = []
        self.align_state_open: Dict[str, AlignGeneration] = {}
        self.align_state_next_generation: Dict[str, int] = {}
        self.align_load_initializers: Dict[str, AlignProducerRecord] = {}

    def _load_ready_cycle(self, u: Uop) -> int:
        ready = max(self.vf_startup_cost, int(getattr(u, 'lsq_ready_cycle', 0)))
        producer = u.align_load_producer
        if producer is not None:
            if producer.start_cycle is None:
                return 10 ** 9
            ready = max(ready, producer.start_cycle + self.db.get_forwarding_for_profiles(producer.profile, u.profile))
        return ready

    def _store_ready_cycle(self, u: Uop) -> Tuple[int, Optional[str], Optional[str], Optional[int]]:
        u.store_dependencies_resolved = False
        for ps in u.preg_src:
            if ps is None:
                continue
            if ps in self.registers.preg_pending and ps not in self.registers.preg_producer:
                return (10 ** 9, None, None, None)
        best_t = -1
        pop = None
        pform = None
        pst = None
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.registers.preg_producer.get(ps)
            if info is None:
                continue
            prod_op, prod_form, prod_start, kind = info
            producer_profile = self.registers.preg_producer_profile.get(ps)
            if kind not in ('COMPUTE', 'LOAD') and (not (producer_profile is not None and producer_profile.op_class in ('COMPUTE', 'LOAD'))):
                continue
            cand = self.registers._ready_time_for_src(info, u.op, u.form, producer_profile, u.profile)
            if cand > best_t:
                best_t = cand
                pop = prod_op
                pform = prod_form
                pst = prod_start
        has_dependency = best_t >= 0
        generation = u.align_generation
        if u.align_state_operation == 'consume' and generation is not None:
            if any((record.start_cycle is None for record in generation.producers)):
                return (10 ** 9, pop, pform, pst)
            state_ready = int(getattr(u, 'lsq_ready_cycle', 0))
            for record in generation.producers:
                state_ready = max(
                    state_ready,
                    int(record.start_cycle) + int(self.db.get_forwarding_for_profiles(record.profile, u.profile))
                )
            best_t = max(best_t, state_ready)
            has_dependency = True
        if not has_dependency:
            return (10 ** 9, None, None, None)
        best_t = max(best_t, int(getattr(u, 'lsq_ready_cycle', 0)))
        u.store_dependencies_resolved = True
        return (best_t, pop, pform, pst)

    def bind_align_state(self, u: Uop, attributes: Any) -> None:
        if not isinstance(attributes, dict):
            return
        operation = str(attributes.get('align_state_operation', '')).lower()
        state_id = str(attributes.get('align_state_id', ''))
        if operation in {'load_init', 'load_use'}:
            if not state_id:
                raise ValueError('Load align state ID is required')
            u.align_state_operation, u.align_state_id = (operation, state_id)
            if operation == 'load_init':
                record = AlignProducerRecord(u.inst_id, u.stream_seq, u.profile)
                self.align_load_initializers[state_id] = record
                u.align_producer_record = record
            else:
                if state_id not in self.align_load_initializers:
                    raise ValueError(f'Uninitialized load align state: {state_id}')
                u.align_load_producer = self.align_load_initializers[state_id]
            return
        if operation not in {'append', 'consume'} or not state_id:
            return
        generation = self.align_state_open.get(state_id)
        if generation is None:
            generation_id = self.align_state_next_generation.get(state_id, 0)
            generation = AlignGeneration(state_id, generation_id)
            self.align_state_open[state_id] = generation
            self.align_state_next_generation[state_id] = generation_id + 1
        u.align_state_operation = operation
        u.align_state_id = state_id
        u.align_generation = generation
        if operation == 'append':
            record = AlignProducerRecord(inst_id=u.inst_id, stream_seq=u.stream_seq, profile=u.profile)
            generation.producers.append(record)
            u.align_producer_record = record
            return
        generation.consumer_inst_id = u.inst_id
        generation_id = self.align_state_next_generation.get(state_id, generation.generation_id + 1)
        self.align_state_open[state_id] = AlignGeneration(state_id, generation_id)
        self.align_state_next_generation[state_id] = generation_id + 1

    def _log_membar_blocked(self, u: Uop) -> None:
        old_reason = u.blocked_reason
        u.blocked_reason = 'membar'
        self.trace._log('blocked', u)
        u.blocked_reason = old_reason

    @staticmethod
    def _lsu_age_key(u: Uop) -> Tuple[int, int]:
        stream_seq = int(u.stream_seq) if int(u.stream_seq) >= 0 else int(u.inst_id)
        return (stream_seq, int(u.inst_id))

    def _lsu_issue_key(self, u: Uop) -> Tuple[int, int, int]:
        age = self._lsu_age_key(u)
        preg_pressure = len(self.registers.freelist) < self.lsu_store_priority_preg_threshold
        preferred_class = 'STORE' if preg_pressure else 'LOAD'
        class_priority = 0 if u.profile.op_class == preferred_class else 1
        return (class_priority, *age)

    def _issue_ready_lsu(
        self,
        cycle: int,
        issued_loads: int,
        issued_stores: int,
        issued_total: int,
        membar_blocked_logged_ids: Optional[Set[int]]=None
    ) -> Tuple[int, int, int]:
        """Issue pressure-ranked ready LSU operations within shared UB limits."""
        if self.ub_budget_cycle != cycle:
            self.ub_budget_cycle = cycle
            self.ub_bytes_issued = 0
        if cycle < self.vf_startup_cost or issued_total >= self.ub_slots:
            return (issued_loads, issued_stores, issued_total)
        candidates = sorted(
            (u for u in self.LSQ if u.state == 'ready' and u.profile and (u.profile.op_class in ('LOAD', 'STORE'))),
            key=self._lsu_issue_key
        )
        issued: List[Uop] = []
        for u in candidates:
            if issued_total >= self.ub_slots:
                break
            op_class = u.profile.op_class
            transfer_bytes = u.ub_transfer_bytes
            if transfer_bytes <= 0:
                raise ValueError('Missing Uop UB transfer bytes')
            if transfer_bytes > self.ub_bandwidth_bytes_per_cycle:
                raise ValueError('UB bandwidth budget cannot fit one LSU transaction')
            if self.ub_bytes_issued + transfer_bytes > self.ub_bandwidth_bytes_per_cycle:
                continue
            if op_class == 'LOAD' and issued_loads >= self.load_ports:
                continue
            if op_class == 'STORE' and issued_stores >= self.store_ports:
                continue
            if self._blocked_by_control_unit(u):
                if membar_blocked_logged_ids is None or u.inst_id not in membar_blocked_logged_ids:
                    self._log_membar_blocked(u)
                    if membar_blocked_logged_ids is not None:
                        membar_blocked_logged_ids.add(u.inst_id)
                continue
            if op_class == 'STORE' and (not u.store_dependencies_resolved) and (u.producer_op_for_store is None):
                continue
            u.start_cycle = cycle
            control = self.control
            if control is not None and hasattr(control, 'notify_lsu_start'):
                control.notify_lsu_start(u.stream_seq, cycle)
            u.blocked_reason = None
            u.done_cycle = cycle + u.profile.latency
            u.state = 'running'
            self.running[u.inst_id] = u
            if u.align_producer_record is not None:
                u.align_producer_record.start_cycle = cycle
            self.registers.on_uop_start(u)
            if op_class == 'LOAD':
                issued_loads += 1
            else:
                issued_stores += 1
                if self.enable_shq_credit_model and bool(getattr(u, 'shq_tracked', False)):
                    self.shq.schedule_shq_release(cycle, 1)
                    u.shq_tracked = False
            issued_total += 1
            self.ub_bytes_issued += transfer_bytes
            self.trace._log('start', u)
            self.trace._log_start_simple(u)
            issued.append(u)
        self.remove(issued)
        return (issued_loads, issued_stores, issued_total)

    def _blocked_by_control_unit(self, u):
        return self.control.blocks(u) if self.control is not None else False

    def remove(self, issued):
        ids = {u.inst_id for u in issued}
        self.LSQ[:] = [u for u in self.LSQ if u.inst_id not in ids]

    def get_free_lsq(self):
        return 10 ** 18 if self.theoretical_limit_mode else self.lsq_depth - len(self.LSQ)

    def has_pending_before(self, seq, op_class):
        return any((u.stream_seq < seq and u.profile.op_class == op_class for u in [*self.LSQ, *self.running.values()]))

    def finish(self, cycle):
        completed = []
        for key, u in list(self.running.items()):
            if cycle >= u.done_cycle:
                u.state = 'done'
                if u.align_producer_record is not None:
                    u.align_producer_record.done_cycle = u.done_cycle
                completed.append(u)
                del self.running[key]
        return completed

    def update_ready(self, cycle, stores_only=False):
        for u in self.LSQ:
            if stores_only and u.profile.op_class != 'STORE':
                continue
            if u.profile.op_class == 'LOAD':
                u.ready_cycle = self._load_ready_cycle(u)
            else:
                u.ready_cycle, u.producer_op_for_store, u.producer_form_for_store, u.producer_start_for_store = self._store_ready_cycle(u)
            u.state = 'ready' if cycle >= u.ready_cycle else 'blocked'

    def enqueue(self, uop, cycle, delay):
        uop.lsq_ready_cycle = cycle + max(0, delay)
        self.LSQ.append(uop)
