from __future__ import annotations
from typing import Any, Dict, List, Optional, Set, Tuple

class Scheduler:

    def __init__(self, uarch, registers, shq, exq, exu, timing, trace):
        self.registers = registers
        self.shq = shq
        self.exq = exq
        self.exu = exu
        self.timing = timing
        self.trace = trace
        self.theoretical_limit_mode = bool(uarch.get('theoretical_limit_mode', False))
        self.enforce_same_cycle_src_hazard = bool(uarch.get('enforce_same_cycle_src_hazard', True))
        self.exq_issue_inflight_cap_per_port: int = int(uarch.get('exq_issue_inflight_cap_per_port', 0))
        self.predict_exq_issue_with_cross_fu: bool = bool(uarch.get('predict_exq_issue_with_cross_fu', False))
        self.shq_exq_static_rr: bool = bool(uarch.get('shq_exq_static_rr', False))
        self.issue_ports = int(uarch.get('issue_ports', 2))
        self.exq_depth = int(uarch.get('exq_depth', 26))
        self.exq_recv_delay = int(uarch.get('exq_recv_delay', 1))
        self.shq_to_exq_port_per_cycle = int(uarch.get('shq_to_exq_port_per_cycle', 1))
        self.exq_capacity_counts_inflight = bool(uarch.get('exq_capacity_counts_inflight', False))
        self.shq_exq_dispatch_policy = uarch.get('shq_exq_dispatch_policy', 'fu_round_robin_exu0_reserve')
        self.exu0_reserve_lookahead = uarch.get('exu0_reserve_lookahead', 8)
        self.exu0_reserve_min_count = uarch.get('exu0_reserve_min_count', 1)
        self.shq_exq_rr_ptr: int = 0
        self.shq_exq_rr_ptr_by_fu: Dict[str, int] = {'ALU': 0, 'SFU': 0}

    def _use_fu_round_robin_fifo(self) -> bool:
        policy = str(getattr(self, 'shq_exq_dispatch_policy', 'fu_round_robin_fifo')).lower()
        return policy in ('fu_round_robin_fifo', 'fu_rr_fifo', 'fu_round_robin', 'fu_round_robin_exu0_reserve')

    def _use_exu0_reserve(self) -> bool:
        policy = str(getattr(self, 'shq_exq_dispatch_policy', 'fu_round_robin_fifo')).lower()
        return policy == 'fu_round_robin_exu0_reserve'

    def _dispatch_exu_tag(self, u: Any) -> str:
        profile = getattr(u, 'profile', None)
        if profile is not None:
            return profile.dispatch_exu
        try:
            return str(self.timing._inst_params(u.op, form=u.form).get('dispatch_exu', '')).upper()
        except Exception:
            return ''

    def _exu0_only_pressure_count(self, start_index: int) -> int:
        lookahead = int(getattr(self, 'exu0_reserve_lookahead', 0))
        if lookahead <= 0:
            return 0
        min_count = max(1, int(getattr(self, 'exu0_reserve_min_count', 1)))
        seen_compute = 0
        seen_exu0_only = 0
        for cand in self.shq.SHQ[start_index + 1:]:
            seen_compute += 1
            if self._dispatch_exu_tag(cand) == 'EXU0_ONLY':
                seen_exu0_only += 1
            if seen_compute >= lookahead:
                break
        return seen_exu0_only if seen_exu0_only >= min_count else 0

    def _apply_exu0_reserve(self, index: int, u: Any, legal_ports: Set[int], candidates: List[int]) -> List[int]:
        if not self._use_exu0_reserve():
            return candidates
        if 0 not in candidates:
            return candidates
        if self._dispatch_exu_tag(u) == 'EXU0_ONLY':
            return candidates
        if 0 not in legal_ports or len(legal_ports) <= 1:
            return candidates
        pressure = self._exu0_only_pressure_count(index)
        if pressure <= 0 or len(candidates) <= 1:
            return candidates
        occupancy = [self.exq_occ(port) for port in range(self.issue_ports)]

        def balance_error(port: int) -> float:
            projected = list(occupancy)
            projected[port] += 1
            non_exu0 = projected[1:]
            if not non_exu0:
                return 0.0
            non_exu0_average = sum(non_exu0) / len(non_exu0)
            return abs(non_exu0_average - projected[0] - pressure)
        best_error = min((balance_error(port) for port in candidates))
        return [port for port in candidates if balance_error(port) == best_error]

    def exq_occ(self, port: int) -> int:
        occ = self.exq.occupancy(port)
        if self.exq_capacity_counts_inflight:
            occ += self.exu.exq_inflight[port]
        return occ

    def total_compute_inflight(self) -> int:
        total = sum((int(x) for x in self.exu.exq_inflight))
        for q in self.exq.exq_wait:
            total += len(q['ALU']) + len(q['SFU'])
        return int(total)

    def select_fu_rr_port(self, fu_type: str, candidates: List[int]) -> int:
        if not candidates:
            raise ValueError('select_fu_rr_port() requires at least one candidate')
        ptrs = getattr(self, 'shq_exq_rr_ptr_by_fu', None)
        if ptrs is None:
            self.shq_exq_rr_ptr_by_fu = {'ALU': 0, 'SFU': 0}
            ptrs = self.shq_exq_rr_ptr_by_fu
        ptr = int(ptrs.get(fu_type, 0))
        issue_ports = max(1, int(self.issue_ports))
        candidate_set = set(candidates)
        for off in range(issue_ports):
            port = (ptr + off) % issue_ports
            if port in candidate_set:
                ptrs[fu_type] = (port + 1) % issue_ports
                return port
        chosen = min(candidates)
        ptrs[fu_type] = (chosen + 1) % issue_ports
        return chosen

    def predict_exq_issue_cycle(
        self,
        port: int,
        fu_type: str,
        op: str,
        recv_cycle: int,
        form: Optional[str]=None,
        profile: Any=None
    ) -> int:
        q_fu = self.exq.exq_wait[port][fu_type]
        pred = int(recv_cycle)
        if q_fu:
            prev = q_fu[-1]
            prev_pred = int(getattr(prev, 'exq_pred_issue', recv_cycle))
            ii = self.timing._get_ii(
                prev.op,
                op,
                prev_form=prev.form,
                cur_form=form,
                prev_profile=prev.profile,
                cur_profile=profile
            )
            pred = max(pred, prev_pred + ii)
        else:
            prev_op = self.exu.last_op[fu_type][port]
            prev_form = self.exu.last_form[fu_type][port]
            prev_issue = self.exu.last_issue_cycle[fu_type][port]
            ii = self.timing._get_ii(
                prev_op,
                op,
                prev_form=prev_form,
                cur_form=form,
                prev_profile=self.exu.last_profile[fu_type][port],
                cur_profile=profile
            )
            pred = max(pred, prev_issue + ii)
        if self.predict_exq_issue_with_cross_fu:
            prev_op_exu = self.exu.last_op_exu[port]
            prev_form_exu = self.exu.last_form_exu[port]
            prev_issue_exu = self.exu.last_issue_cycle_exu[port]
            ii_exu = self.timing._get_ii(
                prev_op_exu,
                op,
                prev_form=prev_form_exu,
                cur_form=form,
                prev_profile=self.exu.last_profile_exu[port],
                cur_profile=profile
            )
            pred = max(pred, prev_issue_exu + ii_exu)
        return pred

    def issue_direct_from_shq(
        self,
        cycle: int,
        issued_srcs_this_cycle: Set[str],
        exu_used_this_cycle: List[bool]
    ) -> int:
        issued_ex: List[Any] = []
        ex_count = 0
        for index, u in enumerate(self.shq.SHQ):
            if u.state != 'ready':
                continue
            if ex_count >= self.issue_ports:
                break
            cur_srcs = {ps for ps in u.preg_src if ps is not None}
            if self.enforce_same_cycle_src_hazard and (not self.theoretical_limit_mode) and issued_srcs_this_cycle & cur_srcs:
                continue
            fu_type = self.timing._get_fu_type(u.op, u.form, u.profile)
            chosen_port = self.exu._pick_exu_port(fu_type, u.op, cycle, exu_used_this_cycle, u.form, u.profile)
            if chosen_port is None:
                continue
            self.exu.start(u, chosen_port, fu_type, cycle, queued=False)
            self.registers.on_uop_start(u)
            self.trace._log('start', u)
            self.trace._log_start_simple(u)
            ex_count += 1
            issued_ex.append(u)
            exu_used_this_cycle[chosen_port] = True
            issued_srcs_this_cycle |= cur_srcs
        self.shq.remove(issued_ex)
        return ex_count

    def enqueue_shq_to_exq(self, cycle: int, issued_srcs_this_cycle: Set[str]) -> int:
        issued_shq: List[Any] = []
        shq_to_exq_cnt = [0] * self.issue_ports
        ex_count = 0
        use_fu_rr_fifo = self._use_fu_round_robin_fifo()
        blocked_fu_types: Set[str] = set()
        for index, u in enumerate(self.shq.SHQ):
            fu_type = self.timing._get_fu_type(u.op, u.form, u.profile)
            if use_fu_rr_fifo and fu_type in blocked_fu_types:
                continue
            if u.state != 'ready':
                continue
            if ex_count >= self.issue_ports:
                break
            cur_srcs = {ps for ps in u.preg_src if ps is not None}
            if self.enforce_same_cycle_src_hazard and (not self.theoretical_limit_mode) and issued_srcs_this_cycle & cur_srcs:
                if use_fu_rr_fifo:
                    blocked_fu_types.add(fu_type)
                continue
            legal_ports = set(self.timing._eligible_exu_ports(u.op, u.form, u.profile))
            candidates: List[int] = []
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if shq_to_exq_cnt[port] >= self.shq_to_exq_port_per_cycle:
                    continue
                occ = self.exq_occ(port)
                if occ >= self.exq_depth:
                    continue
                candidates.append(port)
            if not candidates:
                all_ports = set(range(self.issue_ports))
                if use_fu_rr_fifo and all_ports.issubset(legal_ports):
                    blocked_fu_types.add(fu_type)
                continue
            candidates = self._apply_exu0_reserve(index, u, legal_ports, candidates)
            recv_cycle = cycle + self.exq_recv_delay
            if use_fu_rr_fifo:
                chosen_port = self.select_fu_rr_port(fu_type, candidates)
                predicted_issue = self.predict_exq_issue_cycle(chosen_port, fu_type, u.op, recv_cycle, u.form, u.profile)
            elif self.shq_exq_static_rr:
                ordered = sorted(candidates, key=lambda p: ((p - self.shq_exq_rr_ptr) % self.issue_ports, p))
                chosen_port = ordered[0]
                predicted_issue = self.predict_exq_issue_cycle(chosen_port, fu_type, u.op, recv_cycle, u.form, u.profile)
                self.shq_exq_rr_ptr = (chosen_port + 1) % self.issue_ports
            else:
                best = None
                for port in candidates:
                    predicted_issue = self.predict_exq_issue_cycle(port, fu_type, u.op, recv_cycle, u.form, u.profile)
                    occ = self.exq_occ(port)
                    key = (predicted_issue, occ, port)
                    if best is None or key < best[0]:
                        best = (key, port)
                chosen_port = best[1]
                predicted_issue = best[0][0]
            self.exq.enqueue(u, chosen_port, fu_type, recv_cycle, predicted_issue)
            issued_shq.append(u)
            if bool(getattr(u, 'shq_tracked', False)):
                self.shq.schedule_shq_release(cycle, 1)
                u.shq_tracked = False
            shq_to_exq_cnt[chosen_port] += 1
            ex_count += 1
            issued_srcs_this_cycle |= cur_srcs
        self.shq.remove(issued_shq)
        return ex_count

    def issue_exq_to_exu(self, cycle: int, exu_used_this_cycle: List[bool]) -> None:
        for port in range(self.issue_ports):
            if exu_used_this_cycle[port]:
                continue
            q_port = self.exq.exq_wait[port]
            if not q_port['ALU'] and (not q_port['SFU']):
                continue
            prev_op_exu = self.exu.last_op_exu[port]
            prev_form_exu = self.exu.last_form_exu[port]
            prev_issue_exu = self.exu.last_issue_cycle_exu[port]
            best: Optional[Tuple[Tuple[int, int, int], str, Any]] = None
            for fu_type in ('ALU', 'SFU'):
                if not q_port[fu_type]:
                    continue
                cand = q_port[fu_type][0]
                if self.exq_issue_inflight_cap_per_port > 0 and int(self.exu.exq_inflight[port]) >= self.exq_issue_inflight_cap_per_port:
                    continue
                recv_cy = int(getattr(cand, 'exq_recv_cycle', cycle))
                if recv_cy > cycle:
                    continue
                ready_cy = int(self.registers._compute_ready_cycle(cand))
                if ready_cy > cycle:
                    continue
                ii_exu = self.timing._get_ii(
                    prev_op_exu,
                    cand.op,
                    prev_form=prev_form_exu,
                    cur_form=cand.form,
                    prev_profile=self.exu.last_profile_exu[port],
                    cur_profile=cand.profile
                )
                if cycle < prev_issue_exu + ii_exu:
                    continue
                key = (ready_cy, recv_cy, cand.inst_id)
                if best is None or key < best[0]:
                    best = (key, fu_type, cand)
            if best is None:
                continue
            _, fu_type, u = best
            self.exq.pop(port, fu_type)
            self.exu.start(u, port, fu_type, cycle, queued=True)
            self.registers.on_uop_start(u)
            self.trace._log('start', u)
            self.trace._log_start_simple(u)
            exu_used_this_cycle[port] = True
