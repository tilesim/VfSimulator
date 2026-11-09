from __future__ import annotations
from vfsimulator.core.execution.instruction import Uop
from vfsimulator.core.params.types import UarchParameters
from collections import deque

class EXQ:
    """Per-port resource queues sharing admission and physical issue bandwidth."""

    def __init__(self, uarch: UarchParameters, db, registers, exu):
        self.db = db
        self.registers = registers
        self.exu = exu
        self.issue_ports = uarch.issue_ports
        self.depth = uarch.exq_depth
        self.capacity_counts_inflight = uarch.exq_capacity_counts_inflight
        self.predict_issue_with_cross_fu = uarch.predict_exq_issue_with_cross_fu
        self.exq_wait = [{name: deque() for name in (*db.execution_resources, 'ALU', 'SFU')}
                         for _ in range(self.issue_ports)]

    @staticmethod
    def queue_name(profile):
        return profile.execution_resource or profile.fu_type

    def enqueue(self, uop, port, recv_cycle, predicted_issue):
        uop.exu_port = port
        uop.exq_recv_cycle = recv_cycle
        uop.exq_pred_issue = predicted_issue
        uop.state = 'exq_wait'
        self.exq_wait[port][self.queue_name(uop.profile)].append(uop)

    def pop(self, port, fu_type):
        return self.exq_wait[port][fu_type].popleft()

    def occupancy(self, port):
        return sum(len(queue) for queue in self.exq_wait[port].values())

    def admission_occupancy(self, port: int) -> int:
        occupied = self.occupancy(port)
        if self.capacity_counts_inflight:
            occupied += self.exu.exq_inflight[port]
        return occupied

    def can_accept(self, port: int) -> bool:
        return self.admission_occupancy(port) < self.depth

    def predict_issue_cycle(self, port: int, uop: Uop, recv_cycle: int) -> int:
        profile = uop.profile
        queue = self.exq_wait[port][self.queue_name(profile)]
        history_fu = None if profile.execution_resource else profile.fu_type
        predicted = max(int(recv_cycle), self.exu.next_issue_cycle(uop, port, fu_type=history_fu))
        if queue:
            previous = queue[-1]
            previous_prediction = int(getattr(previous, 'exq_pred_issue', recv_cycle))
            ii = self.db.get_ii_for_profiles(previous.profile, profile)
            predicted = max(predicted, previous_prediction + ii,
                            self.exu.next_resource_issue_cycle(profile, port))
            for pending in reversed(queue):
                if (pending.profile.op == profile.op and
                        pending.profile.requested_form == profile.requested_form):
                    predicted = max(predicted, int(pending.exq_pred_issue) +
                                    self.db.get_ii_for_profiles(pending.profile, profile))
                    break
        target = f'{profile.op}.{profile.resolved_form}'
        for pending_queue in self.exq_wait[port].values():
            for pending in pending_queue:
                gap = self.db.issue_overrides_for_profile(pending.profile).get(target)
                if gap is not None:
                    predicted = max(predicted, int(pending.exq_pred_issue)+gap)
        if self.predict_issue_with_cross_fu:
            predicted = max(predicted, self.exu.next_issue_cycle(uop, port))
        return self.exu.next_completion_issue_cycle(uop, port, predicted)

    def pop_ready(self, port: int, cycle: int):
        best = None
        for queue_name, queue in self.exq_wait[port].items():
            if not queue:
                continue
            candidate = queue[0]
            if not self.exu.can_accept(port, queued=True):
                continue
            received = int(getattr(candidate, 'exq_recv_cycle', cycle))
            if received > cycle:
                continue
            ready = int(self.registers.ready_cycle(candidate))
            if ready > cycle:
                continue
            if not self.exu.can_issue(candidate, port, cycle, queued=True):
                continue
            key = (ready, received, candidate.inst_id)
            if best is None or key < best[0]:
                best = (key, queue_name)
        if best is None:
            return None
        _, queue_name = best
        uop = self.pop(port, queue_name)
        return uop.profile.fu_type, uop
