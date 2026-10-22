import json
from pathlib import Path

from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from core.param_db import ParamDB


def test_lrelu_profiles_match_checked_native_evidence_without_generic_fallback():
    root=Path(__file__).resolve().parents[1]
    evidence=json.loads((root/'tests/fixtures/lrelu/calibration.json').read_text())
    db=ParamDB(base_dir=str(root))
    assert len(evidence['cases'])==6
    assert set(DEFAULT_INSTRUCTION_CATALOG.lookup('VLRELU').forms)=={'fp16','fp32'}
    for case in evidence['cases']:
        assert case['golden']['passed'] and case['golden']['guard_passed']
        dtype=case['dtype']
        profile=db.resolve_inst('VLRELU',dtype,dtype)
        assert set(case['latencies'])=={profile.latency}
        assert set(case['load_to_compute'])=={db.get_forwarding_cycles('VLDS','VLRELU',dtype)}
        assert set(case['compute_to_store'])=={db.get_forwarding_cycles('VLRELU','VSTS',dtype)}
    assert not db.get_warnings()
