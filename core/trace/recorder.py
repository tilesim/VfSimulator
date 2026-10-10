from __future__ import annotations
from typing import Any, Dict, List
from core.common.instruction import Uop
import json

class TraceRecorder:

    def __init__(self, registers):
        self.registers = registers
        self.cycle: int = 0
        self.history: List[Dict[str, Any]] = []
        self.cyc_start_log: List[Dict[str, Any]] = []
        self.cyc_done_log: List[Dict[str, Any]] = []

    def dump_simple_logs(self, start_path='start_log.jsonl', done_path='done_log.jsonl') -> None:
        with open(start_path, 'w', encoding='utf-8') as f:
            for item in self.cyc_start_log:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')
        with open(done_path, 'w', encoding='utf-8') as f:
            for item in self.cyc_done_log:
                f.write(json.dumps(item, ensure_ascii=False) + '\n')

    def _log_start_simple(self, u: Uop) -> None:
        profile = u.profile
        op_class = profile.op_class if profile is not None else None
        self.cyc_start_log.append({
            'cy': self.cycle,
            'inst_id': u.inst_id,
            'static_instruction_id': u.static_instruction_id,
            'iteration_path': u.iteration_path,
            'stream_seq': u.stream_seq,
            'src_value_instances': u.src_value_instances,
            'dst_value_instances': u.dst_value_instances,
            'op': u.op,
            'form': u.form,
            'op_class': op_class,
            'fu_type': profile.fu_type if profile is not None and op_class == 'COMPUTE' else None,
            'exu_port': u.exu_port,
            'ready_cycle': u.ready_cycle,
            'dst': u.dst,
            'src': u.src,
            'preg_dst': u.preg_dst,
            'preg_src': u.preg_src
        })

    def _log_done_simple(self, u: Uop) -> None:
        profile = u.profile
        op_class = profile.op_class if profile is not None else None
        self.cyc_done_log.append({
            'cy': u.done_cycle if u.done_cycle is not None else self.cycle,
            'inst_id': u.inst_id,
            'static_instruction_id': u.static_instruction_id,
            'iteration_path': u.iteration_path,
            'stream_seq': u.stream_seq,
            'src_value_instances': u.src_value_instances,
            'dst_value_instances': u.dst_value_instances,
            'op': u.op,
            'form': u.form,
            'op_class': op_class,
            'fu_type': profile.fu_type if profile is not None and op_class == 'COMPUTE' else None,
            'dst': u.dst,
            'src': u.src
        })

    def _log(self, event: str, u: Uop) -> None:
        self.history.append({
            'cy': self.cycle,
            'event': event,
            'id': u.inst_id,
            'static_instruction_id': u.static_instruction_id,
            'iteration_path': u.iteration_path,
            'stream_seq': u.stream_seq,
            'src_value_instances': u.src_value_instances,
            'dst_value_instances': u.dst_value_instances,
            'op': u.op,
            'form': u.form,
            'state': u.state,
            'blocked_reason': u.blocked_reason,
            'ready': u.ready_cycle,
            'start': u.start_cycle,
            'done': u.done_cycle,
            'src': u.src,
            'dst': u.dst,
            'preg_src': u.preg_src,
            'src_predicate_phys': [p for p in u.preg_src if self.registers.physical_banks.get(p) is self.registers.predicate_bank],
            'dst_predicate_phys': [p for p in u.preg_dst if self.registers.physical_banks.get(p) is self.registers.predicate_bank],
            'vector_phys_free': len(self.registers.vector_bank.freelist),
            'predicate_phys_free': len(self.registers.predicate_bank.freelist),
            'preg_dst': u.preg_dst,
            'producer_op_for_store': u.producer_op_for_store,
            'store_dependencies_resolved': u.store_dependencies_resolved,
            'align_state_operation': u.align_state_operation,
            'align_state_id': u.align_state_id,
            'align_generation': u.align_generation.generation_id if u.align_generation is not None else None,
            'producer_form_for_store': u.producer_form_for_store,
            'producer_start_for_store': u.producer_start_for_store
        })

    def dump_history(self, path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=2, ensure_ascii=False)
