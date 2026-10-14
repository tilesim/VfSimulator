from __future__ import annotations
from core.params.types import UarchParameters
from typing import Any, Dict, List, Set

class OoO:
    """Select ready SHQ instructions and assign EXQ ports; no additional instruction queue."""

    def __init__(self, uarch: UarchParameters, registers, shq, exq, exu, trace):
        self.registers = registers
        self.shq = shq
        self.exq = exq
        self.exu = exu
        self.trace = trace
        self.theoretical_limit_mode = uarch.theoretical_limit_mode
        self.enforce_same_cycle_src_hazard = uarch.enforce_same_cycle_src_hazard
        self.shq_exq_static_rr: bool = uarch.shq_exq_static_rr
        self.issue_ports = uarch.issue_ports
        self.exq_recv_delay = uarch.exq_recv_delay
        self.shq_to_exq_port_per_cycle = uarch.shq_to_exq_port_per_cycle
        self.shq_exq_dispatch_policy = uarch.shq_exq_dispatch_policy
        self.exu0_reserve_lookahead = uarch.exu0_reserve_lookahead
        self.exu0_reserve_min_count = uarch.exu0_reserve_min_count
        self.shq_exq_rr_ptr: int = 0
        self.shq_exq_rr_ptr_by_fu: Dict[str, int] = {'ALU': 0, 'SFU': 0}

    def _use_fu_round_robin_fifo(self) -> bool:
        policy = str(getattr(self, 'shq_exq_dispatch_policy', 'fu_round_robin_fifo')).lower()
        return policy in ('fu_round_robin_fifo', 'fu_rr_fifo', 'fu_round_robin', 'fu_round_robin_exu0_reserve')

    def _use_exu0_reserve(self) -> bool:
        policy = str(getattr(self, 'shq_exq_dispatch_policy', 'fu_round_robin_fifo')).lower()
        return policy == 'fu_round_robin_exu0_reserve'

    def _exu0_only_pressure_count(self, start_index: int) -> int:
        lookahead = int(getattr(self, 'exu0_reserve_lookahead', 0))
        if lookahead <= 0:
            return 0
        min_count = max(1, int(getattr(self, 'exu0_reserve_min_count', 1)))
        seen_compute = 0
        seen_exu0_only = 0
        for cand in self.shq.SHQ[start_index + 1:]:
            seen_compute += 1
            if cand.profile.dispatch_exu == 'EXU0_ONLY':
                seen_exu0_only += 1
            if seen_compute >= lookahead:
                break
        return seen_exu0_only if seen_exu0_only >= min_count else 0

    def _apply_exu0_reserve(self, index: int, u: Any, legal_ports: Set[int], candidates: List[int]) -> List[int]:
        if not self._use_exu0_reserve():
            return candidates
        if 0 not in candidates:
            return candidates
        if u.profile.dispatch_exu == 'EXU0_ONLY':
            return candidates
        if 0 not in legal_ports or len(legal_ports) <= 1:
            return candidates
        pressure = self._exu0_only_pressure_count(index)
        if pressure <= 0 or len(candidates) <= 1:
            return candidates
        occupancy = [self.exq.admission_occupancy(port) for port in range(self.issue_ports)]

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
            fu_type = u.profile.fu_type
            chosen_port = self.exu.pick_port(u.profile, cycle, exu_used_this_cycle)
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

    def dispatch(self, cycle: int, issued_srcs_this_cycle: Set[str]) -> int:
        issued_shq: List[Any] = []
        shq_to_exq_cnt = [0] * self.issue_ports
        ex_count = 0
        use_fu_rr_fifo = self._use_fu_round_robin_fifo()
        blocked_fu_types: Set[str] = set()
        for index, u in enumerate(self.shq.SHQ):
            fu_type = u.profile.fu_type
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
            legal_ports = set(u.profile.allowed_exu_ports)
            candidates: List[int] = []
            for port in range(self.issue_ports):
                if port not in legal_ports:
                    continue
                if shq_to_exq_cnt[port] >= self.shq_to_exq_port_per_cycle:
                    continue
                if not self.exq.can_accept(port):
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
                predicted_issue = self.exq.predict_issue_cycle(chosen_port, u.profile, recv_cycle)
            elif self.shq_exq_static_rr:
                ordered = sorted(candidates, key=lambda p: ((p - self.shq_exq_rr_ptr) % self.issue_ports, p))
                chosen_port = ordered[0]
                predicted_issue = self.exq.predict_issue_cycle(chosen_port, u.profile, recv_cycle)
                self.shq_exq_rr_ptr = (chosen_port + 1) % self.issue_ports
            else:
                best = None
                for port in candidates:
                    predicted_issue = self.exq.predict_issue_cycle(port, u.profile, recv_cycle)
                    occ = self.exq.admission_occupancy(port)
                    key = (predicted_issue, occ, port)
                    if best is None or key < best[0]:
                        best = (key, port)
                chosen_port = best[1]
                predicted_issue = best[0][0]
            self.exq.enqueue(u, chosen_port, fu_type, recv_cycle, predicted_issue)
            issued_shq.append(u)
            self.shq.release_credit(u, cycle)
            shq_to_exq_cnt[chosen_port] += 1
            ex_count += 1
            issued_srcs_this_cycle |= cur_srcs
        self.shq.remove(issued_shq)
        return ex_count
