import json
from pathlib import Path
import pytest
from api.program_api import predict_from_program
from api.program_adapter import program_to_canonical
from core.program_ir import VfSimProgram,VfSimInst as I,VfSimLoop as L,VfSimValue as V,StorageKind as S,VfSimMemoryAccess as M
from core.param_db import ParamDB

def scalar_values(*names):return {name:V(name,S.SCALAR,'uint32') for name in names}

def run(program,tmp_path):
    result=predict_from_program(program,out_dir=tmp_path,dump_results=True)
    return result,[json.loads(line) for line in (tmp_path/'start_by_cycle.json').read_text().splitlines()]

def test_scalar_loop_carried_values_wait_for_the_previous_scalar_result(tmp_path):
    program=VfSimProgram([I('SMOV',['one'],['x']),L(4,body=[I('SADD',['x','one'],['x'])],name='i')],
        values=scalar_values('one','x'),config={'scalar_timing':True})
    canonical=program_to_canonical(program)
    assert canonical.context[1].carried_values[0].logical_id=='x'
    assert all(value.storage.value=='Scalar' for value in canonical.values.values())
    result,starts=run(program,tmp_path)
    adds=[row for row in starts if row['op']=='SADD']
    assert len(adds)==4 and result['cycles']==44
    assert all(b['cy']>=a['cy']+2 for a,b in zip(adds,adds[1:]))
    assert all(row['op_class']=='SCALAR' and row['exu_port'] is None and not row['preg_dst'] for row in starts)
    assert adds[1]['src_value_instances'][0]['iteration_path'][0]['iteration']==0

def test_load_waits_for_scalar_address_result_after_loop_exit(tmp_path):
    program=VfSimProgram([I('PSET_B32',[],['p']),I('SMOV',['one'],['address']),
        L(8,body=[I('SADD',['address','one'],['address'])],name='i'),
        I('VLDS',['mem'],['v'],config={'scalar_address_inputs':['address']},memory_accesses=(M('mem','read',span=64),)),
        I('VSTS',['v','p'],['out'],memory_accesses=(M('out','write',span=64),))],
        values={**scalar_values('one','address'),'p':V('p',S.PREDICATE_REGISTER,'bool'),
                'v':V('v',S.REGISTER,'fp32'),'mem':V('mem',S.UB,'fp32',(64,),storage_object_id='a'),
                'out':V('out',S.UB,'fp32',(64,),storage_object_id='b')},config={'scalar_timing':True})
    _,starts=run(program,tmp_path)
    last_add=max(row['cy'] for row in starts if row['op']=='SADD')
    load=next(row for row in starts if row['op']=='VLDS')
    assert load['cy']>=last_add+2
    assert load['src_value_instances'][1]['iteration_path'][0]['iteration']==7

def test_scalar_address_input_cannot_be_a_vector_register():
    program=VfSimProgram([I('VLDS',['mem'],['v'],config={'scalar_address_inputs':['address']})],
        values={'mem':V('mem',S.UB,'fp32',(64,),storage_object_id='mem'),
                'v':V('v',S.REGISTER,'fp32'),'address':V('address',S.REGISTER,'uint32')})
    with pytest.raises(ValueError,match='declared Scalar'):
        program_to_canonical(program)

def test_scalar_profiles_match_the_native_unit_evidence():
    root=Path(__file__).resolve().parents[1];db=ParamDB(base_dir=str(root))
    cases=json.loads((root/'tests/fixtures/vf_scalar/calibration.json').read_text())['cases']
    assert len(cases)==72
    for case in cases:
        assert case['golden']['passed'] and case['max_scalar_issue_per_cycle']==1
        for op,latencies in case['scalar_latencies'].items():
            form='uint32' if op=='SMIN' else 'b16' if op=='SADDI' else 'b16_to_b32' if op=='SZEROEXT' else 'b32'
            profile=db.resolve_inst(op,form,'uint32')
            assert profile.op_class=='SCALAR' and profile.fu_type=='SCALAR'
            assert set(latencies)=={profile.latency}

def test_independent_scalar_operations_issue_once_per_cycle(tmp_path):
    values=scalar_values('a','b',*[f'x{i}' for i in range(8)])
    program=VfSimProgram([I('SADD',['a','b'],[f'x{i}']) for i in range(8)],values=values,config={'scalar_timing':True})
    _,starts=run(program,tmp_path)
    cycles=[row['cy'] for row in starts]
    assert all(b-a==1 for a,b in zip(cycles,cycles[1:]))
    assert all(row['fu_type']=='SCALAR' and row['exu_port'] is None for row in starts)

def test_scalar_arithmetic_rejects_vector_operands():
    program=VfSimProgram([I('SADD',['a','b'],['x'])],values={**scalar_values('b','x'),'a':V('a',S.REGISTER,'uint32')},
                        config={'scalar_timing':True})
    with pytest.raises(ValueError,match='storage'):
        program_to_canonical(program)
