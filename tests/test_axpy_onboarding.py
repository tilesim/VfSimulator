"""AXPY is an accumulator instruction; dropping old dst must change readiness."""
import json
from pathlib import Path
import pytest
from api.program_api import predict_from_program
from api.program_adapter import program_to_canonical
from core.program_ir import VfSimProgram,VfSimInst as I,VfSimValue as V,StorageKind as S
from core.param_db import ParamDB


def program(dtype,delay_accumulator=False):
    values={name:V(name,S.REGISTER,dtype) for name in ('a','b')}
    values.update(p=V('p',S.PREDICATE_REGISTER,'bool'),alpha=V('alpha',S.SCALAR,dtype),
                  x=V('x',S.UB,dtype,(128,),storage_object_id='x'),
                  y=V('y',S.UB,dtype,(128,),storage_object_id='y'))
    body=[I('PSET_B16' if dtype=='fp16' else 'PSET_B32',[],['p']),I('VLDS',['x'],['a']),I('VLDS',['y'],['b'])]
    if delay_accumulator:body.extend(I('VMUL',['b','b','p'],['b']) for _ in range(8))
    body.extend([I('VAXPY',['b','a','alpha','p'],['b']),I('VSTS',['b','p'],['y'])])
    return VfSimProgram(body,values=values,dtype=dtype)


def test_profiles_match_native_latency_and_forwarding_evidence():
    root=Path(__file__).resolve().parents[1];db=ParamDB(base_dir=str(root))
    fixture=json.loads((root/'tests/fixtures/axpy/calibration.json').read_text())
    assert len(fixture['cases'])==24
    for case in fixture['cases']:
        assert case['golden']['passed'] and case['source_data_counts_match']
        profile=db.resolve_inst('VAXPY',case['dst_dtype'],case['dst_dtype'])
        assert profile.latency==9 and profile.op_class=='COMPUTE'
        assert set(map(int,case['instruction_elapsed_histograms']['RV_VAXPY']))=={profile.latency}
    forwarding=json.loads((root/'configs/forwarding.json').read_text())['forwarding']
    for pair,minimum in fixture['forwarding_minima'].items():
        source,target=pair.split('->')
        assert forwarding[source][target]==minimum


@pytest.mark.parametrize('dtype',['fp16','fp32'])
def test_accumulator_read_waits_for_the_actual_old_destination(dtype,tmp_path):
    starts=[]
    for delayed in (False,True):
        out=tmp_path/str(delayed)
        result=predict_from_program(program(dtype,delayed),out_dir=out,dump_results=True)
        warnings=result['raw'].get('instruction_warnings',[])
        if not delayed:assert not warnings
        # The artificial VMUL chain tests dependency binding, not calibrated
        # VMUL->VAXPY forwarding or initiation intervals.
        assert all(w['kind'] in {'missing_forwarding_pair','missing_ii_pair'} for w in warnings)
        trace=[json.loads(line) for line in (out/'start_by_cycle.json').read_text().splitlines()]
        axpy=next(row for row in trace if row['op']=='VAXPY')
        assert len(axpy['preg_src'])==4 and all(axpy['preg_src'][i] for i in (0,1))
        assert axpy['preg_src'][0]!=axpy['preg_src'][1]
        assert axpy['preg_src'][2] is None and axpy['preg_src'][3].startswith('pred')
        starts.append(axpy['cy'])
        if delayed:
            last=max(row['cy'] for row in trace if row['op']=='VMUL')
            db=ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
            ready_gap=db.get_forwarding_cycles('VMUL','VAXPY',dtype,producer_form=dtype,consumer_form=dtype)
            assert ready_gap>0 and axpy['cy']>=last+ready_gap
    assert starts[1]>starts[0]


@pytest.mark.parametrize('dtype',['fp16','fp32'])
def test_missing_accumulator_input_is_not_accepted_as_ordinary_scalar_math(dtype):
    p=program(dtype);p.body[-2].src=['a','alpha','p']
    with pytest.raises(ValueError):program_to_canonical(p)


def test_axpy_scalar_must_be_typed_scalar():
    p=program('fp32');p.values['alpha']=V('alpha',S.REGISTER,'fp32')
    with pytest.raises(ValueError):program_to_canonical(p)
