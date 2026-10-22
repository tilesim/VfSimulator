import json
from pathlib import Path

import pytest

from api.program_api import predict_from_program
from api.program_adapter import program_to_canonical
from api.frontend.schema import StorageKind as S
from core.program_ir import VfSimProgram, VfSimValue, VfSimInst, VfSimLoop, VfSimMemoryAccess


def program():
    values={n:VfSimValue(n,S.PREDICATE_REGISTER,'bool') for n in ('all','none','pattern','unused')}
    values.update({n:VfSimValue(n,S.REGISTER,'fp32') for n in ('x','y')})
    values.update({n:VfSimValue(n,S.UB,'fp32',(1024,),storage_object_id=n) for n in ('input','output')})
    store=VfSimInst('VSTUR',['y'],['output'],config={'align_state_id':'u','align_state_operation':'append'},
        memory_accesses=(VfSimMemoryAccess('output','write',span=16,address_state_id='outptr',
            update_mode='post_update',post_update_delta_bytes=64),))
    return VfSimProgram([
        VfSimInst('PSET_B32',[],['all']),VfSimInst('PSET_B32',[],['none'],config={'pattern':'PAT_ALLF'}),
        VfSimInst('PINTLV_B32',['all','none'],['pattern','unused']),
        VfSimLoop(8,[VfSimInst('VLDS',['input'],['x']),
                     VfSimInst('VSQZ',['x','pattern'],['y']),store],name='row'),
        VfSimInst('VSTAR',[],['output'],config={'align_state_id':'u','align_state_operation':'consume'}),
    ],values=values)


def test_compression_reaches_core_and_flush_waits_for_same_align_state(tmp_path):
    p=program()
    result=predict_from_program(p,out_dir=tmp_path)
    assert 0 < result['cycles'] < 2000
    rows=[json.loads(s) for s in (tmp_path/'start_by_cycle.json').read_text().splitlines()]
    rows.sort(key=lambda r:r['stream_seq'])
    compress=[r for r in rows if r['op']=='VSQZ']
    stores=[r for r in rows if r['op']=='VSTUR']
    flush=next(r for r in rows if r['op']=='VSTAR')
    assert len(compress)==len(stores)==8
    for producer,consumer in zip(compress,stores):
        assert consumer['preg_src']==producer['preg_dst']
        assert consumer['cy'] >= producer['cy']+18
    assert flush['cy'] >= max(r['cy'] for r in stores)+1
    vf=program_to_canonical(p)
    loop=next(n for n in vf.context if hasattr(n,'body'))
    access=loop.body[-1].outputs[0].memory_access
    assert access.span==16
    assert access.post_update_delta_bytes.constant==64


def test_compressed_store_cannot_silently_use_full_vector_addressing():
    p=program();p.body[3].body[-1].memory_accesses=()
    with pytest.raises(ValueError,match='selected span'):
        program_to_canonical(p)


def test_compressed_flush_requires_explicit_align_identity():
    p=program();p.body[-1].config={}
    with pytest.raises(ValueError,match='align'):
        program_to_canonical(p)


def test_sprclr_has_no_fake_register_and_orders_compression(tmp_path):
    p = program()
    p.body.insert(0, VfSimInst('SPRCLR', config={'spr':74}))
    predict_from_program(p, out_dir=tmp_path)
    rows = [json.loads(s) for s in (tmp_path/'start_by_cycle.json').read_text().splitlines()]
    clear = next(r for r in rows if r['op'] == 'SPRCLR')
    assert clear['preg_dst'] == []
    assert all(r['cy'] >= clear['cy'] + 8 for r in rows if r['op'] == 'VSQZ')


@pytest.mark.parametrize('spr', [None, 0, 73, 75, True, '74'])
def test_sprclr_rejects_unmeasured_spr(spr):
    p = program()
    p.body.insert(0, VfSimInst('SPRCLR', config={'spr':spr}))
    with pytest.raises(ValueError, match='spr=74'):
        program_to_canonical(p)
