"""CanonicalVfInfo is the sole simulator input; adapters live in api/."""
from dataclasses import dataclass
from pathlib import Path
from api.common.schema import CanonicalVfInfo
from core.execution.prepare import prepare_program
from core.execution.engine import Engine
from core.params.database import ParamDB
from core.params.uarch import normalize_mainline_uarch, apply_theoretical_limit_overrides

@dataclass
class CoreVfCostModel:
    base_dir: str | Path = Path(__file__).resolve().parents[1]
    out_dir: str | Path | None = 'results/cost_model'
    fallback_dtype: str = 'fp32'
    include_param_cache_stats: bool = False
    three_ports: bool = False
    theoretical_limit_vloop_only: bool = False
    theoretical_limit_direct_issue: bool = False

    def predict_vf_cycles(self, vf_info: CanonicalVfInfo) -> int:
        return self.run_vf_info(vf_info)['vf_end_cycle']

    def run_vf_info(self, vf_info: CanonicalVfInfo) -> dict:
        if not isinstance(vf_info, CanonicalVfInfo):
            raise TypeError('Core accepts only CanonicalVfInfo; use InputAPI to load files or JSON')
        db = ParamDB(base_dir=str(self.base_dir))
        uarch = dict(db.get_uarch())
        uarch.update(vf_info.uarch)
        if self.three_ports:
            uarch.update(three_ports_mode=True, issue_ports=3, load_ports=3, store_ports=1)
        if self.theoretical_limit_vloop_only or self.theoretical_limit_direct_issue:
            uarch.update(theoretical_limit_mode=True, theoretical_limit_vloop_only=True)
            if self.theoretical_limit_direct_issue:
                uarch.update(theoretical_limit_legacy_forwarding=True, theoretical_limit_direct_issue=True)
        uarch = normalize_mainline_uarch(uarch)
        if uarch.get('theoretical_limit_mode'):
            uarch = apply_theoretical_limit_overrides(uarch)
        program = prepare_program(vf_info, db, uarch)
        engine = Engine(program, uarch, db)
        output = Path(self.out_dir) if self.out_dir is not None else None
        if output is not None and (not output.is_absolute()):
            output = Path(self.base_dir) / output
        result = engine.run(output).to_dict()
        result['linear_inst_count'] = program.linear_inst_count
        if self.include_param_cache_stats:
            result['param_cache_stats'] = db.get_profile_cache_stats()
        return result
