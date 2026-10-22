import json
from pathlib import Path
from core.param_db import ParamDB
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

def test_vtrc_profile_is_grounded_in_three_native_rounding_modes():
    root=Path(__file__).resolve().parents[1]
    evidence=json.loads((root/'tests/fixtures/rounding/calibration.json').read_text())['cases']
    assert len(evidence)==12
    db=ParamDB(base_dir=str(root));profile=db.resolve_inst('VTRC','fp32','fp32')
    for case in evidence:
        assert case['golden']['passed'] and case['golden']['guard_passed']
        assert set(case['compute_latency'])=={profile.latency}
        assert min(case['load_to_compute'])==db.get_forwarding_cycles('VLDS','VTRC','fp32',producer_form='fp32',consumer_form='fp32')
        assert set(case['compute_to_store'])=={db.get_forwarding_cycles('VTRC','VSTS','fp32',producer_form='fp32',consumer_form='fp32')}
    assert DEFAULT_INSTRUCTION_CATALOG.lookup('VTRC').forms=={'fp32'}
