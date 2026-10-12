from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from core.common.instruction import Uop
from core.params.types import InstructionProfile, UarchParameters
from core.uarch.register_bank import PhysicalRegisterBank

@dataclass
class SrcReleaseEvent:
    inst_id: int
    preg: str
    gen: int

class Registers:

    def __init__(self, uarch: UarchParameters, pdb, value_storage=None, dtype='fp32'):
        self.preg_num = uarch.vreg_num
        self.fallback_dtype = dtype
        self.db = pdb
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.vf_startup_cost = pdb.vf_timing.startup_cost
        self.cycle: int = 0
        self.theoretical_limit_legacy_forwarding = uarch.theoretical_limit_legacy_forwarding
        self.consumer_release_start_offset: int = uarch.consumer_release_start_offset
        self.assert_start_release_integrity: bool = uarch.assert_start_release_integrity
        self.enable_credit_visibility_delay: bool = uarch.enable_credit_visibility_delay
        self.idu_visible_preg_delay: int = uarch.idu_visible_preg_delay
        self.enable_isu_queue_model = uarch.enable_isu_queue_model
        self.vector_bank = PhysicalRegisterBank(self.preg_num, name='vector', prefix='p')
        self.predicate_bank = PhysicalRegisterBank(uarch.physical_predicate_registers, name='predicate', prefix='pred')
        self.physical_banks = {physical_id: bank for bank in (self.vector_bank, self.predicate_bank) for physical_id in bank.physical_ids}
        self.freelist = self.vector_bank.freelist
        self.RAT = self.vector_bank.rat
        self.next_dynamic_preg_id: int = self.preg_num
        self.preg_producer = self.vector_bank.producer
        self.preg_producer_uop = self.vector_bank.producer_uop
        self.preg_producer_profile = self.vector_bank.producer_profile
        self.preg_pending = self.vector_bank.pending
        self.preg_consumer_count = self.vector_bank.consumer_count
        self.preg_release_eligible_cycle = self.vector_bank.release_eligible_cycle
        self.preg_generation = self.vector_bank.generation
        self.src_release_events: Dict[int, List[SrcReleaseEvent]] = {}
        for field in (
            'rat',
            'producer',
            'producer_uop',
            'producer_profile',
            'pending',
            'consumer_count',
            'generation',
            'release_eligible_cycle'
        ):
            setattr(self.predicate_bank, field, getattr(self.vector_bank, field))
        self.visible_predicate_free = self.predicate_bank.capacity
        self.idu_mailbox_predicate_release_delta = 0
        self.visible_predicate_free_events: Dict[int, int] = {}
        self.src_release_scheduled_inst_ids: Set[int] = set()
        self.src_release_expected: Dict[int, int] = {}
        self.src_release_seen: Dict[int, int] = {}
        self.visible_preg_free: int = len(self.freelist)
        self.visible_preg_free_events: Dict[int, int] = {}
        self.idu_mailbox_preg_release_delta: int = 0
        self.value_storage = value_storage

    def is_renameable(self, name: Any) -> bool:
        return self.value_storage.is_renameable(name)

    def bank_for_value(self, name: Any):
        return self.predicate_bank if self.value_storage.is_predicate(name) else self.vector_bank

    def get_free_predicate(self) -> int:
        if self.theoretical_limit_mode:
            return 10 ** 18
        if getattr(self, 'enable_credit_visibility_delay', False):
            return self.visible_predicate_free
        return len(self.predicate_bank.freelist)

    def _ready_time_for_src(
        self,
        producer_info: Tuple[str, str, int, str],
        consumer_op: str,
        consumer_form: Optional[str]=None,
        producer_profile: Optional[InstructionProfile]=None,
        consumer_profile: Optional[InstructionProfile]=None
    ) -> int:
        prod_op, prod_form, prod_start, _kind = producer_info
        if producer_profile is not None and consumer_profile is not None:
            fwd = int(self.db.get_forwarding_for_profiles(producer_profile, consumer_profile))
        else:
            fwd = int(self.db.get_forwarding_cycles(
                prod_op,
                consumer_op,
                dtype=self.fallback_dtype,
                producer_form=prod_form,
                consumer_form=consumer_form
            ))
        if (consumer_profile is not None and consumer_profile.op_class == 'COMPUTE') and bool(getattr(self, 'enable_isu_queue_model', False)) and (not self.theoretical_limit_legacy_forwarding):
            return prod_start + max(0, fwd - 1)
        return prod_start + fwd

    def _compute_ready_cycle(self, u: Uop) -> int:
        t = max(self.vf_startup_cost, int(getattr(u, 'shq_ready_cycle', 0)))
        for ps in u.preg_src:
            if ps is None:
                continue
            info = self.preg_producer.get(ps)
            if info is None:
                if ps in self.preg_pending:
                    return 10 ** 9
                continue
            t = max(t, self._ready_time_for_src(info, u.op, u.form, self.preg_producer_profile.get(ps), u.profile))
        return t

    def is_current_mapping(self, preg: str) -> bool:
        return preg in self.RAT.values()

    def can_free_preg(self, preg: str) -> bool:
        if self.theoretical_limit_mode:
            return False
        return self.physical_banks[preg].can_free(preg, self.cycle)

    def try_free_preg(self, preg: str) -> bool:
        if self.theoretical_limit_mode:
            return False
        bank = self.physical_banks[preg]
        if not bank.try_free(preg, self.cycle):
            return False
        if bank is self.predicate_bank:
            delay = self.idu_visible_preg_delay if self.enable_credit_visibility_delay else 0
            if delay <= 0:
                self.visible_predicate_free += 1
                self.idu_mailbox_predicate_release_delta += 1
            else:
                time = self.cycle + delay
                self.visible_predicate_free_events[time] = self.visible_predicate_free_events.get(time, 0) + 1
            return True
        if self.enable_credit_visibility_delay:
            delay = int(self.idu_visible_preg_delay)
            if delay <= 0:
                self.visible_preg_free = int(self.visible_preg_free) + 1
                self.idu_mailbox_preg_release_delta = int(self.idu_mailbox_preg_release_delta) + 1
            else:
                t = int(self.cycle) + delay
                self.visible_preg_free_events[t] = int(self.visible_preg_free_events.get(t, 0)) + 1
        return True

    def on_uop_done(self, u: Uop) -> None:
        if self.assert_start_release_integrity:
            exp = int(self.src_release_expected.get(u.inst_id, 0))
            seen = int(self.src_release_seen.get(u.inst_id, 0))
            if seen != exp:
                raise AssertionError(f'start-release mismatch at done: inst_id={u.inst_id}, seen={seen}, expected={exp}')
        self.src_release_expected.pop(u.inst_id, None)
        self.src_release_seen.pop(u.inst_id, None)

    def schedule_src_release_from_start(self, u: Uop) -> None:
        key = int(u.inst_id)
        if key in self.src_release_scheduled_inst_ids:
            return
        if u.start_cycle is None:
            return
        release_cycle = int(u.start_cycle) + self.consumer_release_start_offset
        src_gens: List[Optional[int]] = list(getattr(u, 'preg_src_gen', [None] * len(u.preg_src)))
        srcs: List[Tuple[str, int]] = []
        for idx, preg in enumerate(u.preg_src):
            if preg is None:
                continue
            gen = src_gens[idx] if idx < len(src_gens) else None
            if gen is None:
                gen = int(self.preg_generation.get(preg, 0))
            srcs.append((preg, int(gen)))
        if srcs:
            evs = self.src_release_events.setdefault(release_cycle, [])
            for preg, gen in srcs:
                evs.append(SrcReleaseEvent(inst_id=key, preg=preg, gen=gen))
        self.src_release_expected[key] = len(srcs)
        self.src_release_seen[key] = 0
        self.src_release_scheduled_inst_ids.add(key)

    def run_src_release_events(self, cycle: int) -> None:
        evs = self.src_release_events.pop(cycle, None)
        if not evs:
            return
        for ev in evs:
            preg, gen = (ev.preg, ev.gen)
            self.src_release_seen[ev.inst_id] = int(self.src_release_seen.get(ev.inst_id, 0)) + 1
            cur_gen = int(self.preg_generation.get(preg, 0))
            if cur_gen != int(gen):
                if self.assert_start_release_integrity:
                    raise AssertionError(f'stale start-release event: inst_id={ev.inst_id}, preg={preg}, event_gen={gen}, cur_gen={cur_gen}, cycle={cycle}')
                continue
            if preg in self.preg_consumer_count:
                self.preg_consumer_count[preg] = max(0, self.preg_consumer_count[preg] - 1)
                if self.preg_consumer_count[preg] == 0:
                    prev = self.preg_release_eligible_cycle.get(preg, -1)
                    self.preg_release_eligible_cycle[preg] = max(prev, cycle)
            self.try_free_preg(preg)

    def try_free_dests(self, u: Uop) -> None:
        for preg in u.preg_dst:
            self.try_free_preg(preg)

    def try_free_eligible_pregs(self) -> None:
        if not self.preg_release_eligible_cycle:
            return
        for preg in list(self.preg_release_eligible_cycle.keys()):
            self.try_free_preg(preg)

    def get_free_preg(self):
        if self.theoretical_limit_mode:
            return 10 ** 18
        return self.visible_preg_free if self.enable_credit_visibility_delay else len(self.vector_bank.freelist)

    def update_visibility(self, cycle):
        self.cycle = cycle
        pred = self.idu_mailbox_predicate_release_delta
        self.idu_mailbox_predicate_release_delta = 0
        released = self.visible_predicate_free_events.pop(cycle, 0)
        self.visible_predicate_free += released
        pred += released
        vec = self.idu_mailbox_preg_release_delta
        self.idu_mailbox_preg_release_delta = 0
        released = self.visible_preg_free_events.pop(cycle, 0)
        self.visible_preg_free += released
        return {'preg_free': vec + released, 'predicate_free': pred}

    def rename(self, inst, profile, cycle):
        self.cycle = cycle
        srcs = inst.src
        dsts = inst.dst

        def key(identity):
            return (
                identity['definition_id'],
                tuple(((i['loop_id'], i['iteration']) for i in identity['iteration_path']))
            )
        for bank in (self.vector_bank, self.predicate_bank):
            required = sum((self.is_renameable(d) and self.bank_for_value(d) is bank for d in dsts))
            if not self.theoretical_limit_mode and required > len(bank.freelist):
                raise RuntimeError(f'Atomic rename precheck failed: {bank.name} credit')
        preg_src = []
        gens = []
        released = []
        for index, source in enumerate(srcs):
            preg = None
            if self.is_renameable(source):
                source_key = key(inst.src_value_instances[index])
                preg = self.RAT.get(source_key)
                if preg is None and self.value_storage.is_predicate(source):
                    raise RuntimeError(f'Predicate source has no renamed producer: {source}')
                if inst.src_value_instance_release[index]:
                    old = self.RAT.pop(source_key, None)
                    if old is not None:
                        released.append(old)
            preg_src.append(preg)
            gens.append(self.preg_generation.get(preg, 0) if preg else None)
        for preg in preg_src:
            if preg is not None:
                self.preg_consumer_count[preg] = self.preg_consumer_count.get(preg, 0) + 1
                self.preg_release_eligible_cycle.pop(preg, None)
        preg_dst = []
        vec_alloc = 0
        for index, destination in enumerate(dsts):
            if not self.is_renameable(destination):
                continue
            bank = self.bank_for_value(destination)
            if self.theoretical_limit_mode:
                physical = f'p{self.next_dynamic_preg_id}'
                self.next_dynamic_preg_id += 1
            else:
                physical = bank.allocate()
            self.physical_banks[physical] = bank
            if bank is self.predicate_bank:
                self.visible_predicate_free -= 1
            destination_key = key(inst.dst_value_instances[index])
            if destination_key in self.RAT:
                raise RuntimeError('Duplicate dynamic Canonical definition')
            if inst.dst_value_instance_keep[index]:
                self.RAT[destination_key] = physical
            preg_dst.append(physical)
            vec_alloc += bank is self.vector_bank
            self.preg_generation[physical] = self.preg_generation.get(physical, 0) + 1
            self.preg_consumer_count[physical] = 0
            self.preg_release_eligible_cycle.pop(physical, None)
            self.preg_pending.add(physical)
        if self.enable_credit_visibility_delay:
            self.visible_preg_free = max(0, self.visible_preg_free - vec_alloc)
        u = Uop(
            inst.inst_id,
            inst.op,
            inst.form,
            list(srcs),
            list(dsts),
            preg_src,
            preg_dst,
            profile=profile,
            preg_src_gen=gens,
            top_block_id=inst.top_block_id,
            iter_stack=list(inst.iter_stack),
            is_last_in_top_block=inst.is_last_in_top_block,
            stream_seq=inst.stream_seq,
            static_instruction_id=inst.static_instruction_id,
            iteration_path=inst.iteration_path,
            src_value_instances=inst.src_value_instances,
            dst_value_instances=inst.dst_value_instances
        )
        for preg in released:
            self.try_free_preg(preg)
        return u

    def on_uop_start(self, uop):
        self.schedule_src_release_from_start(uop)
        if uop.op_class in ('LOAD', 'COMPUTE'):
            for physical_id in uop.preg_dst:
                self.preg_producer[physical_id] = (uop.op, uop.form, uop.start_cycle, uop.op_class)
                self.preg_producer_profile[physical_id] = uop.profile
                self.preg_producer_uop[physical_id] = uop
                self.preg_pending.discard(physical_id)
