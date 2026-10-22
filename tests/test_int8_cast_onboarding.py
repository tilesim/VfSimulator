from pathlib import Path
import json
import pytest
from api.input_symbols import normalize_dtype,compact_dtype
from core.program_ir import VfSimValue,StorageKind
from core.param_db import ParamDB

@pytest.mark.parametrize('alias',['int16','s16'])
def test_signed16_is_a_real_typed_value_not_float_retyping(alias):
    assert str(normalize_dtype(alias))=='int16'
    assert compact_dtype(alias)=='s16'
    assert VfSimValue('x',StorageKind.REGISTER,alias).dtype=='int16'

def test_int8_conversion_profiles_match_native_instruction_evidence():
    root=Path(__file__).resolve().parents[1]
    rows=json.loads((root/'tests/fixtures/int8_cast/calibration.json').read_text())['cases']
    db=ParamDB(base_dir=str(root));assert len(rows)==24
    identities={'F162S16':('VCVT_F16_TO_S16','f16_to_s16'),'S162F16':('VCVT_S16_TO_F16','s16_to_f16'),
                'F162S8':('VCVT_F16_TO_S8','f16_to_s8'),'RV_VAND':('VAND','b16'),'VDUP':('VDUP','int16')}
    for row in rows:
        assert row['golden']['passed'] and row['golden']['guard_passed']
        for leaf,(op,form) in identities.items():
            latency=row['instruction_latencies'][leaf]
            if latency:assert set(latency)=={db.resolve_inst(op,form,'fp16').latency}
        # Packed stores can have observed mask-dependent RMW; intrinsic minimum
        # must not be flattened to the longest queued/partial execution time.
        assert min(row['instruction_latencies']['STORE'])==db.resolve_inst('VSTS','int8','fp16').latency
        assert set(row['instruction_latencies']['STORE'])<={9,13}
        if row['instruction_latencies']['F162S16']:
            for key,value in [('LOAD->F162S16',6),('F162S16->RV_VAND',4),('RV_VAND->S162F16',2),('S162F16->F162S8',4)]:
                assert min(row['forwarding'][key])>=value
        assert min(row['forwarding']['F162S8->STORE'])>=6
    for key,value in [('LOAD->F162S16',6),('F162S16->RV_VAND',4),('RV_VAND->S162F16',2),('S162F16->F162S8',4),('F162S8->STORE',6)]:
        assert min(v for row in rows for v in row['forwarding'].get(key,[]))==value
