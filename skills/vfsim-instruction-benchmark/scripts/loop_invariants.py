"""Validate A5 compiler loop-invariant motion with numerical C Model probes."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import struct

import vector_compute as compute


NAMES = ('pset_fixed', 'plt_reset_literal', 'plt_reset_external', 'plt_feedback',
         'plt_induction', 'plt_reset_derived', 'pltm_fixed', 'pltm_induction',
         'vdup_fixed', 'vdup_scalar_variant', 'vdup_mask_variant', 'vdup_vector_variant',
         'invariant_chain', 'nested_outer_variant', 'zero_trip', 'external_preheader',
         'alu_fixed', 'alu_feedback')

EXPECTED = {
    'pset_fixed': {'RV_PSET': 2},  # ALL store mask plus invariant partial mask
    'plt_reset_literal': {'RV_PLT': 1}, 'plt_reset_external': {'RV_PLT': 1},
    'plt_feedback': {'RV_PLT': 4}, 'plt_induction': {'RV_PLT': 4},
    'plt_reset_derived': {'RV_PLT': 1}, 'pltm_fixed': {'RV_PLTM': 1},
    'pltm_induction': {'RV_PLTM': 4}, 'vdup_fixed': {'RV_VDUPS': 1},
    'vdup_scalar_variant': {'RV_VDUPS': 4},
    'vdup_mask_variant': {'RV_PLT': 4, 'RV_VDUPS': 4},
    'vdup_vector_variant': {'RV_VDUP': 4},
    'invariant_chain': {'RV_PLT': 1, 'RV_PNOT': 1, 'RV_VDUPS': 1},
    'nested_outer_variant': {'RV_PLT': 2}, 'zero_trip': {'RV_PLT': 0},
    'external_preheader': {'RV_PLT': 1},
    'alu_fixed': {'RV_VADDS': 1}, 'alu_feedback': {'RV_VADDS': 4},
}


def collect_counts(work, rows):
    """Count popped RV events within the actual VF PC and dynamic ID window."""
    events = [(int(m[2],16),int(m[3]),m[4]) for m in compute.EVENT.finditer(
        (work/'core0.veccore0.instr_popped_log.dump').read_text())]
    for row in rows:
        start,end = [int(pc,16) for pc in row['vf_pc']]
        first,following = row['vf_instruction_id_range']
        counts = Counter(op for pc,identity,op in events if start <= pc < end
                         and (first is None or first <= identity)
                         and (following is None or identity < following))
        row['dynamic_opcode_counts'] = dict(counts)
        row['expected_opcode_counts'] = EXPECTED[row['name']]
        row['motion_verified'] = all(counts[op] == count for op,count in EXPECTED[row['name']].items())
        if not row['motion_verified']:
            raise ValueError(f'Unexpected compiler motion in {work}: {row["name"]}: {counts}')
    return rows


def make_batch(cases):
    stride = 8192
    data = bytearray((i * 29 + 17) % 256 for i in range(stride * len(cases)))
    functions, calls, contracts = [], [], []
    expected = {}
    for number, case in enumerate(cases):
        name, replica = case['name'], case['replica']
        base = number * stride
        count = (161 - 11 * replica if name in ('plt_feedback', 'pltm_induction') else 37 - 7 * replica)
        for i in range(8 * 64):
            data[base + 4*i:base + 4*i+4] = struct.pack('<f', (replica + 1) * 4 + i / 16)
        data[base + 6144:base + 6148] = struct.pack('<I', count)
        body = ['vector_bool all=pset_b32(PAT_ALL),p,q;', 'vector_f32 x,y,z;', 'vbr(z,0.0f);']
        if name == 'plt_feedback':
            body += ['uint32_t n=base_count;']
        if name == 'external_preheader':
            body += ['uint32_t n=base_count;', 'p=plt_b32(n,POST_UPDATE);']
        if name in ('alu_fixed', 'alu_feedback'):
            body += [f'vlds(x,(__ubuf__ float *)input,{base//4},NORM);']
        nested = name == 'nested_outer_variant'
        iterations = 0 if name == 'zero_trip' else 8 if nested else 4
        loop = []
        if nested:
            body += ['#pragma unroll(1)', 'for(uint16_t outer=0;outer<2;++outer){',
                     'uint32_t outer_count=base_count+outer*7;']
            index = '(outer*4+i)'
        else:
            index = 'i'
        body += ['#pragma unroll(1)', f'for(uint16_t i=0;i<{0 if name=="zero_trip" else 4};++i){{']
        if name == 'pset_fixed':
            loop += ['p=pset_b32(PAT_VL32);']
        elif name in ('plt_feedback', 'external_preheader'):
            if name == 'plt_feedback':
                loop += ['p=plt_b32(n,POST_UPDATE);']
        elif name in ('pltm_fixed', 'pltm_induction'):
            loop += [f'p=pltm_b32((uint16_t){"i" if name=="pltm_induction" else "0"},base_count);']
        elif name not in ('vdup_fixed', 'vdup_scalar_variant', 'vdup_vector_variant', 'alu_fixed', 'alu_feedback'):
            expression = ('37' if name == 'plt_reset_literal' else
                          '13+i*7' if name in ('plt_induction', 'vdup_mask_variant') else
                          'base_count+5' if name == 'plt_reset_derived' else
                          'outer_count' if nested else 'base_count')
            loop += [f'uint32_t n={expression};', 'p=plt_b32(n,POST_UPDATE);']
        if name in ('vdup_fixed', 'vdup_scalar_variant', 'vdup_mask_variant', 'invariant_chain'):
            scalar = '2.5f'
            if name == 'invariant_chain':
                loop += ['pnot(q,p,all);']
            mask = 'q' if name == 'invariant_chain' else 'p' if name == 'vdup_mask_variant' else 'all'
            if name == 'vdup_scalar_variant':
                loop += ['vector_u32 varying;vdup(varying,(uint32_t)i+3u,all,MODE_ZEROING);']
            else:
                loop += [f'vdup(y,{scalar},{mask},MODE_ZEROING);']
        elif name in ('alu_fixed', 'alu_feedback'):
            loop += [f'vadds({"y" if name=="alu_fixed" else "x"},x,1.0f,all);']
            if name == 'alu_feedback':
                loop += ['y=x;']
        else:
            loop += [f'vlds(x,(__ubuf__ float *)input,{base//4}+{index}*64,NORM);']
            if name == 'vdup_vector_variant':
                loop += ['vdup(y,x,all,POS_LOWEST,MODE_ZEROING);']
            else:
                loop += ['vsel(y,x,z,p);']
        loop += [f'vsts({"varying,(__ubuf__ uint32_t *)" if name=="vdup_scalar_variant" else "y,(__ubuf__ float *)"}out,{base//4}+{index}*64,NORM_B32,all);']
        has_remaining = name in ('plt_reset_literal','plt_reset_external','plt_feedback',
                                  'plt_induction','plt_reset_derived','nested_outer_variant','zero_trip','external_preheader')
        if has_remaining:
            loop += ['vector_u32 remaining;vbr(remaining,n);',
                     f'vsts(remaining,(__ubuf__ uint32_t *)out,{base//4+512}+{index}*64,NORM_B32,all);']
        body += loop + ['}'] + (['}'] if nested else [])
        # Retain a real VF even if a zero-trip loop is entirely removed.
        body += [f'vlds(x,(__ubuf__ float *)input,{base//4+1792},NORM);',
                 f'vsts(x,(__ubuf__ float *)out,{base//4+1792},NORM_B32,all);']
        function = f'probe_{number}_{name}'
        functions.append(f'__attribute__((always_inline)) inline [aicore] void {function}('
                         '__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,uint32_t base_count){__VEC_SCOPE__{\n'
                         + '\n'.join(body) + '\n}}')
        calls += [f'uint32_t count_{number}=*((__ubuf__ uint32_t *)(input+{base+6144}));',
                  f'{function}(input,out,count_{number});']
        remaining_count = count
        values = []
        for iteration in range(iterations):
            if name == 'pset_fixed':
                active = 32
            elif name == 'plt_feedback':
                active = min(64, remaining_count)
                remaining_count = max(0, remaining_count-64)
            elif name == 'plt_reset_literal':
                active, remaining_count = 37, 0
            elif name in ('plt_induction', 'vdup_mask_variant'):
                active, remaining_count = 13+iteration*7, 0
            elif name == 'plt_reset_derived':
                active, remaining_count = min(64,count+5), max(0,count+5-64)
            elif nested:
                active = min(64,count+(iteration//4)*7)
                remaining_count = max(0,count+(iteration//4)*7-64)
            elif name == 'pltm_induction':
                active = min(64,max(0,count-iteration*64))
            else:
                active, remaining_count = min(64,count), max(0,count-64)
            result = []
            for lane in range(64):
                value = struct.unpack_from('<f',data,base+iteration*256+lane*4)[0]
                if name in ('vdup_fixed','vdup_scalar_variant'):
                    value = 2.5 + (iteration if name=='vdup_scalar_variant' else 0)
                elif name == 'vdup_mask_variant':
                    value = 2.5 if lane < active else 0
                elif name == 'invariant_chain':
                    value = 0 if lane < active else 2.5
                elif name == 'vdup_vector_variant':
                    value = struct.unpack_from('<f',data,base+iteration*256)[0]
                elif name in ('alu_fixed', 'alu_feedback'):
                    value = struct.unpack_from('<f',data,base+lane*4)[0] + (1 if name=='alu_fixed' else iteration+1)
                else:
                    value = value if lane < active else 0
                result.append(value)
            raw = struct.pack('<64I',*([iteration+3]*64)) if name=='vdup_scalar_variant' else struct.pack('<64f',*result)
            values.append((base+iteration*256,raw))
            if has_remaining:
                values.append((base+2048+iteration*256,struct.pack('<64I',*([remaining_count]*64))))
        expected[number] = values
        contracts.append(dict(case, api='loop_invariants', dtype='fp32', form='fp32',
            output_offset=base, output_bytes=4096, guard_offset=base+4096, guard_bytes=4096,
            exact_bytes=True, target_count=iterations,
            target_opcodes=['RV_PLT','RV_PLTM','RV_PSET','RV_VDUP','RV_VDUPM','RV_VDUPS','RV_VBR','RV_PNOT','RV_VADDS'],
            initial_count=count, source_iterations=iterations))
    golden = bytearray(data)
    for values in expected.values():
        for offset, raw in values:
            golden[offset:offset+len(raw)] = raw
    size = len(data)
    source = '#define __aicore__ [aicore]\n' + '\n'.join(functions) + f'''
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *inputGM,__gm__ uint8_t *outputGM){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0);
 __ubuf__ uint8_t *out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,inputGM,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,inputGM,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {''.join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 set_flag(PIPE_S,PIPE_MTE3,(event_t)1);wait_flag(PIPE_S,PIPE_MTE3,(event_t)1);
 copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
'''
    return source, bytes(data), bytes(golden), contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--cann', type=Path, default=Path(os.environ.get('ACL_PATH',
        '/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1')))
    parser.add_argument('--names', nargs='+', choices=NAMES)
    parser.add_argument('--replicas', type=int, default=2)
    parser.add_argument('--batch-size', type=int, choices=range(1,8), default=6)
    parser.add_argument('--jobs', type=int, choices=(1,2), default=2)
    args = parser.parse_args()
    cases = [dict(name=name,replica=replica) for replica in range(args.replicas) for name in args.names or NAMES]
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'plan.json').write_text(json.dumps(cases,indent=2)+'\n')
    batches = [cases[i:i+args.batch_size] for i in range(0,len(cases),args.batch_size)]
    def run(item):
        index,batch = item;work=args.out/f'batch_{index:03}'
        rows=compute.run_batch(batch,work,args.cann,factory=make_batch,run_timeout=1800)
        if not all(row['passed'] for row in rows):
            raise ValueError(f'Numerical or guard validation failed: {work}')
        collect_counts(work, rows)
        print(json.dumps(dict(batch=index,passed=len(rows),observations=[dict(name=r['name'],replica=r['replica'],opcodes=r['all_opcodes']) for r in rows])),flush=True)
        return rows
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows=[row for batch in pool.map(run,enumerate(batches)) for row in batch]
    result=dict(target='A5 / CANN 9.0.0-beta.1 / dav-c310-vec / Ascend950PR_9599',records=rows)
    (args.out/'analysis.json').write_text(json.dumps(result,indent=2)+'\n')


if __name__ == '__main__':
    main()
