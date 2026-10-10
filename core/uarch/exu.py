from __future__ import annotations
from typing import List, Optional
from core.params.profile import InstructionProfile

class EXU:

    def __init__(self, uarch, timing):
        self.timing = timing
        self.enable_cross_fu_ii = bool(uarch.get('enable_cross_fu_ii', True))
        self.enable_exq_greedy_balance = bool(uarch.get('enable_exq_greedy_balance', False))
        self.running = {}
        self.issue_ports = int(uarch.get('issue_ports', 2))
        self.last_issue_cycle = {'ALU': [-10 ** 9] * self.issue_ports, 'SFU': [-10 ** 9] * self.issue_ports}
        self.last_op = {'ALU': [None] * self.issue_ports, 'SFU': [None] * self.issue_ports}
        self.last_form = {'ALU': [None] * self.issue_ports, 'SFU': [None] * self.issue_ports}
        self.last_issue_cycle_exu = [-10 ** 9] * self.issue_ports
        self.last_op_exu = [None] * self.issue_ports
        self.last_form_exu = [None] * self.issue_ports
        self.last_profile = {'ALU': [None] * self.issue_ports, 'SFU': [None] * self.issue_ports}
        self.last_profile_exu = [None] * self.issue_ports
        self.exq_rr_ptr = 0
        self.exq_inflight = [0] * self.issue_ports

    def _pick_exu_port(
        self,
        fu_type: str,
        cur_op: str,
        c: int,
        exu_used_this_cycle: List[bool],
        cur_form: Optional[str]=None,
        cur_profile: Optional[InstructionProfile]=None
    ) -> Optional[int]:
        legal_ports = set(self.timing._eligible_exu_ports(cur_op, cur_form, cur_profile))
        if not self.enable_exq_greedy_balance:
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if exu_used_this_cycle[port]:
                    continue
                if self.enable_cross_fu_ii:
                    prev_op = self.last_op_exu[port]
                    prev_form = self.last_form_exu[port]
                    prev_issue = self.last_issue_cycle_exu[port]
                else:
                    prev_op = self.last_op[fu_type][port]
                    prev_form = self.last_form[fu_type][port]
                    prev_issue = self.last_issue_cycle[fu_type][port]
                ii = self.timing._get_ii(
                    prev_op,
                    cur_op,
                    prev_form=prev_form,
                    cur_form=cur_form,
                    prev_profile=self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[fu_type][port],
                    cur_profile=cur_profile
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
                prev_op = self.last_op_exu[port]
                prev_form = self.last_form_exu[port]
                prev_issue = self.last_issue_cycle_exu[port]
            else:
                prev_op = self.last_op[fu_type][port]
                prev_form = self.last_form[fu_type][port]
                prev_issue = self.last_issue_cycle[fu_type][port]
            ii = self.timing._get_ii(
                prev_op,
                cur_op,
                prev_form=prev_form,
                cur_form=cur_form,
                prev_profile=self.last_profile_exu[port] if self.enable_cross_fu_ii else self.last_profile[fu_type][port],
                cur_profile=cur_profile
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
        uop.done_cycle = cycle + self.timing._latency(uop.op, uop.form, uop.profile)
        uop.exu_port = port
        uop.state = 'running'
        self.running[uop.inst_id] = uop
        self.exq_inflight[port] += 1
        if queued or self.enable_cross_fu_ii:
            self.last_issue_cycle_exu[port] = cycle
            self.last_op_exu[port] = uop.op
            self.last_form_exu[port] = uop.form
            self.last_profile_exu[port] = uop.profile
        if queued or not self.enable_cross_fu_ii:
            self.last_issue_cycle[fu_type][port] = cycle
            self.last_op[fu_type][port] = uop.op
            self.last_form[fu_type][port] = uop.form
            self.last_profile[fu_type][port] = uop.profile
