from pathlib import Path
import json
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
ROOT=Path(__file__).resolve().parents[1]

def test_arg_profiles_have_two_independent_output_goldens_and_manual_counts():
    d=json.loads((ROOT/'tests/fixtures/arg_reduce/calibration.json').read_text())
    assert len(d['cases'])==18
    assert all(r['state']=='native_verified' and r['golden']['value_guard_passed'] and r['golden']['index_guard_passed'] and r['manual_counts_match'] for r in d['cases'])
    isa=json.loads((ROOT/'configs/isa.json').read_text())['instructions']
    for op,f in d['forms'].items():assert isa[op]['forms'][f['form']]['latency']==f['latency']

def test_onepoint_s32_store_keeps_four_byte_transfer():
    spec=DEFAULT_INSTRUCTION_CATALOG.lookup('VSTS').memory_modes['ONEPT_B32']
    assert 'int32' in spec.forms and spec.memory_span==1 and spec.ub_transfer_bytes==4
