"""Timing for legacy UB VEC instructions, outside the register-VF engine.

These costs cover one instruction's execution, excluding scalar preparation,
queue waits, transfers and VF entry/exit. Never insert them as register ALU ops.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class LegacyUbTiming:
    opcode: str
    cycles: int
    hardware_repeat: int
    execution_domain: str = 'legacy_VEC'
    timing_scope: str = 'one UB VEC instruction execution; excludes queue wait and scalar setup'
    calibration: str = 'CANN 9.2.0-beta.1 dav_3510 CAModel; library alias 9599, runtime reports 9589'


def predict_legacy_ub_instruction(opcode: str, *, dtype: str,
                                  hardware_repeat: int,
                                  list_lengths: tuple[int, ...] = (),
                                  payload_bytes: int | None = None) -> LegacyUbTiming:
    """Predict a measured native instruction without invoking the VF scheduler.

    VBS32 sorts 32 FP32 values per hardware repeat, emitting interleaved
    value/index pairs. Repeats 1, 2 and 32 establish 35 + 16*r cycles; repeat 8
    independently validates that law. Other dtypes/opcodes remain unsupported.
    """
    op = str(opcode).upper()
    if dtype != 'fp32':
        raise NotImplementedError(f'No measured legacy UB model: {op}.{dtype}')
    if type(hardware_repeat) is not int or not 1 <= hardware_repeat <= 255:
        raise ValueError('hardware_repeat must be an integer in [1,255]')
    if op in ('COPY_UBUF_TO_UBUF', 'MOV_UB_TO_UB'):
        if type(payload_bytes) is not int or payload_bytes != 160 or hardware_repeat != 1 or list_lengths:
            raise NotImplementedError('Only measured one-burst, five-block (160-byte) UB copy is supported')
        return LegacyUbTiming('COPY_UBUF_TO_UBUF', 30, hardware_repeat)
    if payload_bytes is not None:
        raise ValueError('payload_bytes applies only to UB copy instructions')
    if op in ('VBITSORT', 'VBS32'):
        if list_lengths:
            raise ValueError('VBITSORT has no merge-list lengths')
        return LegacyUbTiming('VBITSORT', 35 + 16 * hardware_repeat, hardware_repeat)
    if op in ('VMRGSORT4', 'VMS4', 'VMS4V2'):
        if any(type(n) is not int or n <= 0 for n in list_lengths):
            raise ValueError('list_lengths must contain positive integer proposal counts')
        # Exact measured configurations used by the 8x1024/k10 TopK leaf.
        # Do not extrapolate to exhausted-list suspension or other list counts.
        measured = {((32,32,32,32),8):552, ((128,128,128,128),2):552, ((10,10),1):48}
        key=(tuple(list_lengths),hardware_repeat)
        if key not in measured:
            raise NotImplementedError(f'Unmeasured merge configuration: {key}')
        return LegacyUbTiming('VMRGSORT4', measured[key], hardware_repeat)
    raise NotImplementedError(f'No measured legacy UB model: {op}.{dtype}')
