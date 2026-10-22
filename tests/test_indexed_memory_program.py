import json
from pathlib import Path

import pytest

from api.program_api import predict_from_program
from api.program_adapter import program_to_canonical
from api.frontend.schema import StorageKind as S
from core.program_ir import VfSimProgram, VfSimValue, VfSimInst, VfSimLoop
from core.indexed_memory import indexed_memory_timing


def indexed_program(op='VGATHER2',stride=128,lanes=128,count=2):
    values={'mask':VfSimValue('mask',S.PREDICATE_REGISTER,'bool'),
            'index':VfSimValue('index',S.REGISTER,'uint16'),
            'x':VfSimValue('x',S.REGISTER,'fp16'),
            'zero':VfSimValue('zero',S.SCALAR,'int16'),
            'stride':VfSimValue('stride',S.SCALAR,'uint16')}
    values.update({n:VfSimValue(n,S.UB,'fp16',(16384,),storage_object_id=n) for n in ('input','output')})
    body=[VfSimInst('VCI',['zero'],['index'],form='int16'),
          VfSimInst('VMULS',['index','stride','mask'],['index'],form='uint16')]
    cfg={'index_stride_elements':stride,'active_lanes':lanes}
    if op=='VGATHER2':
        body += [VfSimInst(op,['input','index','mask'],['x'],form='fp16',config=cfg),
                 VfSimInst('VSTS',['x','mask'],['output'])]
    else:
        body += [VfSimInst('VLDS',['input'],['x']),
                 VfSimInst(op,['x','index','mask'],['output'],form='fp16',config=cfg)]
    return VfSimProgram([VfSimInst('PSET_B16',[],['mask']),VfSimLoop(count,body,name='row')],values=values)


@pytest.mark.parametrize('op,stride,interval',[('VGATHER2',128,128),('VSCATTER',64,643)])
def test_real_index_dependencies_and_multicycle_lsu_occupancy(tmp_path,op,stride,interval):
    p=indexed_program(op,stride)
    vf=program_to_canonical(p)
    loop=next(n for n in vf.context if hasattr(n,'body'))
    inst=next(n for n in loop.body if n.opcode==op)
    assert any(vf.values[i.value_id].logical_id=='index' for i in inst.inputs)
    predict_from_program(p,out_dir=tmp_path)
    rows=[json.loads(s) for s in (tmp_path/'start_by_cycle.json').read_text().splitlines()]
    targets=sorted([r for r in rows if r['op']==op],key=lambda r:r['stream_seq'])
    assert len(targets)==2
    assert targets[1]['cy']-targets[0]['cy']>=interval
    for target in targets:
        producer=max((r for r in rows if r['op']=='VMULS' and r['stream_seq']<target['stream_seq']),key=lambda r:r['stream_seq'])
        assert set(producer['preg_dst']).issubset(target['preg_src'])
        assert target['cy']>producer['cy']


def test_missing_or_unmeasured_index_pattern_is_an_error(tmp_path):
    p=indexed_program();p.body[1].body[2].config={}
    with pytest.raises(ValueError,match='index_stride_elements'):
        predict_from_program(p,out_dir=tmp_path)
    with pytest.raises(ValueError,match='Unmeasured'):
        indexed_memory_timing('VGATHER2','fp16',{'index_stride_elements':3,'active_lanes':128})


def test_unmeasured_throughput_is_visible_without_dumping_artifacts(tmp_path):
    p=indexed_program(stride=2,count=1)
    result=predict_from_program(p,out_dir=tmp_path/'not_created',dump_results=False)
    assert any(w['kind']=='unmeasured_indexed_throughput'
               for w in result['raw']['instruction_warnings'])
    assert not (tmp_path/'not_created').exists()


def test_camodel_single_vector_samples_match_measured_patterns():
    fixture=Path(__file__).parent/'fixtures/indexed_memory/measurements.json'
    evidence=json.loads(fixture.read_text())
    for row in evidence['measurements']:
        timing=indexed_memory_timing(row['op'],'fp16',row)
        assert timing.latency==row['cycles']
