"""Numerical RAW probes for compute, predicate, conversion and memory archive pairs."""
from __future__ import annotations
import argparse,json,os,re
from pathlib import Path
import operator_pairs as ops
import vector_compute as compute
import vector_structural as structural

PREDICATES={'PLT','PAND','PLDS'}
STORES={'VSTS','VSTUS','VSCATTER','PSTS'}
LOADS={'VLDS','PLDS','VGATHER2'}


def types(key):
    op,form=key.split('.',1)
    if op in PREDICATES or op=='PSTS':return 'fp32','predicate'
    return ops.types(key)


def physical(key):
    op=key.split('.',1)[0]
    if op=='VLDS':return ['RV_VLDS','RV_VLDI']
    if op=='PLDS':return ['RV_PLDS','RV_PLDI']
    if op=='VSTS':return ['RV_VSTS','RV_VSTI']
    if op=='VSTUS':return ['RV_VSTUS','RV_VSTUI']
    return ops.physical(key)


def roles(previous,current):
    producer=types(previous)[1]
    op=current.split('.',1)[0]
    if producer=='predicate':
        return ['left_mask','right_mask','mask'] if op=='PAND' else ['predicate'] if op=='PSTS' else ['mask']
    if op=='VAXPY':return ['old','vector']
    if op=='VSCATTER':return ['data','index']
    if op=='VGATHER2':return ['index']
    if op in STORES:return ['data']
    if op.lower() in compute.BINARY or op.startswith('VCMP_') or op in ['VSEL','VEXPDIF']:return ['left','right']
    return ['vector']


def vector(dtype):return 'bool' if dtype=='predicate' else compute.TYPES[dtype].vector

def view(name,dtype):return f'(vector_{vector(dtype)}){name}'


def mask_bytes(seed):
    return sum(1<<i for i in range(256) if i==0 or (i+seed)%7<4).to_bytes(32,'little')


