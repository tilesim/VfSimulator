"""Measured affine-index A5 LSU profiles; unsupported patterns fail explicitly."""
from dataclasses import dataclass


@dataclass(frozen=True)
class IndexedMemoryTiming:
    latency: int
    initiation_interval: int | None


def indexed_memory_timing(op, form, attributes):
    if form != 'fp16':
        raise ValueError(f'Unmeasured indexed memory form: {op}.{form}')
    stride=attributes.get('index_stride_elements')
    lanes=attributes.get('active_lanes')
    if type(stride) is not int or type(lanes) is not int:
        raise ValueError(f'{op} requires resolved index_stride_elements and active_lanes')
    # Columns: 32 and 128 active contiguous predicate lanes; all indices are
    # unsigned element offsets, not byte offsets. Each cell is a verified
    # isolated pop-to-completion sample, not a fitted ordinary-load latency.
    measured={
        'VGATHER2':{1:(21,21),2:(21,23),16:(28,51),64:(52,147),128:(52,147)},
        'VSCATTER':{1:(13,13),2:(17,31),16:(52,171),64:(172,651),128:(172,651)},
    }
    if op not in measured or stride not in measured[op] or lanes not in (32,128):
        raise ValueError(f'Unmeasured indexed memory pattern: {op} stride={stride}, lanes={lanes}')
    interval={('VGATHER2',128,128):128,('VSCATTER',64,128):643}.get((op,stride,lanes))
    return IndexedMemoryTiming(measured[op][stride][lanes==128],interval)
