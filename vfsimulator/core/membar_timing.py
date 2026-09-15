"""Configuration-driven barrier timing, independent of EXU resources."""
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any

from vfsimulator.core.control_unit import ControlUnit
from vfsimulator.core.isa_traits import get_op_class


@dataclass
class LsuProgress:
    op_class: str
    pending: int = 0
    start_cycle: int | None = None


@dataclass
class TimedBarrier:
    stream_seq: int
    pc: int
    barrier: str
    wait_class: str
    block_class: str
    available_cycle: int
    previous: Any = None
    predecessors: list[LsuProgress] = field(default_factory=list)
    issue_cycle: int | None = None
    release_cycle: int | None = None
    retire_cycle: int | None = None
    retired: bool = False


class TimedControlUnit(ControlUnit):
    """Approximate two-sided synchronization using measured A5 event delays.

    Each barrier seals the preceding segment in dynamic fetch order. Its issue
    waits for the previous barrier's retirement and segment LSU start feedback;
    release also waits for all prior LSU of the specified direction to complete.
    """

    def __init__(self, pdb, dtype, config, *, issue_floor=0):
        super().__init__(pdb, dtype)

        def object_field(value, path):
            if not isinstance(value, Mapping):
                raise ValueError(f'{path} must be an object')
            return value

        def integer(fields, key, path):
            value = fields.get(key)
            if type(value) is not int or not 0 <= value <= (1 << 63) - 1:
                raise ValueError(f'{path}.{key} must be a nonnegative int64')
            return value

        config = object_field(config, 'membar_timing')
        self.admission_delay = integer(config, 'admission_delay', 'membar_timing')
        path = 'membar_timing.lsu_start_to_next_issue'
        feedback = object_field(config.get('lsu_start_to_next_issue'), path)
        self.start_feedback = {name: integer(feedback, name, path) for name in ('LOAD', 'STORE')}
        directions = object_field(config.get('directions'), 'membar_timing.directions')
        self.rules = {}
        for name in self._SUPPORTED:
            path = f'membar_timing.directions.{name}'
            rule = object_field(directions.get(name), path)
            self.rules[name] = {key: integer(rule, key, path) for key in (
                'release_latency', 'retire_latency', 'consumer_delay', 'next_issue_delay')}
        for name in self._SUPPORTED:
            rule = self.rules[name]
            if rule['retire_latency'] < rule['release_latency']:
                raise ValueError('membar retirement cannot precede synchronization release')
            if rule['next_issue_delay'] < 1:
                raise ValueError('membar next_issue_delay must be positive')
        self.issue_floor = issue_floor
        self.cycle = 0
        self.last_barrier = None
        self.segment = {}
        self.pending_starts = {}
        self.history = []
        self.last_retire_cycle = 0

    def observe_instruction(self, inst):
        cls = get_op_class(inst.get('op', ''), self.db, inst.get('form') or self.dtype)
        if cls in ('LOAD', 'STORE'):
            progress = self.segment.setdefault(cls, LsuProgress(cls))
            progress.pending += 1
            self.pending_starts[int(inst['stream_seq'])] = progress

    def notify_lsu_start(self, stream_seq, cycle):
        progress = self.pending_starts.pop(stream_seq, None)
        if progress is not None:
            progress.pending -= 1
            progress.start_cycle = max(cycle, progress.start_cycle or 0)

    def accept_membar(self, node, cycle=0):
        direction = self.normalize_barrier(node.get('barrier', node.get('type')))
        if direction not in self._SUPPORTED:
            return super().accept_membar(node, cycle)
        wait_class, block_class = self._SUPPORTED[direction]
        # SHQ-side barrier order follows stores, and the loads released by a
        # preceding VST_VLD. Initial VLD_VST may issue before its input load.
        predecessor_class = self.last_barrier.block_class if self.last_barrier else 'STORE'
        barrier = TimedBarrier(
            int(node['stream_seq']), int(node.get('pc', -1)), direction,
            wait_class, block_class, max(self.issue_floor, cycle+self.admission_delay),
            self.last_barrier,
            [self.segment[predecessor_class]] if predecessor_class in self.segment else [],
        )
        self.segment = {}
        self.last_barrier = barrier
        self.barriers.append(barrier)

    def _log(self, event, barrier):
        self.history.append(dict(cy=self.cycle, event=event, stream_seq=barrier.stream_seq,
                                 pc=barrier.pc, barrier=barrier.barrier))

    def update(self, has_pending_prior, *, cycle=0, has_pending_dispatch=None):
        self.cycle = cycle
        active = []
        for b in self.barriers:
            rule = self.rules[b.barrier]
            if b.issue_cycle is None:
                ready = b.available_cycle
                previous = b.previous
                if previous is not None:
                    if previous.retire_cycle is None:
                        active.append(b)
                        continue
                    ready = max(ready, previous.retire_cycle+rule['next_issue_delay'])
                if any(p.pending or p.start_cycle is None for p in b.predecessors):
                    active.append(b)
                    continue
                for p in b.predecessors:
                    ready = max(ready, p.start_cycle+self.start_feedback[p.op_class])
                if cycle < ready or (has_pending_dispatch and has_pending_dispatch(b.stream_seq)):
                    active.append(b)
                    continue
                b.issue_cycle = cycle
                b.previous = None
                b.predecessors.clear()
                self._log('issue', b)
            if b.release_cycle is None and cycle >= b.issue_cycle+rule['release_latency']:
                if not has_pending_prior(b.stream_seq, b.wait_class):
                    b.release_cycle = cycle
                    b.retire_cycle = cycle+rule['retire_latency']-rule['release_latency']
                    self._log('sync_release', b)
            if b.retire_cycle is not None and cycle >= b.retire_cycle and not b.retired:
                b.retired = True
                self.last_retire_cycle = max(self.last_retire_cycle, b.retire_cycle)
                self._log('retire', b)
            gate_open = b.release_cycle is not None and cycle >= b.release_cycle+rule['consumer_delay']
            if not (b.retired and gate_open):
                active.append(b)
        self.barriers = active

    def blocks(self, inst):
        seq = int(inst.get('stream_seq', -1))
        cls = get_op_class(inst.get('op', ''), self.db, inst.get('form') or self.dtype)
        for b in self.barriers:
            if seq > b.stream_seq and cls == b.block_class:
                if b.release_cycle is None or self.cycle < b.release_cycle+self.rules[b.barrier]['consumer_delay']:
                    return True
        return False
