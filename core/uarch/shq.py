from __future__ import annotations
from typing import Dict

class SHQ:

    def __init__(self, uarch):
        self.theoretical_limit_mode = bool(uarch.get('theoretical_limit_mode', False))
        self.enable_shq_credit_model: bool = bool(uarch.get('enable_shq_credit_model', False))
        self.shq_release_delay: int = int(uarch.get('shq_release_delay', 1))
        self.idu_visible_shq_delay: int = int(uarch.get('idu_visible_shq_delay', 0))
        self.shq_depth = int(uarch.get('shq_depth', 58))
        self.enable_credit_visibility_delay = bool(uarch.get('enable_credit_visibility_delay', True))
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
