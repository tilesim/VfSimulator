"""Validate complete PTO TopK output and classify its native/VF busy intervals."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import struct


def analyze(root):
    source=(root/'input0.bin').read_bytes()
    out=(root/'output0.bin').read_bytes()
    idx=(root/'output1.bin').read_bytes()
    assert len(source)==len(out)==len(idx)==32768
    inputs=struct.unpack('<8192f',source)
    values=struct.unpack_from('<128f',out)
    indices=struct.unpack_from('<128I',idx)
    for row in range(8):
        assert list(values[row*16:row*16+10])==sorted(inputs[row*1024:(row+1)*1024],reverse=True)[:10]
        ids=indices[row*16:row*16+10]
        assert len(set(ids))==10 and all(0<=i<1024 for i in ids)
        assert all(values[row*16+j]==inputs[row*1024+ids[j]] for j in range(10))
    assert out[512:1024]==source[512:1024]
    assert idx[512:1024]==source[512:1024]
    # Bytes beyond the explicit 1024-byte GM transfers were never initialized.
    # They are deliberately excluded from the output-guard claim.
    starts={}
    for line in (root/'core0.veccore0.instr_popped_log.dump').read_text().splitlines():
        m=re.search(r'\[(\d+)\].*?\(ID:\s*(\d+)\)',line)
        if m: starts[int(m[2])]=int(m[1])
    counts=Counter();groups=defaultdict(list);intervals=[]
    for line in (root/'core0.veccore0.instr_log.dump').read_text().splitlines():
        m=re.search(r'\[(\d+)\].*?\(ID:\s*(\d+)\)\s+([A-Za-z0-9_]+)',line)
        if not m:continue
        cycle,ident,op=int(m[1]),int(m[2]),m[3]
        counts[op]+=1
        real=re.search(r'vf_real_execute_time:\s*(\d+)',line)
        if real and op in {'VBS32','VMS4V2','MOV_UB_TO_UB','VF'}:
            busy=int(real[1]);start=cycle-busy
            item={'id':ident,'op':op,'popped':starts[ident],'start':start,'done':cycle,'busy':busy}
            groups[op].append(item);intervals.append((start,cycle))
    assert counts['VBS32']==8 and counts['VMS4V2']==24 and counts['MOV_UB_TO_UB']==8
    assert counts['VF']==26 and counts['RV_VSQZ']==16 and counts['RV_VSTUR']==16 and counts['RV_VSTAR']==2
    intervals.sort();union=0;end=None
    for lo,hi in intervals:
        union+=max(0,hi-max(lo,end if end is not None else lo))
        end=max(hi,end if end is not None else hi)
    summed=sum(b-a for a,b in intervals)
    span=intervals[-1][1]-intervals[0][0]
    payload={'golden':{'passed':True,'logical_values_checked':80,'indices_checked':80,'guard_bytes_checked':1024,
                       'physical_row_stride_elements':16,'logical_k':10},
             'instruction_counts':dict(counts),'vec_busy_union':union,'vec_busy_sum':summed,
             'vec_elapsed_span':span,'overlap_cycles':summed-union,'idle_cycles':span-union,
             'native_and_vf_events':dict(groups),
             'sha256':{name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
                       ('input0.bin','output0.bin','output1.bin','core0.veccore0.instr_log.dump','core0.veccore0.instr_popped_log.dump')}}
    (root/'topk_validation.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k not in ('native_and_vf_events','sha256')},indent=2))
    return payload


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('root',type=Path);analyze(p.parse_args().root)
