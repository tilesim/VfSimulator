import json
from pathlib import Path
import pytest
from api.program_adapter import program_to_canonical
from api.program_api import predict_from_program
from core.program_ir import VfSimProgram,VfSimInst as I,VfSimValue as V,VfSimMemoryAccess as A,StorageKind as S


def program(span=16):
    return VfSimProgram([
        I('PSET_B32',[],['p0']),I('PSET_B32',[],['p1']),
        I('PDINTLV_B8',['p0','p1'],['packed','unused'],form='b8'),
        I('PSTS',['packed'],['out'],form='uint8',config={'catalog_mode':'PK'},
          memory_accesses=(A('out','write',span=span),))],
        values={**{n:V(n,S.PREDICATE_REGISTER,'bool') for n in ('p0','p1','packed','unused')},
                'out':V('out',S.UB,'uint8',(1,16),storage_object_id='out')},dtype='fp32')


def test_packed_store_reads_predicate_bank_and_writes_exact_packet(tmp_path):
    canonical=program_to_canonical(program())
    store=canonical.context[-1]
    assert store.inputs[0].role.value=='predicate'
    result=predict_from_program(program(),out_dir=tmp_path)
    assert result['cycles']>0
    assert not [w for w in result['raw']['instruction_warnings'] if w['kind']=='unsupported_isa_form']


def test_wrong_output_packet_span_is_rejected_before_execution():
    with pytest.raises(ValueError,match='catalog_memory_span_mismatch'):
        program_to_canonical(program(32))


def test_comparison_packet_profiles_have_golden_checked_native_evidence():
    root=Path(__file__).resolve().parents[1]
    records=json.loads((root/'tests/fixtures/compare_packed/calibration.json').read_text())['cases']
    from core.param_db import ParamDB
    db=ParamDB(base_dir=str(root))
    for row in records:
        assert row['golden']['passed'] and row['golden']['guard_passed']
        assert set(row['compare_latency'])=={db.resolve_inst(row['op'],row['dtype'],row['dtype']).latency}
        assert set(row['store_latency'])=={db.resolve_inst('PSTS','uint8','uint8').latency}
        if row['interleave_latency']:
            assert set(row['interleave_latency'])=={db.resolve_inst('PDINTLV_B8','b8','bool').latency}
