from __future__ import annotations
from typing import List, Optional
from core.params.types import InstructionProfile, UarchParameters

class EXU:

    def __init__(self, uarch: UarchParameters, db):
        self.db = db
        self.enable_cross_fu_ii = uarch.enable_cross_fu_ii
        self.enable_exq_greedy_balance = uarch.enable_exq_greedy_balance
        self.running = {}
        self.issue_ports = uarch.issue_ports
        self.last_issue_cycle = {'ALU': [-10 ** 9] * self.issue_ports, 'SFU': [-10 ** 9] * self.issue_ports}
        self.last_issue_cycle_exu = [-10 ** 9] * self.issue_ports
        self.last_profile = {'ALU': [None] * self.issue_ports, 'SFU': [None] * self.issue_ports}
        self.last_profile_exu = [None] * self.issue_ports
        self.exq_rr_ptr = 0
        self.exq_inflight = [0] * self.issue_ports

    def _pick_exu_port(self, profile: InstructionProfile, c: int, exu_used_this_cycle: List[bool]) -> Optional[int]:
        legal_ports = set(profile.allowed_exu_ports)
        if not self.enable_exq_greedy_balance:
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if exu_used_this_cycle[port]:
                    continue
                if self.enable_cross_fu_ii:
                    prev_issue = self.last_issue_cycle_exu[port]
                else:
                    prev_issue = self.last_issue_cycle[profile.fu_type][port]
                ii = self.db.get_ii_for_profiles(
                    self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[profile.fu_type][port],
                    profile
                )
                if c >= prev_issue + ii:
                    return port
            return None
        candidates = []
        for port in range(self.issue_ports):
            if port not in legal_ports:
                continue
            if exu_used_this_cycle[port]:
                continue
            if self.enable_cross_fu_ii:
                prev_issue = self.last_issue_cycle_exu[port]
            else:
                prev_issue = self.last_issue_cycle[profile.fu_type][port]
            ii = self.db.get_ii_for_profiles(
                self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[profile.fu_type][port],
                profile
            )
            avail = max(c, prev_issue + ii)
            candidates.append((port, avail))
        if not candidates:
            return None
        min_avail = min((av for _, av in candidates))
        if min_avail > c:
            return None
        fast_ports = [p for p, av in candidates if av == min_avail]
        if len(fast_ports) == 1:
            return fast_ports[0]
        min_load = min((self.exq_inflight[p] for p in fast_ports))
        light_ports = [p for p in fast_ports if self.exq_inflight[p] == min_load]
        if len(light_ports) == 1:
            return light_ports[0]
        for off in range(self.issue_ports):
            cand = (self.exq_rr_ptr + off) % self.issue_ports
            if cand in light_ports:
                self.exq_rr_ptr = (cand + 1) % self.issue_ports
                return cand
        return light_ports[0]

    def finish(self, cycle):
        completed = []
        for key, u in list(self.running.items()):
            if cycle >= u.done_cycle:
                self.exq_inflight[u.exu_port] -= 1
                u.exu_port = None
                u.state = 'done'
                completed.append(u)
                del self.running[key]
        return completed

    def start(self, uop, port, fu_type, cycle, queued=False):
        uop.start_cycle = cycle
        uop.done_cycle = cycle + uop.profile.latency
        uop.exu_port = port
        uop.state = 'running'
        self.running[uop.inst_id] = uop
        self.exq_inflight[port] += 1
        if queued or self.enable_cross_fu_ii:
            self.last_issue_cycle_exu[port] = cycle
            self.last_profile_exu[port] = uop.profile
        if queued or not self.enable_cross_fu_ii:
            self.last_issue_cycle[fu_type][port] = cycle
            self.last_profile[fu_type][port] = uop.profile
