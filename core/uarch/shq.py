from __future__ import annotations
from core.params.types import UarchParameters
from typing import Dict

class SHQ:
    """Compute waiting state and shared credits with delayed release visibility."""

    def __init__(self, uarch: UarchParameters):
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.enable_shq_credit_model: bool = uarch.enable_shq_credit_model
        self.shq_release_delay: int = uarch.shq_release_delay
        self.idu_visible_shq_delay: int = uarch.idu_visible_shq_delay
        self.shq_depth = uarch.shq_depth
        self.enable_credit_visibility_delay = uarch.enable_credit_visibility_delay
        self.shq_used: int = 0
        self.shq_release_events: Dict[int, int] = {}
        self.visible_shq_used: int = 0
        self.visible_shq_release_events: Dict[int, int] = {}
        self.idu_mailbox_shq_release_delta: int = 0
        self.SHQ = []

    def run_shq_release_events(self, cycle: int) -> None:
        if not self.enable_shq_credit_model:
            return
        released = int(self.shq_release_events.pop(cycle, 0))
        if released <= 0:
            return
        self.shq_used = max(0, self.shq_used - released)
        if not self.enable_credit_visibility_delay:
            return
        delay = int(self.idu_visible_shq_delay)
        if delay <= 0:
            self.visible_shq_used = max(0, int(self.visible_shq_used) - int(released))
            self.idu_mailbox_shq_release_delta = int(self.idu_mailbox_shq_release_delta) + int(released)
            return
        visible_cycle = int(cycle) + delay
        self.visible_shq_release_events[visible_cycle] = int(self.visible_shq_release_events.get(visible_cycle, 0)) + int(released)

    def schedule_shq_release(self, cycle: int, count: int=1) -> None:
        if not self.enable_shq_credit_model or count <= 0:
            return
        release_cycle = int(cycle) + int(self.shq_release_delay)
        self.shq_release_events[release_cycle] = int(self.shq_release_events.get(release_cycle, 0)) + int(count)

    def get_free_shq(self) -> int:
        if self.theoretical_limit_mode or not self.enable_shq_credit_model:
            return 10 ** 18
        if self.enable_credit_visibility_delay:
            return max(0, int(self.shq_depth) - int(self.visible_shq_used))
        return max(0, int(self.shq_depth) - int(self.shq_used))

    def get_free_shq_queue(self):
        return 10 ** 18 if self.theoretical_limit_mode else self.shq_depth - len(self.SHQ)

    def update_visibility(self, cycle):
        delta = self.idu_mailbox_shq_release_delta
        self.idu_mailbox_shq_release_delta = 0
        released = self.visible_shq_release_events.pop(cycle, 0)
        self.visible_shq_used = max(0, self.visible_shq_used - released)
        return delta + released

    def update_ready(self, cycle: int, registers) -> None:
        for uop in self.SHQ:
            uop.ready_cycle = registers.ready_cycle(uop)
            uop.state = 'ready' if cycle >= uop.ready_cycle else 'blocked'

    def release_credit(self, uop, cycle: int) -> None:
        if uop.shq_tracked:
            self.schedule_shq_release(cycle, 1)
            uop.shq_tracked = False

    def remove(self, issued):
        ids = {u.inst_id for u in issued}
        self.SHQ[:] = [u for u in self.SHQ if u.inst_id not in ids]

    def enqueue(self, uop, cycle, delay):
        uop.shq_ready_cycle = cycle + max(0, delay)
        self.SHQ.append(uop)

    def reserve(self, uop):
        if self.enable_shq_credit_model and uop.op_class in ('COMPUTE', 'STORE'):
            self.shq_used += 1
            if self.enable_credit_visibility_delay:
                self.visible_shq_used += 1
            uop.shq_tracked = True
