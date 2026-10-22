"""Explicit serial composition of native UB instructions and VF invocations.

This is an execution-time sum, not a cross-domain queue scheduler. The caller
must establish serialization from source semantics or a matching execution
trace. Each VF keeps its own startup, drain, register state and address state.
"""
from dataclasses import dataclass, field
from pathlib import Path

from api.legacy_ub_costmodel import predict_legacy_ub_instruction
from api.program_api import predict_from_program
from core.program_ir import VfSimProgram


@dataclass(frozen=True)
class LegacyUbInvocation:
    name: str
    opcode: str
    dtype: str
    hardware_repeat: int
    list_lengths: tuple[int, ...] = ()
    payload_bytes: int | None = None
    provenance: dict = field(default_factory=dict)


@dataclass(frozen=True)
class VfInvocation:
    name: str
    program: VfSimProgram
    provenance: dict = field(default_factory=dict)


def predict_serial_vec(invocations, *, serialization_evidence: str,
                       config_root=None, out_dir='results/serial_vec',
                       dump_results=False):
    """Sum source-order invocations, retaining every timing boundary.

    Excludes scalar preparation, DMA, queue waits and inter-invocation dispatch
    gaps. Shared UB allocation identities remain in the individual programs;
    their data hazards are satisfied by the caller's serialization guarantee.
    """
    if not isinstance(serialization_evidence, str) or not serialization_evidence.strip():
        raise ValueError('Serial composition requires explicit serialization evidence')
    invocations = list(invocations)
    if not invocations:
        raise ValueError('At least one VEC invocation is required')
    if any(not isinstance(x, (LegacyUbInvocation, VfInvocation)) for x in invocations):
        raise TypeError('Expected LegacyUbInvocation or VfInvocation')
    names = [x.name for x in invocations]
    if any(not isinstance(n, str) or not n for n in names) or len(set(names)) != len(names):
        raise ValueError('Invocation names must be nonempty and unique')
    cursor = 0
    events = []
    warnings = []
    for index, invocation in enumerate(invocations):
        if isinstance(invocation, LegacyUbInvocation):
            timing = predict_legacy_ub_instruction(
                invocation.opcode, dtype=invocation.dtype,
                hardware_repeat=invocation.hardware_repeat,
                list_lengths=invocation.list_lengths,
                payload_bytes=invocation.payload_bytes)
            cycles = timing.cycles
            domain = timing.execution_domain
            detail = {'opcode': timing.opcode, 'hardware_repeat': timing.hardware_repeat}
        else:
            result = predict_from_program(
                invocation.program, config_root=config_root,
                out_dir=Path(out_dir)/f'invocation_{index:04d}', dump_results=dump_results)
            cycles = result['cycles']
            domain = 'VF'
            detail = {'instruction_warnings': result['raw'].get('instruction_warnings', [])}
            warnings.extend({'invocation': invocation.name, **w} for w in detail['instruction_warnings'])
        events.append({'name': invocation.name, 'domain': domain, 'start_cycle': cursor,
                       'done_cycle': cursor+cycles, 'cycles': cycles,
                       'provenance': dict(invocation.provenance), **detail})
        cursor += cycles
    return {'cycles': cursor, 'events': events, 'instruction_warnings': warnings,
            'composition': 'serial_execution_sum',
            'serialization_evidence': serialization_evidence,
            'timing_scope': 'VEC execution including each VF startup/drain; excludes scalar/DMA/queue waits/dispatch gaps'}
