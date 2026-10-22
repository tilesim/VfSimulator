from __future__ import annotations
from typing import List, Optional
from vfsimulator.core.params.types import InstructionProfile, UarchParameters

class EXU:
    """Execution ports, issue timing and one completion per port per cycle."""

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
        self._self_issue_ready = [{} for _ in range(self.issue_ports)]
        self._completion_cycles = [set() for _ in range(self.issue_ports)]
        self.exq_rr_ptr = 0
        self.exq_inflight = [0] * self.issue_ports
        self.exq_issue_inflight_cap_per_port = uarch.exq_issue_inflight_cap_per_port

    def can_accept(self, port: int, *, queued: bool = False) -> bool:
        if not queued or self.exq_issue_inflight_cap_per_port <= 0:
            return True
        return self.exq_inflight[port] < self.exq_issue_inflight_cap_per_port

    def next_self_issue_cycle(self, profile: InstructionProfile, port: int) -> int:
        return self._self_issue_ready[port].get((profile.op, profile.requested_form), -10 ** 9)

    def next_completion_issue_cycle(self, profile: InstructionProfile, port: int, cycle: int) -> int:
        while cycle + profile.latency in self._completion_cycles[port]:
            cycle += 1
        return cycle

    def next_issue_cycle(self, profile: InstructionProfile, port: int, *,
                         fu_type: str | None = None, not_before: int = -10 ** 9) -> int:
        if fu_type is None:
            previous_cycle = self.last_issue_cycle_exu[port]
            previous_profile = self.last_profile_exu[port]
        else:
            previous_cycle = self.last_issue_cycle[fu_type][port]
            previous_profile = self.last_profile[fu_type][port]
        adjacent_ready = previous_cycle + self.db.get_ii_for_profiles(previous_profile, profile)
        earliest = max(not_before, adjacent_ready, self.next_self_issue_cycle(profile, port))
        return self.next_completion_issue_cycle(profile, port, earliest)

    def can_issue(self, profile: InstructionProfile, port: int, cycle: int, *, queued: bool = False) -> bool:
        if port not in profile.allowed_exu_ports or not self.can_accept(port, queued=queued):
            return False
        fu_type = None if queued or self.enable_cross_fu_ii else profile.fu_type
        return cycle >= self.next_issue_cycle(profile, port, fu_type=fu_type, not_before=cycle)

    def pick_port(self, profile: InstructionProfile, c: int, exu_used_this_cycle: List[bool]) -> Optional[int]:
        legal_ports = set(profile.allowed_exu_ports)
        if not self.enable_exq_greedy_balance:
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if exu_used_this_cycle[port]:
                    continue
                if self.can_issue(profile, port, c):
                    return port
            return None
        candidates = []
        for port in range(self.issue_ports):
            if port not in legal_ports:
                continue
            if exu_used_this_cycle[port]:
                continue
            fu_type = None if self.enable_cross_fu_ii else profile.fu_type
            avail = self.next_issue_cycle(profile, port, fu_type=fu_type, not_before=c)
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
                self._completion_cycles[u.exu_port].remove(u.done_cycle)
                self.exq_inflight[u.exu_port] -= 1
                u.exu_port = None
                u.state = 'done'
                completed.append(u)
                del self.running[key]
        return completed

    def start(self, uop, port, fu_type, cycle, queued=False):
        done_cycle = cycle + uop.profile.latency
        if done_cycle in self._completion_cycles[port]:
            raise RuntimeError(f'EXU {port} completion cycle {done_cycle} is already reserved')
        uop.start_cycle = cycle
        uop.done_cycle = done_cycle
        uop.exu_port = port
        uop.state = 'running'
        self.running[uop.inst_id] = uop
        self._completion_cycles[port].add(done_cycle)
        self.exq_inflight[port] += 1
        # Other opcodes, including those in the same FU class, cannot clear
        # this form's outstanding self-II restriction.
        profile = uop.profile
        self._self_issue_ready[port][(profile.op, profile.requested_form)] = (
            cycle + self.db.get_ii_for_profiles(profile, profile)
        )
        if queued or self.enable_cross_fu_ii:
            self.last_issue_cycle_exu[port] = cycle
            self.last_profile_exu[port] = uop.profile
        if queued or not self.enable_cross_fu_ii:
            self.last_issue_cycle[fu_type][port] = cycle
            self.last_profile[fu_type][port] = uop.profile