def scalar_input(dtype,slot,replica):
    if dtype in ['int32','uint32']:
        return compute.encode([i for i in range(64)],dtype)
    width=compute.TYPES[dtype].width
    return compute.encode([compute.quantize(0.75 if i%11==0 else 0.25+((i*3+slot*7+replica)%47)/32,dtype) for i in range(256//width)],dtype)


def active(mask,index,width):return bool(int.from_bytes(mask,'little')&(1<<(index*width)))


def reference(key,a,b,mask,scalar=0.5,slot=0):
    op=key.split('.',1)[0];src,dst=types(key)
    if op=='PLT':return sum(1<<(4*i) for i in range(slot)).to_bytes(32,'little')
    if op=='PAND':return (int.from_bytes(a,'little')&int.from_bytes(b,'little')&int.from_bytes(mask,'little')).to_bytes(32,'little')
    if op in ['VCADD','VCMAX']:
        width=compute.TYPES[src].width
        raw=b''.join(a[i:i+width] if active(mask,i//width,width) else compute.encode([0 if op=='VCADD' else float('-inf')],src) for i in range(0,256,width))
        return structural.reduce_result(op.lower(),raw,src,256//width)
    raw=ops.reference(key,a,b,scalar,slot)
    if op=='VMOV' or op=='VCI':return raw
    if dst=='predicate':return (int.from_bytes(raw,'little')&int.from_bytes(mask,'little')).to_bytes(32,'little')
    width=compute.TYPES[dst].width
    if op=='VSEL':return b''.join(a[i:i+width] if active(mask,i//width,width) else b[i:i+width] for i in range(0,256,width))
    return b''.join(raw[i:i+width] if active(mask,i//width,width) else bytes(width) for i in range(0,len(raw),width))


def make_batch(cases):
    stride=4096;data=bytearray((i*29+31)%256 for i in range(stride*len(cases)))
    initial=bytearray(x^0xa5 for x in data);golden=bytearray(initial)
    functions=[];calls=[];contracts=[]
    for number,case in enumerate(cases):
        previous,current=case['previous_key'],case['current_key']
        pop,cop=previous.split('.')[0],current.split('.')[0]
        pt,pr=types(previous);ct,cr=types(current)
        role=case['source_role'];replica=case['replica'];base=number*stride
        a=scalar_input(pt,1,replica);b=scalar_input(pt,2,replica)
        if pop=='VSUB' and pt in compute.FLOATING:
            b=compute.encode([compute.quantize(value/2,pt) for value in compute.decode(a,pt)],pt)
        if pop=='VLN' and pt in compute.FLOATING:
            a=compute.encode([compute.quantize(value+4,pt) for value in compute.decode(a,pt)],pt)
        if pop=='VDUP' and previous.endswith('.b32'):
            a=scalar_input('fp32',1,replica);b=scalar_input('fp32',2,replica)
        ca=scalar_input(ct,3,replica);cb=scalar_input(ct,4,replica)
        if pr=='predicate' and cop=='VDUP' and ct=='uint32':
            ca=compute.encode(list(range(1,65)),'uint32')
        if pr=='predicate' and cop=='VCMAX':
            ca=compute.encode([0.25+i/64 for i in range(64)],'fp32')
        # Gather/scatter indices count elements, and preserve the integer bit view.
        if cop in ['VGATHER2','VSCATTER'] and role=='index' and pop=='VLDS':a=compute.encode(list(range(64)),'uint32')
        pa,pb=mask_bytes(replica),mask_bytes(replica+2)
        source_mask=mask_bytes(replica+4)
        for offset,raw in [(0,a),(256,b),(512,ca),(768,cb),(2048,pa),(2080,pb),(2112,source_mask)]:data[base+offset:base+offset+len(raw)]=raw
        image=compute.encode([compute.quantize(0.25+i/128,'fp32') for i in range(128)],'fp32')
        data[base+1280:base+1792]=image
        data[base+1792:base+2048]=a
        scalar=compute.quantize(0.5+replica/16,'fp32');data[base+3592:base+3596]=compute.encode([scalar],'fp32')
        full=bytes([255])*32
        body=['vector_bool full=pset_b8(PAT_ALL),pa,pb;',f'vector_{vector(pt)} a,b;',f'vector_{vector(ct)} ca,cb;',f'vector_{vector(pr)} produced;',f'vector_{vector(cr)} consumed;']
        if pop in ['VCADD','VCMAX'] or cop in ['VCADD','VCMAX']:
            body += ['vector_bool one=pset_b32(PAT_VL1),two=pset_b32(PAT_VL2);']
        reduction_bytes=8 if pop=='VCMAX' else 4 if pop=='VCADD' else None
        if reduction_bytes and cop=='VSTS':
            width=compute.TYPES[ct].width
            body.append(f'vector_bool reduction_mask=pset_b{width*8}(PAT_VL{reduction_bytes//width});')
        for name,dtype,offset in [('a',pt,0),('b',pt,256),('ca',ct,512),('cb',ct,768)]:
            t=compute.TYPES[dtype]
            body += [f'vlds({name},(__ubuf__ {t.scalar} *)input,{(base+offset)//t.width},NORM);',f'vsts({name},(__ubuf__ {t.scalar} *)input,{(base+offset)//t.width},NORM_B{t.width*8},full);']
        for name,offset in [('pa',2048),('pb',2080)]:body += [f'plds({name},(__ubuf__ uint32_t *)input,{base+offset},NORM);',f'psts({name},(__ubuf__ uint32_t *)input,{base+offset},NORM);']
        if pop=='VAXPY':body.append('produced=a;')
        if cop=='VAXPY' and role!='old':body.append('consumed=ca;')
        if pop=='VGATHER2' or cop in ['VGATHER2','VSCATTER']:
            data[base+2304:base+2560]=compute.encode(list(range(64)),'uint32')
            body += ['vector_u32 indices;',f'vlds(indices,(__ubuf__ uint32_t *)input,{(base+2304)//4},NORM);']
        body += ['mem_bar(VV_ALL);']
        if pop in LOADS or pop in ['PLT','VCI']:
            warm_op=case.get('warm_operation','vor')
            if warm_op=='vadd' and any(k.startswith('VADD.') for k in [previous,current]):warm_op='vsub'
            arithmetic=warm_op in ['vadd','vsub'];warm_type='float' if arithmetic else 'uint32_t'
            body += [f'vector_{"f32" if arithmetic else "u32"} warm,word;',f'vlds(warm,(__ubuf__ {warm_type} *)input,{base//4},NORM);',f'vlds(word,(__ubuf__ {warm_type} *)input,{(base+256)//4},NORM);']
            warm_count=case.get('warm_length',80)+replica%3
            body += [f'{warm_op}(warm,warm,word,full,MODE_ZEROING);']*warm_count
            body += [f'vsts((vector_u8)warm,out,{base+3072},NORM_B8,full);']
            warm_raw=bytes(x|y for x,y in zip(data[base:base+256],data[base+256:base+512]))
            if arithmetic:
                warm_values=compute.decode(data[base:base+256],'fp32');word_values=compute.decode(data[base+256:base+512],'fp32')
                for _ in range(warm_count):warm_values=[compute.quantize(x+y if warm_op=='vadd' else x-y,'fp32') for x,y in zip(warm_values,word_values)]
                warm_raw=compute.encode(warm_values,'fp32')
            golden[base+3072:base+3328]=warm_raw
        else:
            data[base+3840:base+3872]=full
            body += ['vector_bool late;',f'plds(late,(__ubuf__ uint32_t *)input,{base+3840},NORM);']
            if pop not in ['PAND','VDUPS']:
                gate='b' if pop=='VAXPY' else 'a'
                body.append(f'vor((vector_u32 &){gate},(vector_u32){gate},(vector_u32){gate},late,MODE_ZEROING);')
        if pop=='VLDS':
            t=compute.TYPES[pr];body += [f'vlds(produced,(__ubuf__ {t.scalar} *)input,{(base+1792)//t.width},NORM);'];produced=a
        elif pop=='PLDS':
            body += [f'plds(produced,(__ubuf__ uint32_t *)input,{base+2112},NORM);'];produced=source_mask
        elif pop=='VGATHER2':
            t=compute.TYPES[pr];body += [f'vgather2(produced,(__ubuf__ {t.scalar} *)(input+{base+1280}),indices,full);'];produced=image[:256]
        elif pop=='PLT':
            lanes=17+replica%11;body += [f'uint32_t remaining={lanes};produced=plt_b32(remaining,POST_UPDATE);'];produced=reference(previous,a,b,full,scalar,lanes)
        elif pop=='PAND':
            body += ['pand(produced,pa,pb,late);'];produced=reference(previous,pa,pb,full)
        else:
            producer_mask='late' if pop=='VDUPS' else 'full'
            body += [ops.emit(previous,'produced','a','b',producer_mask,'scalar',0)];produced=reference(previous,a,b,full,scalar)
        mask=view('produced','predicate') if pr=='predicate' else 'full';mask_raw=produced if pr=='predicate' else full
        if cop=='VSEL' and pr!='predicate':mask='pa';mask_raw=pa
        if reduction_bytes and cop not in ['VDUP','VMOV']:
            if cop=='VSTS':
                width=compute.TYPES[ct].width;mask='reduction_mask'
                mask_raw=sum(1<<(i*width) for i in range(reduction_bytes//width)).to_bytes(32,'little')
            else:mask='one';mask_raw=(1).to_bytes(32,'little')
        left=view('produced',ct) if role in ['left','vector','old','data'] and pr!='predicate' else 'ca'
        right=view('produced',ct) if role=='right' else 'cb'
        # Undefined reduction lanes are never consumed under the active mask. Use
        # finite nonzero placeholders so the independent scalar evaluator stays
        # defined even for inactive DIV/LN lanes, then zero them with that mask.
        reference_produced=produced
        if reduction_bytes:
            filler=compute.encode([1],ct)
            reference_produced=produced+filler*((256-len(produced))//len(filler))
        left_raw=reference_produced if role in ['left','vector','old','data'] and pr!='predicate' else ca
        right_raw=reference_produced if role=='right' else cb
        consumer_name='consumed';clobbered=False;regions=[]
        def save(name,dtype,offset,raw):
            key=previous if offset==0 else current
            known=4 if key.startswith('VCADD.') else 8 if key.startswith('VCMAX.') else reduction_bytes if offset==1024 and cop=='VMOV' else None
            if known:
                body.append(f'vsts({view(name,"fp32")},(__ubuf__ float *)out,{(base+offset)//4},NORM_B32,{"one" if known==4 else "two"});')
                raw=raw[:known]
            elif dtype=='predicate':body.append(f'psts({name},(__ubuf__ uint32_t *)out,{base+offset},NORM);')
            else:body.append(f'vsts((vector_u8){name},out,{base+offset},NORM_B8,full);')
            golden[base+offset:base+offset+len(raw)]=raw
            if key.startswith('VCMAX.') or known==8:
                regions.extend([dict(offset=base+offset,bytes=4,dtype='fp32'),dict(offset=base+offset+4,bytes=4,dtype='uint32')])
            else:
                regions.append(dict(offset=base+offset,bytes=len(raw),dtype='uint8' if dtype=='predicate' else dtype))
        if cop=='PAND':
            lhs='produced' if role=='left_mask' else 'pa';rhs='produced' if role=='right_mask' else 'pb';pg='produced' if role=='mask' else 'full'
            body += [f'pand(consumed,{lhs},{rhs},{pg});']
            consumed=reference(current,produced if role=='left_mask' else pa,produced if role=='right_mask' else pb,produced if role=='mask' else full)
        elif cop=='PSTS':
            body += [f'psts(produced,(__ubuf__ uint32_t *)out,{base+1024},NORM);'];consumed=produced
            golden[base+1024:base+1056]=consumed;regions.append(dict(offset=base+1024,bytes=32,dtype='uint8'))
        elif cop=='VSTS':
            dtype={'b32':'uint32','b16':'uint16'}.get(current.split('.',1)[1],current.split('.',1)[1]);t=compute.TYPES[dtype]
            payload=view('produced',dtype) if pr!='predicate' else view('ca',dtype);raw=produced if pr!='predicate' else ca
            body += [f'vsts({payload},(__ubuf__ {t.scalar} *)out,{(base+1024)//t.width},NORM_B{t.width*8},{mask});']
            for i in range(256//t.width):
                if active(mask_raw,i,t.width):golden[base+1024+i*t.width:base+1024+(i+1)*t.width]=raw[i*t.width:(i+1)*t.width]
            if reduction_bytes==8:
                regions.extend([dict(offset=base+1024,bytes=4,dtype='fp32'),dict(offset=base+1028,bytes=4,dtype='uint32')])
            else:regions.append(dict(offset=base+1024,bytes=reduction_bytes or 256,dtype=pr if pr in compute.TYPES else dtype))
        elif cop=='VSTUS':
            body += ['vector_align alignment;',f'__ubuf__ float *destination=(__ubuf__ float *)(out+{base+1024});',f'vstus(alignment,64,{view("produced","fp32")},destination);','vstas(alignment,destination,64);']
            golden[base+1024:base+1280]=produced;regions.append(dict(offset=base+1024,bytes=256,dtype='fp32'))
        elif cop in ['VGATHER2','VSCATTER']:
            target_indices=view('produced','uint32') if role=='index' else 'indices'
            index_raw=produced if role=='index' else compute.encode(list(range(64)),'uint32')
            indices=compute.decode(index_raw,'uint32')
            if cop=='VGATHER2':
                body += [f'vgather2(consumed,(__ubuf__ {compute.TYPES[cr].scalar} *)(input+{base+1280}),{target_indices},{mask});']
                consumed=b''.join(image[i*4:(i+1)*4] if active(mask_raw,n,4) else bytes(4) for n,i in enumerate(indices))
            else:
                payload=view('produced','uint32') if role=='data' else view('ca','uint32');raw=produced if role=='data' else ca
                body += [f'vscatter({payload},(__ubuf__ uint32_t *)(out+{base+1024}),{target_indices},{mask});']
                for n,i in enumerate(indices):
                    if i>=128:raise ValueError('Unsafe scatter probe index')
                    if active(mask_raw,n,4):golden[base+1024+i*4:base+1028+i*4]=raw[n*4:(n+1)*4]
                regions.append(dict(offset=base+1024,bytes=512,dtype='uint8'))
        elif cop=='VAXPY':
            if pr=='predicate':lhs=ca;rhs=cb;source='cb'
            elif role=='old':consumer_name='produced';clobbered=True;lhs=produced;rhs=cb;source='cb'
            else:lhs=ca;rhs=produced;source=view('produced','fp32')
            body += [f'vaxpy({consumer_name},{source},1.25f,{mask},MODE_ZEROING);'];consumed=reference(current,lhs,rhs,mask_raw,scalar)
        else:
            body += [ops.emit(current,'consumed',left,right,mask,'scalar',0)];consumed=reference(current,left_raw,right_raw,mask_raw,scalar)
        if cop not in STORES:save(consumer_name,cr,1024,consumed)
        if not clobbered:save('produced',pr,0,produced)
        name=f'probe_{number}'
        functions.append(f'__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,float scalar){{__VEC_SCOPE__{{\n'+'\n'.join(body)+'\n}}')
        calls += [f'float scalar{number}=*((__ubuf__ float *)(input+{base+3592}));',f'{name}(input,out,scalar{number});']
        contracts.append(dict(case,api='membar_fwd',dtype='fp32',form='mixed',target_count=2,target_kind='memory' if pop in LOADS or cop in STORES|LOADS else 'compute',target_opcodes=sorted(set(physical(previous)+physical(current))),output_offset=base,output_bytes=3584,guard_offset=base+3584,guard_bytes=512,output_regions=regions,record_supporting_compute=True,reads_old_destination_opcodes=['RV_VAXPY'],consumer_mask_source='producer' if pr=='predicate' else mask))
    size=len(data)
    if size>28*stride:raise ValueError('Batch exceeds separated UB windows and reserved stack space')
    output_base=0x10000 if size<=65536 else 0x20000
    source='#define __aicore__ [aicore]\n'+'\n'.join(functions)+f'''
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *src,__gm__ uint8_t *dst){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm({output_base});
 copy_gm_to_ubuf_align_v2(input,src,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,src+{size},0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {''.join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(dst,out,0,1,{size},0,0,0);
}}
'''
    return source,bytes(data+initial),bytes(golden),contracts


def copy_errors(actual,data,golden,row):
    """Check bit preservation after independently validating the producer result."""
    base=row['output_offset'];previous,current=row['previous_key'],row['current_key']
    pop,cop=previous.split('.')[0],current.split('.')[0]
    _,pr=types(previous);ct,_=types(current)
    initial=data[len(golden):]
    errors=[]
    if pop in LOADS|{'VMOV','VDUP','VDUPS'}:
        size=32 if pr=='predicate' else 256
        errors.extend(base+i for i in range(size) if actual[base+i]!=golden[base+i])
    if cop=='VSEL':
        mask=actual[base:base+32] if pr=='predicate' else data[base+2048:base+2080] if row.get('consumer_mask_source')=='pa' else bytes([255])*32
        left=actual[base:base+256] if pr!='predicate' and row['source_role']=='left' else data[base+512:base+768]
        right=actual[base:base+256] if pr!='predicate' and row['source_role']=='right' else data[base+768:base+1024]
        width=compute.TYPES[ct].width
        expected=b''.join(left[i:i+width] if active(mask,i//width,width) else right[i:i+width] for i in range(0,256,width))
        errors.extend(base+1024+i for i in range(256) if actual[base+1024+i]!=expected[i])
        return errors
    if cop not in {'VSTS','VSTUS','VMOV','VDUP'}:return errors
    source=data[base+512:base+768] if pr=='predicate' else actual[base:base+256]
    mask=actual[base:base+32] if pr=='predicate' else bytes([255])*32
    size=8 if pop=='VCMAX' else 4 if pop=='VCADD' else 256
    width=compute.TYPES[ct].width
    expected=bytearray(initial[base+1024:base+1280] if cop=='VSTS' else bytes(256))
    if cop in {'VMOV','VSTUS'}:
        expected[:size]=source[:size]
    else:
        for offset in range(0,size if cop=='VSTS' else 256,width):
            if active(mask,offset//width,width):
                start=0 if cop=='VDUP' else offset
                expected[offset:offset+width]=source[start:start+width]
    length=size if cop=='VMOV' else 256
    errors.extend(base+1024+i for i in range(length) if actual[base+1024+i]!=expected[i])
    return errors


def analyze(work,contracts=None):
    if contracts is None:contracts=json.loads((work/'cases.json').read_text())
    golden=(work/'golden.bin').read_bytes();actual=(work/'output.bin').read_bytes()
    data=(work/'input.bin').read_bytes()
    rows=compute.analyze(work,contracts,golden)
    physical_registers={}
    for line in (work/'core0.veccore0.rvec.ISU.dump').read_text().splitlines():
        identity=re.search(r'\[ISU_RECV\].*?instr_id (\d+) ',line)
        if identity and int(identity[1]) not in physical_registers:
            physical_registers[int(identity[1])]={name:{int(p) for p in re.findall(r'p_idx=(\d+)',section)}
                            for name,section in re.findall(r'(SRC_VREGS|DST_VREGS|SRC_PREGS|DST_PREGS):((?:\[v_idx=\d+,p_idx=\d+\])+)',line)}
    for row in rows:
        candidates={event['id']:event for event in row['events']}
        edges=[edge for edge in row['forwarding_observations'] if edge['producer_id'] in candidates and edge['consumer_id'] in candidates and edge['producer_op'] in physical(row['previous_key']) and edge['consumer_op'] in physical(row['current_key'])]
        if edges:
            last=max(e['producer_id'] for e in edges);edges=[e for e in edges if e['producer_id']==last]
            first=min(e['consumer_id'] for e in edges);edges=[e for e in edges if e['consumer_id']==first]
        for edge in edges:
            producer=physical_registers.get(edge['producer_id'],{});consumer=physical_registers.get(edge['consumer_id'],{})
            dependencies=[dict(kind=kind,physical_id=register) for kind in ['VREGS','PREGS']
                                                                    for register in sorted(producer.get('DST_'+kind,set())&consumer.get('SRC_'+kind,set()))]
            edge['physical_dependencies']=dependencies
            edge['dependency_reported_by_dispatch']=bool(dependencies)
        selected={e[k] for e in edges for k in ['producer_id','consumer_id']}
        row['events']=[e for e in row['events'] if e['id'] in selected]
        row['target_count_actual']=len(selected);row['single_op_sample']=len(selected)==2
        row['selected_forwarding']=edges
        row['required_roles']=roles(row['previous_key'],row['current_key'])
        row['source_path_observable']=not (row['current_key'].startswith('VSEL.') and types(row['previous_key'])[1]!='predicate' and row['source_role']=='right' and row.get('consumer_mask_source')!='pa')
        errors=sorted(set(ops.validate_regions(actual,golden,row)+copy_errors(actual,data,golden,row)))
        row['passed']=not errors;row['mismatch_offsets']=errors[:64]
    return rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,required=True);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--batch-size',type=int,choices=range(1,29),default=12)
    parser.add_argument('--run-timeout',type=int,default=300)
    parser.add_argument('--cann',type=Path,default=Path(os.environ.get('ACL_PATH','/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1')))
    args=parser.parse_args();cases=json.loads(args.cases.read_text())
    if any(case.get('kind')!='fwd' for case in cases):parser.error('--cases requires forwarding probe specifications')
    failed=False
    for index,start in enumerate(range(0,len(cases),args.batch_size)):
        work=args.out.resolve()/f'batch_{index:03}';compute.run_batch(cases[start:start+args.batch_size],work,args.cann,factory=make_batch,run_timeout=args.run_timeout)
        rows=analyze(work);(work/'validation.json').write_text(json.dumps(rows,indent=2)+'\n')
        failed |= any(not r['passed'] or not r['single_op_sample'] for r in rows)
        print(json.dumps(dict(batch=index,cases=len(rows),passed=sum(r['passed'] and r['single_op_sample'] for r in rows),failures=[dict(previous=r['previous_key'],current=r['current_key'],role=r['source_role'],errors=r['mismatch_offsets'],count=r['target_count_actual']) for r in rows if not r['passed'] or not r['single_op_sample']])),flush=True)
    if failed:raise SystemExit(1)

if __name__=='__main__':main()
