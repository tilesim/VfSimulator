"""Compare complete transpose sequences (including II calibration cases) with verified CAModel dumps."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from api.program_api import predict_from_program
from api.frontend.schema import StorageKind as S
from core.program_ir import VfSimValue,VfSimInst,VfSimLoop,VfSimProgram,VfSimMemoryAccess
from tools.analyze_topk_transpose_probes import events,verify


def transpose_program(rows,cols):
    values={n:VfSimValue(n,S.REGISTER,t) for n,t in [('idx','uint16'),('data','fp16')]}
    values['mask']=VfSimValue('mask',S.PREDICATE_REGISTER,'bool')
    values.update({n:VfSimValue(n,S.SCALAR,t) for n,t in
                   [('zero','int16'),('limit','uint16'),('stride','uint16'),('outer','uint16')]})
    values.update({n:VfSimValue(n,S.UB,'fp16',(16384,),storage_object_id=n) for n in ('input','output')})
    gather=rows>=cols
    stride=cols if gather else rows
    cfg={'index_stride_elements':stride,'active_lanes':128}
    index=[VfSimInst('VCI',['zero'],['idx'],form='int16'),
           VfSimInst('VMINS',['idx','limit','mask'],['idx'],form='uint16'),
           VfSimInst('VMULS',['idx','stride','mask'],['idx'],form='uint16'),
           VfSimInst('VADDS',['idx','outer','mask'],['idx'],form='uint16')]
    if gather:
        body=index+[
            VfSimInst('VGATHER2',['input','idx','mask'],['data'],form='fp16',config=cfg,
                memory_accesses=(VfSimMemoryAccess('input','read',offset='row',span=127*stride+1),)),
            VfSimInst('VSTS',['data','mask'],['output'],form='fp16',
                memory_accesses=(VfSimMemoryAccess('output','write',offset='row*128',span=128),))]
    else:
        body=[VfSimInst('VLDS',['input'],['data'],form='fp16',
                       memory_accesses=(VfSimMemoryAccess('input','read',offset='row*128',span=128),))]+index+[
            VfSimInst('VSCATTER',['data','idx','mask'],['output'],form='fp16',config=cfg,
                memory_accesses=(VfSimMemoryAccess('output','write',offset='row',span=127*stride+1),))]
    return VfSimProgram([VfSimInst('PSET_B16',[],['mask']),
                         VfSimLoop(cols if gather else rows,body,name='row')],dtype='fp16',values=values)


def validate(root):
    results=[]
    for rows,cols in ((64,128),(128,128)):
        name=f'transpose_{rows}x{cols}'
        run=root/'runs'/name
        verify(name,(run/'input0.bin').read_bytes(),(run/'output0.bin').read_bytes())
        start=events(run/'core0.veccore0.instr_popped_log.dump')
        done=events(run/'core0.veccore0.instr_log.dump')
        useful={i for i,(_,op) in start.items() if op.startswith('RV_') and op not in ('RV_SEND','RV_VLOOPv2')}
        camodel=max(done[i][0] for i in useful)-min(start[i][0] for i in useful)
        out=root/'model_validation'/name
        pred=predict_from_program(transpose_program(rows,cols),out_dir=out)
        history=json.loads((out/'sim_history.json').read_text())
        starts=[r['cy'] for r in history if r['event']=='start']
        dones=[r['cy'] for r in history if r['event']=='done']
        span=max(dones)-min(starts)
        result={'case':name,'source_body_span':camodel,'model_body_span':span,
                'relative_error_pct':100*(span/camodel-1),'model_vf_cycles':pred['cycles']}
        results.append(result)
        print(result)
    (root/'model_validation.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    return results

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);validate(p.parse_args().root)
