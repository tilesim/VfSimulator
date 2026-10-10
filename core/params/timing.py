from __future__ import annotations
from typing import Any, Dict, List, Optional
from core.params.profile import InstructionProfile

class TimingQuery:

    def __init__(self, uarch, pdb, dtype='fp32'):
        self.db = pdb
        self.fallback_dtype = dtype
        self.issue_ports = int(uarch.get('issue_ports', 2))

    def _inst_params(self, op: str, form: Optional[str]=None) -> Dict[str, Any]:
        if hasattr(self.db, 'get_inst_form'):
            return self.db.get_inst_form(op, form=form, dtype=self.fallback_dtype)
        return self.db.get_inst(op, dtype=form or self.fallback_dtype)

    def _get_fu_type(self, op: str, form: Optional[str]=None, profile: Optional[InstructionProfile]=None) -> str:
        if profile is not None:
            return profile.fu_type
        try:
            fu = str(self._inst_params(op, form=form).get('EXU', 'ALU')).upper()
        except Exception:
            fu = 'ALU'
        if fu not in ('ALU', 'SFU'):
            fu = 'ALU'
        return fu

    def _eligible_exu_ports(
        self,
        op: str,
        form: Optional[str]=None,
        profile: Optional[InstructionProfile]=None
    ) -> List[int]:
        """
        Restrict which EXU/EXQ ports an op may use according to isa.json.

        Supported tags:
        - EXU0_ONLY : only port 0
        - EXU01     : port 0 / port 1

        Fallback:
        - missing / unknown tag => all available ports
        """
        try:
            if profile is not None:
                dispatch_exu = profile.dispatch_exu
            else:
                dispatch_exu = str(self._inst_params(op, form=form).get('dispatch_exu', '')).upper()
        except Exception:
            dispatch_exu = ''
        if dispatch_exu == 'EXU0_ONLY':
            return [0] if self.issue_ports > 0 else []
        if dispatch_exu == 'EXU01':
            return [p for p in range(min(self.issue_ports, 2))]
        return list(range(self.issue_ports))

    def _get_ii(
        self,
        prev_op: Optional[str],
        cur_op: str,
        prev_form: Optional[str]=None,
        cur_form: Optional[str]=None,
        prev_profile: Optional[InstructionProfile]=None,
        cur_profile: Optional[InstructionProfile]=None
    ) -> int:
        if prev_op is None:
            return 1
        if prev_profile is not None and cur_profile is not None:
            return int(self.db.get_ii_for_profiles(prev_profile, cur_profile))
        return int(self.db.get_ii(prev_op, cur_op, dtype=self.fallback_dtype, prev_form=prev_form, cur_form=cur_form))

    def _latency(self, op: str, form: Optional[str]=None, profile: Optional[InstructionProfile]=None) -> int:
        return int((profile or self._profile(op, form)).latency)

    def _profile(self, op: str, form: Optional[str]=None) -> InstructionProfile:
        return self.db.resolve_inst(op, form=form, dtype=self.fallback_dtype)
