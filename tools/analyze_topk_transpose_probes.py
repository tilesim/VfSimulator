"""Independently check CAModel bytes and retain per-dynamic-instruction timing."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct

EVENT = re.compile(r'\[(\d+)\].*?\(ID:\s*(\d+)\)\s+([A-Z][A-Z0-9_]+)\b')

def events(path):
    result = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
            m=EVENT.search(line)
            if m:
                result[int(m[2])] = (int(m[1]),m[3])
    return result

def verify(name, src, actual):
    if len(src)!=32768 or len(actual)!=32768:
        raise ValueError('Expected complete 32768-byte buffers')
    expected=bytearray(src)
    if name.startswith('bitsort_'):
        repeats=int(name.split('_r')[1])
        for block in range(repeats):
            source=struct.unpack_from('<32f',src,block*128)
            pairs=[struct.unpack_from('<fI',actual,block*256+j*8) for j in range(32)]
            indices=[idx for _,idx in pairs]
            if sorted(indices)!=list(range(block*32,(block+1)*32)):
                raise ValueError(f'Invalid sorted index permutation in block {block}')
            values=[v for v,_ in pairs]
            if values!=sorted(values,reverse=True):
                raise ValueError(f'Values are not descending in block {block}')
            if any(v!=source[idx-block*32] for v,idx in pairs):
                raise ValueError('Value/index association failed')
        if actual[repeats*256:]!=src[repeats*256:]:
            raise ValueError('Output guard changed')
        return {'checked_proposals':repeats*32,'checked_guard_bytes':32768-repeats*256}
    if name.startswith('transpose_'):
        rows,cols=map(int,name.split('_')[1].split('x'))
        for r in range(rows):
            for c in range(cols):
                expected[(c*rows+r)*2:(c*rows+r+1)*2]=src[(r*cols+c)*2:(r*cols+c+1)*2]
    elif re.fullmatch(r'(gather|scatter)_s\d+_n\d+',name):
        op,stride,count=re.fullmatch(r'(gather|scatter)_s(\d+)_n(\d+)',name).groups()
        stride,count=int(stride),int(count)
        for lane in range(count):
            inp,out=(lane*stride,lane) if op=='gather' else (lane,lane*stride)
            expected[out*2:out*2+2]=src[inp*2:inp*2+2]
    elif name=='extract_fp32':
        # All lanes selected, eight consecutive 64-element vectors.
        expected[:2048]=src[:2048]
    elif name.startswith('extract_topk_'):
        odd=int(name.rsplit('_',1)[1])
        packed=b''.join(src[(r*64+lane)*4:(r*64+lane+1)*4]
                        for r in range(8) for lane in range(odd,32,2))
        expected[:len(packed)]=packed
    elif name.startswith('mrgsort_'):
        key=name.replace('mrgsort_fenced_','mrgsort_')
        count,repeats={'mrgsort_32_r8':(32,8),'mrgsort_128_r2':(128,2),'mrgsort_10_r1':(10,1)}[key]
        lists=2 if count==10 else 4
        for rank in range(count*repeats*lists):
            expected[rank*8:rank*8+8]=struct.pack('<fI',4096-rank,rank)
    else:
        return {'verified':False,'reason':'No independent golden rule for this probe'}
    if actual!=expected:
        offsets=[i for i,(a,b) in enumerate(zip(actual,expected)) if a!=b]
        raise ValueError(f'{len(offsets)} mismatched bytes; first={offsets[0]}')
    return {'checked_bytes':32768}

def analyze(root):
    rows=[]
    for run in sorted((root/'runs').iterdir()):
        row={'case':run.name,'verified':False}
        source=root/'cases'/f'{run.name}.cce'
        if source.exists():
            row['source_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        binary=run/'output0.bin'
        if binary.exists():
            try:
                check=verify(run.name,(run/'input0.bin').read_bytes(),binary.read_bytes())
                row.update({'verified':check.pop('verified',True),**check})
            except ValueError as e:
                row['error']=str(e)
        else:
            row['error']='No complete output buffer; cannot accept timing calibration'
        starts=events(run/'core0.veccore0.instr_popped_log.dump')
        dones=events(run/'core0.veccore0.instr_log.dump')
        samples=defaultdict(list)
        for ident,(start,op) in starts.items():
            if ident in dones and (op.startswith('RV_') or op in ('VBS32','VMS4','VMS4V2')):
                samples[op].append({'id':ident,'start':start,'done':dones[ident][0],
                                    'elapsed':dones[ident][0]-start})
        row['timing']={op:{'count':len(ss),'elapsed_histogram':dict(Counter(s['elapsed'] for s in ss)),
                           'samples':ss} for op,ss in samples.items()}
        rows.append(row)
    payload={'scope':'CANN 9.2.0-beta.1 A5 CAModel, instruction pop to completion; queued samples are not isolated latency',
             'cases':rows,'verified_cases':sum(r['verified'] for r in rows)}
    (root/'measurements.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    for row in rows:
        print(row['case'],row['verified'],row.get('error',''),
              {k:v['elapsed_histogram'] for k,v in row['timing'].items() if k in ('VBS32','VMS4','RV_VGATHER2','RV_VSCATTER','RV_VSQZ','RV_VSTUR','RV_VSTAR')})
    return payload

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('root',type=Path); analyze(p.parse_args().root)
