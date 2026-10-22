"""INT32 profiles do not borrow FP32 arithmetic timings or drop count registers."""
from pathlib import Path
import json
import pytest
from core.param_db import ParamDB
from core.program_ir import VfSimProgram,VfSimInst as I,VfSimValue as V,StorageKind as S
from api.program_adapter import program_to_canonical
from api.program_api import predict_from_program


def test_native_integer_forms_and_dependency_forwarding():
    root=Path(__file__).resolve().parents[1];db=ParamDB(base_dir=str(root))
    data=json.loads((root/'tests/fixtures/integer_shift/calibration.json').read_text())
    assert len(data['cases'])==42 and all(c['golden']['passed'] and c['source_data_counts_match'] for c in data['cases'])
    assert data['forms']['VSUB']['latency']==7
    for op,form in data['forms'].items():assert db.resolve_inst(op,'int32','int32').latency==form['latency']
    for pair,minimum in data['forwarding_minima'].items():
        source,target=pair.split('->');a,af=source.split('.');b,bf=target.split('.')
        assert db.get_forwarding_cycles(a,b,'int32',producer_form=af,consumer_form=bf)==minimum


@pytest.mark.parametrize('op',['VSHL','VSHR'])
def test_variable_shift_reads_both_integer_registers_and_predicate(op,tmp_path):
    values={k:V(k,S.REGISTER,'int32') for k in ('x','count','out')}
    values.update(p=V('p',S.PREDICATE_REGISTER,'bool'),memory=V('memory',S.UB,'int32',(64,),storage_object_id='memory'))
    program=VfSimProgram([I('PSET_B32',[],['p']),I('VLDS',['memory'],['x']),I('VLDS',['memory'],['count']),
        I(op,['x','count','p'],['out']),I('VSTS',['out','p'],['memory'])],values=values,dtype='int32')
    program_to_canonical(program)
    result=predict_from_program(program,out_dir=tmp_path,dump_results=True)
    assert not result['raw'].get('instruction_warnings')
    trace=[json.loads(line) for line in (tmp_path/'start_by_cycle.json').read_text().splitlines()]
    shift=next(r for r in trace if r['op']==op)
    assert shift['preg_src'][0]!=shift['preg_src'][1] and shift['preg_src'][2].startswith('pred')
    assert shift['cy']>=max(r['cy'] for r in trace if r['op']=='VLDS')+6


def test_variable_shift_rejects_scalar_in_place_of_count_register():
    values={k:V(k,S.REGISTER,'int32') for k in ('x','out')}
    values.update(p=V('p',S.PREDICATE_REGISTER,'bool'),count=V('count',S.SCALAR,'int32'))
    program=VfSimProgram([I('VSHL',['x','count','p'],['out'])],values=values,dtype='int32')
    with pytest.raises(ValueError):program_to_canonical(program)
