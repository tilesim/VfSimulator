"""Packed predicate loads and the two-output interleave keep predicate identities."""
import json
from pathlib import Path
import pytest
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG as catalog
from api.program_adapter import program_to_canonical
from api.program_api import predict_from_program
from core.param_db import ParamDB
from core.program_ir import VfSimInst as I,VfSimValue as V,VfSimProgram,StorageKind as S


def program(pair=True):
    values={k:V(k,S.PREDICATE_REGISTER,'bool') for k in ('m','full','p0','p1')}
    values.update({k:V(k,S.REGISTER,'fp32') for k in ('a','b','d0','d1')})
    values.update(mask=V('mask',S.UB,'uint8',(16,),storage_object_id='mask'),
        x=V('x',S.UB,'fp32',(64,),storage_object_id='x'),
        y=V('y',S.UB,'fp32',(128,),storage_object_id='y'))
    body=[I('PSET_B16',[],['full']),I('VLDS',['x'],['a']),I('VLDS',['x'],['b']),I('PLDS_US',['mask'],['m'])]
    body.append(I('PINTLV_B16',['m','full'],['p0','p1']) if pair else I('PUNPACK_LOWER',['m'],['p0']))
    body.extend([I('VSEL',['a','b','p0'],['d0']),I('VSTS',['d0','full'],['y'])])
    if pair:body.extend([I('VSEL',['a','b','p1'],['d1']),I('VSTS',['d1','full'],['y'])])
    return VfSimProgram(body,values=values,dtype='fp32')


def test_native_leaf_profiles_and_packed_load_volume():
    root=Path(__file__).resolve().parents[1];db=ParamDB(base_dir=str(root))
    data=json.loads((root/'tests/fixtures/packed_select/calibration.json').read_text())
    assert len(data['cases'])==18 and all(r['golden']['passed'] and r['source_data_counts_match'] for r in data['cases'])
    assert catalog.lookup('PLDS_US').ub_transfer_bytes==16
    for op,p in data['profiles'].items():
        for form,fields in p['forms'].items():assert db.resolve_inst(op,form,fields['dtype']).latency==fields['latency']
    assert data['forwarding_minima']=={'PLDS_US.uint8->VSEL.fp16':6,'PLDS_US.uint8->PUNPACK_LOWER.b8':6,
        'PLDS_US.uint8->PINTLV_B16.b16':6,'PINTLV_B16.b16->VSEL.fp32':8,'PUNPACK_LOWER.b8->VSEL.fp32':8}


@pytest.mark.parametrize('pair',[False,True])
def test_loaded_mask_and_both_interleaved_predicates_are_scheduled(pair,tmp_path):
    p=program(pair);program_to_canonical(p)
    result=predict_from_program(p,out_dir=tmp_path,dump_results=True)
    assert not [w for w in result['raw'].get('instruction_warnings',[]) if w['kind'].startswith('unsupported_isa')]
    trace=[json.loads(line) for line in (tmp_path/'start_by_cycle.json').read_text().splitlines()]
    loaded=next(r for r in trace if r['op']=='PLDS_US')
    assert len(loaded['preg_dst'])==1 and loaded['preg_dst'][0].startswith('pred')
    compute=next(r for r in trace if r['op']==('PINTLV_B16' if pair else 'PUNPACK_LOWER'))
    assert compute['cy']>=loaded['cy']+6
    selects=[r for r in trace if r['op']=='VSEL']
    assert all(r['cy']>=compute['cy']+8 for r in selects)
    if pair:assert selects[0]['preg_src'][-1]!=selects[1]['preg_src'][-1]


def test_predicate_load_cannot_write_vector_register():
    p=program();p.values['m']=V('m',S.REGISTER,'fp32')
    with pytest.raises(ValueError):program_to_canonical(p)


def test_interleave_cannot_drop_its_second_output():
    p=program();p.body[4].dst=['p0']
    with pytest.raises(ValueError):program_to_canonical(p)
