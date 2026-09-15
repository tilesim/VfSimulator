from __future__ import annotations

import json
from pathlib import Path

from vfsimulator.api.program_adapter import program_to_canonical
from vfsimulator.api.frontend.serialization import canonical_vf_info_to_dict
from vfsimulator.api.simulator_costmodel import CoreVfCostModel
from vfsimulator.core.program_ir import VfSimProgram


def predict_from_program(
    program: VfSimProgram, *, config_root: str | Path | None = None,
    out_dir: str | Path = 'results/program_api', model: str = 'mainline',
    dump_trace_path: str | Path | None = None,
):
    """Predict through CanonicalVfInfo and the master Python core."""
    if model != 'mainline':
        raise ValueError(f'Only the master mainline model is supported, got {model!r}')
    canonical = program_to_canonical(program)
    if dump_trace_path is not None:
        path = Path(dump_trace_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(canonical_vf_info_to_dict(canonical), indent=2), encoding='utf-8')
    base_dir = Path(config_root) if config_root is not None else Path(__file__).resolve().parents[1]
    result = CoreVfCostModel(base_dir=base_dir, out_dir=out_dir, dtype=program.dtype).run_vf_info(canonical)
    return {'cycles': int(result['vf_end_cycle']), 'model': model, 'raw': result,
            'trace_path': str(dump_trace_path) if dump_trace_path is not None else None}
