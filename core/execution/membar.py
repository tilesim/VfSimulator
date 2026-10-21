from __future__ import annotations
from dataclasses import dataclass, field
from collections.abc import Mapping
from typing import Any, Callable, List
from core.execution.instruction import DynamicInstruction, Uop


@dataclass
class MembarState:
    stream_seq: int
    pc: int
    barrier: str
    wait_class: str
    block_class: str
    released: bool = False


class MembarController:
    """Control-side model for explicit vector memory barriers."""

    _SUPPORTED = {
        "VST_VLD": ("STORE", "LOAD"),
        "VLD_VST": ("LOAD", "STORE"),
        "VST_VST": ("STORE", "STORE"),
        "VV_ALL": ("ALL", "ALL"),
    }

    @staticmethod
    def _pending(has_pending_prior, stream_seq, wait_class):
        classes = ("LOAD", "STORE") if wait_class == "ALL" else (wait_class,)
        return any(has_pending_prior(stream_seq, cls) for cls in classes)

    def __init__(self, pdb: Any, dtype: str = "fp32") -> None:
        self.db = pdb
        self.fallback_dtype = str(dtype)
        self.barriers: List[MembarState] = []

    @staticmethod
    def normalize_barrier(raw: Any) -> str:
        text = str(raw or "VST_VLD").strip().upper()
        if "." in text:
            text = text.rsplit(".", 1)[-1]
        return text

    def accept_membar(self, node: DynamicInstruction, cycle: int = 0) -> None:
        barrier = self.normalize_barrier(node.barrier)
        if barrier not in self._SUPPORTED:
            if hasattr(self.db, "record_warning"):
                self.db.record_warning(
                    "unsupported_membar_type",
                    barrier=barrier,
                    pc=int(node.pc),
                    stream_seq=int(node.stream_seq),
                )
            return
        wait_class, block_class = self._SUPPORTED[barrier]
        self.barriers.append(
            MembarState(
                stream_seq=int(node.stream_seq),
                pc=int(node.pc),
                barrier=barrier,
                wait_class=wait_class,
                block_class=block_class,
            )
        )

    def observe_instruction(self, inst: DynamicInstruction | Uop) -> None:
        pass

    def notify_lsu_start(self, stream_seq: int, cycle: int) -> None:
        pass

    def update(
        self,
        has_pending_prior: Callable[[int, str], bool],
        *,
        cycle: int = 0,
        has_pending_dispatch=None,
    ) -> None:
        for barrier in self.barriers:
            if barrier.released:
                continue
            barrier.released = not self._pending(
                has_pending_prior, barrier.stream_seq, barrier.wait_class
            )
        self.barriers = [barrier for barrier in self.barriers if not barrier.released]

    def empty(self) -> bool:
        return len(self.barriers) == 0

    def blocks(self, inst: DynamicInstruction | Uop) -> bool:
        stream_seq = int(inst.stream_seq)
        if stream_seq < 0:
            return False
        op_class = inst.op_class
        for barrier in self.barriers:
            if barrier.released:
                continue
            if stream_seq <= int(barrier.stream_seq):
                continue
            if (
                barrier.block_class == "ALL" and op_class in {"LOAD", "STORE"}
            ) or op_class == barrier.block_class:
                return True
        return False


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


