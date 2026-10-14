from __future__ import annotations
from core.params.types import InstructionProfile, UarchParameters
from collections import deque

class EXQ:
    """Per-port FU queues with admission, reception and head-selection rules."""

    def __init__(self, uarch: UarchParameters, db, registers, exu):
        self.db = db
        self.registers = registers
        self.exu = exu
        self.issue_ports = uarch.issue_ports
        self.depth = uarch.exq_depth
        self.capacity_counts_inflight = uarch.exq_capacity_counts_inflight
        self.predict_issue_with_cross_fu = uarch.predict_exq_issue_with_cross_fu
        self.exq_wait = [{'ALU': deque(), 'SFU': deque()} for _ in range(self.issue_ports)]

    def enqueue(self, uop, port, fu_type, recv_cycle, predicted_issue):
        uop.exu_port = port
        uop.exq_recv_cycle = recv_cycle
        uop.exq_pred_issue = predicted_issue
        uop.state = 'exq_wait'
        self.exq_wait[port][fu_type].append(uop)

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

    def predict_issue_cycle(self, port: int, profile: InstructionProfile, recv_cycle: int) -> int:
        queue = self.exq_wait[port][profile.fu_type]
        predicted = int(recv_cycle)
        if queue:
            previous = queue[-1]
            previous_prediction = int(getattr(previous, 'exq_pred_issue', recv_cycle))
            ii = self.db.get_ii_for_profiles(previous.profile, profile)
            predicted = max(predicted, previous_prediction + ii)
        else:
            predicted = max(predicted, self.exu.next_issue_cycle(profile, port, fu_type=profile.fu_type))
        if self.predict_issue_with_cross_fu:
            predicted = max(predicted, self.exu.next_issue_cycle(profile, port))
        return predicted

    def pop_ready(self, port: int, cycle: int):
        best = None
        for fu_type in ('ALU', 'SFU'):
            queue = self.exq_wait[port][fu_type]
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
            if not self.exu.can_issue(candidate.profile, port, cycle, queued=True):
                continue
            key = (ready, received, candidate.inst_id)
            if best is None or key < best[0]:
                best = (key, fu_type)
        if best is None:
            return None
        _, fu_type = best
        return fu_type, self.pop(port, fu_type)
