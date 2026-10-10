from __future__ import annotations
from collections import deque

class EXQ:

    def __init__(self, uarch):
        self.issue_ports = int(uarch.get('issue_ports', 2))
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
        return sum((len(queue) for queue in self.exq_wait[port].values()))