class TimedMembarController(MembarController):
    """Approximate two-sided synchronization using measured A5 event delays.

    Each barrier seals the preceding segment in dynamic fetch order. Its issue
    waits for the previous barrier's retirement and segment LSU start feedback;
    release also waits for all prior LSU of the specified direction to complete.
    """

    def __init__(self, pdb, dtype, config, *, issue_floor=0):
        super().__init__(pdb, dtype)

        def object_field(value, path):
            if not isinstance(value, Mapping):
                raise ValueError(f"{path} must be an object")
            return value

        def integer(fields, key, path):
            value = fields.get(key)
            if type(value) is not int or not 0 <= value <= (1 << 63) - 1:
                raise ValueError(f"{path}.{key} must be a nonnegative int64")
            return value

        config = object_field(config, "membar_timing")
        self.admission_delay = integer(config, "admission_delay", "membar_timing")
        path = "membar_timing.lsu_start_to_next_issue"
        feedback = object_field(config.get("lsu_start_to_next_issue"), path)
        self.start_feedback = {
            name: integer(feedback, name, path) for name in ("LOAD", "STORE")
        }
        directions = object_field(config.get("directions"), "membar_timing.directions")
        self.rules = {}
        for name in self._SUPPORTED:
            if name not in directions and name in {"VV_ALL", "VST_VST"}:
                continue
            path = f"membar_timing.directions.{name}"
            rule = object_field(directions.get(name), path)
            self.rules[name] = {
                key: integer(rule, key, path)
                for key in (
                    "release_latency",
                    "retire_latency",
                    "consumer_delay",
                    "next_issue_delay",
                )
            }
        for name in self.rules:
            rule = self.rules[name]
            if rule["retire_latency"] < rule["release_latency"]:
                raise ValueError(
                    "membar retirement cannot precede synchronization release"
                )
            if rule["next_issue_delay"] < 1:
                raise ValueError("membar next_issue_delay must be positive")
        self.issue_floor = issue_floor
        self.cycle = 0
        self.last_barrier = None
        self.segment = {}
        self.pending_starts = {}
        self.history = []
        self.last_retire_cycle = 0

    def observe_instruction(self, inst):
        cls = inst.op_class
        if cls in ("LOAD", "STORE"):
            progress = self.segment.setdefault(cls, LsuProgress(cls))
            progress.pending += 1
            self.pending_starts[int(inst.stream_seq)] = progress

    def notify_lsu_start(self, stream_seq, cycle):
        progress = self.pending_starts.pop(stream_seq, None)
        if progress is not None:
            progress.pending -= 1
            progress.start_cycle = max(cycle, progress.start_cycle or 0)

    def accept_membar(self, node, cycle=0):
        direction = self.normalize_barrier(node.barrier)
        if direction not in self._SUPPORTED:
            return super().accept_membar(node, cycle)
        wait_class, block_class = self._SUPPORTED[direction]
        if direction not in self.rules and hasattr(self.db, "record_warning"):
            self.db.record_warning("unmeasured_membar_timing", barrier=direction)
        predecessor_class = (
            self.last_barrier.block_class if self.last_barrier else "STORE"
        )
        barrier = TimedBarrier(
            int(node.stream_seq),
            int(node.pc),
            direction,
            wait_class,
            block_class,
            max(self.issue_floor, cycle + self.admission_delay),
            self.last_barrier,
            list(self.segment.values())
            if predecessor_class == "ALL"
            else [self.segment[predecessor_class]]
            if predecessor_class in self.segment
            else [],
        )
        self.segment = {}
        self.last_barrier = barrier
        self.barriers.append(barrier)

    def _log(self, event, barrier):
        self.history.append(
            dict(
                cy=self.cycle,
                event=event,
                stream_seq=barrier.stream_seq,
                pc=barrier.pc,
                barrier=barrier.barrier,
            )
        )

    def update(self, has_pending_prior, *, cycle=0, has_pending_dispatch=None):
        self.cycle = cycle
        active = []
        for b in self.barriers:
            rule = self.rules.get(b.barrier)
            if rule is None:
                # Completion ordering is available even when this CAModel's
                # two LSU barrier stages have no calibrated timing rule.
                previous_ready = b.previous is None or (
                    b.previous.retire_cycle is not None
                    and cycle >= b.previous.retire_cycle
                )
                if (
                    previous_ready
                    and not self._pending(has_pending_prior, b.stream_seq, b.wait_class)
                    and not (
                        has_pending_dispatch and has_pending_dispatch(b.stream_seq)
                    )
                ):
                    b.issue_cycle = b.release_cycle = b.retire_cycle = cycle
                    b.retired = True
                    self.last_retire_cycle = max(self.last_retire_cycle, cycle)
                    self._log("sync_release", b)
                else:
                    active.append(b)
                continue
            if b.issue_cycle is None:
                ready = b.available_cycle
                previous = b.previous
                if previous is not None:
                    if previous.retire_cycle is None:
                        active.append(b)
                        continue
                    ready = max(ready, previous.retire_cycle + rule["next_issue_delay"])
                if any((p.pending or p.start_cycle is None for p in b.predecessors)):
                    active.append(b)
                    continue
                for p in b.predecessors:
                    ready = max(ready, p.start_cycle + self.start_feedback[p.op_class])
                if cycle < ready or (
                    has_pending_dispatch and has_pending_dispatch(b.stream_seq)
                ):
                    active.append(b)
                    continue
                b.issue_cycle = cycle
                b.previous = None
                b.predecessors.clear()
                self._log("issue", b)
            if (
                b.release_cycle is None
                and cycle >= b.issue_cycle + rule["release_latency"]
            ):
                if not self._pending(has_pending_prior, b.stream_seq, b.wait_class):
                    b.release_cycle = cycle
                    b.retire_cycle = (
                        cycle + rule["retire_latency"] - rule["release_latency"]
                    )
                    self._log("sync_release", b)
            if (
                b.retire_cycle is not None
                and cycle >= b.retire_cycle
                and (not b.retired)
            ):
                b.retired = True
                self.last_retire_cycle = max(self.last_retire_cycle, b.retire_cycle)
                self._log("retire", b)
            gate_open = (
                b.release_cycle is not None
                and cycle >= b.release_cycle + rule["consumer_delay"]
            )
            if not (b.retired and gate_open):
                active.append(b)
        self.barriers = active

    def blocks(self, inst):
        seq = int(inst.stream_seq)
        cls = inst.op_class
        for b in self.barriers:
            if seq > b.stream_seq and (
                (b.block_class == "ALL" and cls in {"LOAD", "STORE"})
                or cls == b.block_class
            ):
                delay = self.rules.get(b.barrier, {}).get("consumer_delay", 0)
                if b.release_cycle is None or self.cycle < b.release_cycle + delay:
                    return True
        return False
