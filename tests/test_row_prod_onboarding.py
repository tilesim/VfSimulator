import json
from pathlib import Path
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

ROOT=Path(__file__).resolve().parents[1]

def test_fp16_interleave_has_independently_measured_profile():
    f=json.loads((ROOT/'tests/fixtures/row_prod/calibration.json').read_text())
    assert len(f['cases'])==24
    assert all(c['golden']['passed'] and c['golden']['guard_passed'] and c['manual_counts_match'] for c in f['cases'])
    assert set(f['VINTLV_fp16_latency_histogram'])=={'11'}
    profile=json.loads((ROOT/'configs/isa.json').read_text())['instructions']['VINTLV']['forms']['fp16']
    assert profile['latency']==11 and profile['dtype']=='fp16'

def test_fp16_interleave_reuses_pair_signature_with_fp16_form():
    spec=DEFAULT_INSTRUCTION_CATALOG.lookup('VINTLV')
    assert 'fp16' in spec.forms
    assert len([p for p in spec.operands if p.direction.value=='output'])==2

def test_fp16_interleave_consumer_forwarding_comes_from_native_minimum():
    f=json.loads((ROOT/'tests/fixtures/row_prod/calibration.json').read_text())
    forwarding=json.loads((ROOT/'configs/forwarding.json').read_text())['forwarding']
    assert forwarding['VINTLV.fp16']['VMUL.fp16']==f['VINTLV_to_VMUL_minimum_issue_gap']==9
