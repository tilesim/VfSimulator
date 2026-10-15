"""CanonicalVfInfo is the sole simulator input; adapters live in api/."""
from dataclasses import dataclass, field
from pathlib import Path
from api.common.schema import CanonicalVfInfo
from core.execution.prepare import prepare_program
from core.execution.engine import Engine
from core.params.database import ParamDB
from core.params.loader import load_parameters
from core.params.types import LoadedParameters
from core.params.uarch import resolve_uarch

@dataclass
class CoreVfCostModel:
    base_dir: str | Path = Path(__file__).resolve().parents[1]
    out_dir: str | Path | None = 'results/cost_model'
    fallback_dtype: str = 'fp32'
    theoretical_limit_vloop_only: bool = False
    theoretical_limit_direct_issue: bool = False
    _parameters: LoadedParameters = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        self.base_dir = Path(self.base_dir).resolve()
        self._parameters = load_parameters(self.base_dir)

    def predict_vf_cycles(self, vf_info: CanonicalVfInfo) -> int:
        return self.run_vf_info(vf_info)['vf_end_cycle']

    def run_vf_info(self, vf_info: CanonicalVfInfo) -> dict:
        """Run Canonical input already validated by the API boundary."""
        if not isinstance(vf_info, CanonicalVfInfo):
            raise TypeError('Core accepts only CanonicalVfInfo; use InputAPI to load files or JSON')
        db = ParamDB(base_dir=str(self.base_dir), parameters=self._parameters)
        uarch = resolve_uarch(db.base_uarch, vf_info.uarch,
                              theoretical_vloop_only=self.theoretical_limit_vloop_only,
                              direct_issue=self.theoretical_limit_direct_issue)
        program = prepare_program(vf_info, db, uarch)
        engine = Engine(program, uarch, db, fallback_dtype=self.fallback_dtype)
        output = Path(self.out_dir) if self.out_dir is not None else None
        if output is not None and (not output.is_absolute()):
            output = Path(self.base_dir) / output
        result = engine.run(output).to_dict()
        result['linear_inst_count'] = program.linear_inst_count
        return result
