"""Numerically validate isolated RAW pairs missing from VFtest operators."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import operator_pairs as probes
import vector_compute as compute


def physical(key):
    if key.startswith(('VLD.', 'VLDS.')):
        return ['RV_VLD', 'RV_VLDS', 'RV_VLDI']
    if key.startswith('VSTS.'):
        return ['RV_VST', 'RV_VSTS', 'RV_VSTI']
    return probes.physical(key)


def roles(key):
    op = key.split('.', 1)[0]
    if op.startswith('VST') or op == 'VSSTB':
        return ['Vd']
    return ['Vn', 'Vm'] if op.lower() in compute.BINARY or op.startswith('VCMP_') else ['Vn']


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 31 + 17) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        previous, current = case['previous_key'], case['current_key']
        producer, consumer = previous.split('.')[0], current.split('.')[0]
        source_type, result_type = probes.types(previous)
        c_source, c_result = probes.types(current)
        base = number * stride
        source = compute.encode(probes.input_values(source_type, 3, case['replica']), source_type)
        other = compute.encode(probes.input_values('fp32', 23, case['replica']), 'fp32')
        data[base:base + 256], data[base + 256:base + 512] = source, other
        scalar = compute.quantize(0.5 + case['replica'] / 16, 'fp32')
        data[base + 3584:base + 3588] = compute.encode([scalar], 'fp32')
        data[base + 3592:base + 3596] = (1).to_bytes(4, 'little')
        data[base + 3712:base + 3744] = (1).to_bytes(32, 'little')
        data[base + 3744:base + 3776] = (1 | (1 << 4)).to_bytes(32, 'little')
        data[base + 3840:base + 3872] = b'\xff' * 32
        golden[base:base + stride] = data[base:base + stride]
        st = compute.TYPES[source_type]
        rt = compute.TYPES[result_type]
        body = ['vector_bool full=pset_b8(PAT_ALL),late,one,two;',
                f'plds(one,(__ubuf__ uint32_t *)input,{base + 3712},NORM);',
                f'plds(two,(__ubuf__ uint32_t *)input,{base + 3744},NORM);',
                f'vector_{st.vector} a;', f'vector_{rt.vector} result;',
                'vector_f32 b;',
                f'vlds(b,(__ubuf__ float *)input,{(base + 256) // 4},NORM);',
                f'vsts(b,(__ubuf__ float *)input,{(base + 256) // 4},NORM_B32,full);']
        if producer not in ('VLD', 'VLDS', 'VDUPS'):
            body += [f'vlds(a,(__ubuf__ {st.scalar} *)input,{base // st.width},NORM);',
                     f'vsts(a,(__ubuf__ {st.scalar} *)input,{base // st.width},NORM_B{st.width * 8},full);']
        body += ['mem_bar(VV_ALL);',
                 f'plds(late,(__ubuf__ uint32_t *)input,{base + 3840},NORM);']
        if producer in ('VLD','VLDS'):
            body += ['vector_u32 warm=(vector_u32)b;']
            body += ['vor(warm,warm,(vector_u32)b,full,MODE_ZEROING);'] * (80 + case['replica'] % 3)
            body.append(f'vsts((vector_u8)warm,out,{base + 3072},NORM_B8,full);')
            golden[base + 3072:base + 3328] = other
        if producer not in ('VLD', 'VLDS', 'VDUPS'):
            body.append('vor((vector_u32 &)a,(vector_u32)a,(vector_u32)a,late,MODE_ZEROING);')
        if producer == 'VLD':
            body += ['#pragma unroll 1', 'for(int iteration=0;iteration<loops;++iteration){',
                     f'__ubuf__ float *address=(__ubuf__ float *)(input+{base});',
                     'vld(result,address,vag_b32(64),NORM);']
            produced = source
        elif producer == 'VLDS':
            body.append(f'vlds(result,(__ubuf__ float *)input,{base // 4},NORM);')
            produced = source
        else:
            body.append(probes.emit(previous, 'result', 'a', 'b', 'late' if producer == 'VDUPS' else 'full', 'scalar', 0))
            produced = probes.reference(previous, source, other, scalar, 0)
        regions = probes.result_regions(previous, base, produced)
        if producer in ('VLD','VLDS'):
            regions.append(dict(offset=base + 3072,bytes=256,dtype='uint8'))
        golden[base:base + len(produced)] = produced
        if consumer == 'VSTS':
            body.append(f'vsts(result,(__ubuf__ float *)out,{(base + 512) // 4},NORM_B32,full);')
            golden[base + 512:base + 512 + len(produced)] = produced
            regions.append(dict(offset=base + 512, bytes=len(produced), dtype='fp32'))
        elif consumer == 'VSSTB':
            body.append(f'vsstb((vector_bf16 &)result,(__ubuf__ bfloat16_t *)(out+{base + 512}),((2<<16)|1),full);')
            for block in range(8):
                offset = base + 512 + (1 + block * 2) * 32
                golden[offset:offset + 32] = produced[block * 32:(block + 1) * 32]
                regions.append(dict(offset=offset, bytes=32, dtype='uint8'))
        else:
            ct = 'bool' if c_result == 'predicate' else compute.TYPES[c_result].vector
            body.append(f'vector_{ct} consumed;')
            left, right = ('b', 'result') if case['source_role'] == 'Vm' else ('result', 'b')
            body.append(probes.emit(current, 'consumed', left, right, 'full', 'scalar', 0))
            raw = produced + b'\0' * (256 - len(produced))
            raw_a, raw_b = (other, raw) if case['source_role'] == 'Vm' else (raw, other)
            consumed = probes.reference(current, raw_a, raw_b, scalar, 0)
            golden[base + 512:base + 512 + len(consumed)] = consumed
            regions += probes.result_regions(current, base + 512, consumed)
            body.append(probes.store(current, 'consumed', base + 512))
        # Consumer STORE is placed first so its timing has a drained LSU.
        if consumer != 'VSTS':
            body.append(probes.store(previous, 'result', base))
        else:
            golden[base:base + 256] = source
        if producer == 'VLD':
            body.append('}')
        name = f'probe_{number}'
        functions.append(f'__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,float scalar,int loops){{__VEC_SCOPE__{{\n' + '\n'.join(body) + '\n}}')
        calls += [f'float s{number}=*((__ubuf__ float *)(input+{base + 3584}));', f'int loops{number}=*((__ubuf__ int *)(input+{base + 3592}));', f'{name}(input,out,s{number},loops{number});']
        contracts.append(dict(case, api='operator_fwd', dtype='fp32', form='mixed',
                              target_opcodes=sorted(set(physical(previous) + physical(current))),
                              target_count=2, target_kind='memory' if producer.startswith('VLD') or consumer in ('VSTS','VSSTB') else 'compute',
                              output_offset=base, output_bytes=3584, guard_offset=base + 3584,
                              guard_bytes=512, output_regions=regions,
                              record_supporting_compute=True, warm_operands=True))
    size = len(data)
    if size > 65536:
        raise ValueError('Batch exceeds separate input/output UB windows')
    source = '#define __aicore__ [aicore]\n' + '\n'.join(functions) + f'''
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *src,__gm__ uint8_t *dst){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,src,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,src,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {''.join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(dst,out,0,1,{size},0,0,0);
}}
'''
    return source, bytes(data), bytes(golden), contracts


def analyze(work):
    contracts = json.loads((work / 'cases.json').read_text())
    golden, actual = (work / 'golden.bin').read_bytes(), (work / 'output.bin').read_bytes()
    rows = compute.analyze(work, contracts, golden)
    for row in rows:
        candidates = {event['id']:event for event in row['events']}
        edges = [edge for edge in row['forwarding_observations']
                 if edge['producer_id'] in candidates and edge['consumer_id'] in candidates
                 and edge['producer_op'] in physical(row['previous_key'])
                 and edge['consumer_op'] in physical(row['current_key'])
                 and edge['role'] == row['source_role']]
        if row['current_key'].startswith('VSTS.') and edges:
            # VLDS warm-up also has a dependent STORE to the input buffer.
            # The consumer under test is the final STORE to the output buffer.
            select = max if row['previous_key'].startswith('VLDS.') else min
            first = select(edge['consumer_id'] for edge in edges)
            edges = [edge for edge in edges if edge['consumer_id'] == first]
        ids = {edge[field] for edge in edges for field in ('producer_id','consumer_id')}
        row['events'] = [event for event in row['events'] if event['id'] in ids]
        row['target_count_actual'] = len(ids)
        row['single_op_sample'] = len(ids) == 2
        row['selected_forwarding'] = edges
        errors = probes.validate_regions(actual, golden, row)
        row['generic_single_dtype_validation'] = row['passed']
        row['passed'] = not errors
        row['mismatch_offsets'] = errors[:64]
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replicas', type=int, default=4)
    parser.add_argument('--pairs', nargs='+')
    parser.add_argument('--batch-size', type=int, default=8)
    parser.add_argument('--jobs', type=int, choices=[1,2,3], default=2)
    parser.add_argument('--cann', type=Path, default=Path(os.environ.get('ACL_PATH','/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1')))
    args = parser.parse_args()
    missing = [row for row in json.loads(args.inventory.read_text())['forwarding'] if row['resolution'] == 'fallback']
    if args.pairs:
        missing = [row for row in missing if row['producer'] + '::' + row['consumer'] in args.pairs]
    cases = [dict(previous_key=row['producer'], current_key=row['consumer'], kind='fwd',
                  source_role=role, replica=replica)
             for row in missing for role in roles(row['consumer']) for replica in range(args.replicas)]
    batches = [cases[i:i + args.batch_size] for i in range(0,len(cases),args.batch_size)]
    def run(item):
        index, batch = item
        work = args.out / f'batch_{index:03}'
        compute.run_batch(batch,work,args.cann,factory=make_batch)
        rows = analyze(work)
        (work/'validation.json').write_text(json.dumps(rows,indent=2)+'\n')
        result = dict(batch=index,cases=len(rows),passed=sum(row['passed'] and row['single_op_sample'] for row in rows),
                      failures=[dict(previous=row['previous_key'],current=row['current_key'],role=row['source_role'],mismatches=row['mismatch_offsets'],actual=row['target_count_actual'])
                                for row in rows if not row['passed'] or not row['single_op_sample']])
        print(json.dumps(result),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run,enumerate(batches)))
    if any(row['passed'] != row['cases'] for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
