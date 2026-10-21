"""Numerical mixed-form II probes selected from the VFtest operator inventory."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import re

import instruction_pairs as ordinary
import vector_compute as compute
import vector_conversion as conversion
import vector_structural as structural


def types(key):
    op, form = key.split('.', 1)
    if op.startswith('PSET_') or op in ('PLT', 'PAND'):
        return 'fp32', 'predicate'
    if op == 'VPACK':
        return 'uint32', 'uint16'
    if op in ('VMULSCVT', 'VCVT_F32_TO_F16'):
        return 'fp32', 'fp16'
    if op == 'VCVT_F16_TO_F32':
        return 'fp16', 'fp32'
    dtype = {'b32': 'uint32', 'b16': 'uint16'}.get(form, form)
    return dtype, 'predicate' if op.startswith(('VCMP_', 'VCMPS_')) else dtype


def unmasked(key):
    return key.split('.', 1)[0].startswith('PSET_') or key.split('.', 1)[0] in ('PLT', 'VCI', 'VMOV')


def physical(key):
    op = key.split('.', 1)[0]
    if op.startswith('PSET_'):
        return ['RV_PSET']
    if op.startswith('VCVT_'):
        return ['RV_VCVT_F2F']
    return ordinary.physical(op.lower())


def event_key(event, keys):
    matches = []
    for key in keys:
        if event['op'] not in physical(key):
            continue
        op, form = key.split('.', 1)
        operands = event['operands']
        if op.startswith('PSET_'):
            if f'Dtype: {form.upper()}' not in operands:
                continue
            # Full support masks use ALL=0; target masks use VL1..VL32.
            if re.search(r'#pattern=0(?:,|\s)', operands):
                continue
        elif op == 'VADD' and len([k for k in keys if k.startswith('VADD.')]) > 1:
            if f'Dtype: {"F16" if form == "fp16" else "F32"}' not in operands:
                continue
        elif op.startswith('VCVT_') and len([k for k in keys if k.startswith('VCVT_')]) > 1:
            if 'type: ' + form.replace('_to_', '2').upper() not in operands:
                continue
        elif op == 'VMULS' and len([k for k in keys if k.startswith('VMULS.')]) > 1:
            if f'Dtype: {"U32" if form == "uint32" else "F32"}' not in operands:
                continue
        matches.append(key)
    if len(matches) > 1:
        raise ValueError(f'Ambiguous event {event}: {matches}')
    return matches[0] if matches else None


def input_values(dtype, slot, replica):
    count = 256 // compute.TYPES[dtype].width
    if dtype == 'uint32':
        return [1 + ((lane * 7 + slot * 3 + replica) % 251) for lane in range(count)]
    return [compute.quantize(0.25 + ((lane * 3 + slot * 7 + replica) % 47) / 64, dtype)
            for lane in range(count)]


def emit(key, destination, a, b, mask, scalar, slot):
    op, form = key.split('.', 1)
    if op.startswith('PSET_'):
        pattern = ['PAT_VL1', 'PAT_VL2', 'PAT_VL4', 'PAT_VL8', 'PAT_VL16', 'PAT_VL32'][slot % 6]
        return f'{destination}=pset_{form}({pattern});'
    if op == 'PLT':
        return f'uint32_t count_{destination}={4 * (slot % 6 + 1)};{destination}=plt_b32(count_{destination},POST_UPDATE);'
    if op == 'PAND':
        left, right = [('one', 'full'), ('two', 'full'), ('one', 'two')][slot % 3]
        return f'pand({destination},{left},{right},{mask});'
    if op == 'VMOV':
        return f'vmov({destination},{a});'
    if op == 'VCI':
        return f'vci({destination},(int32_t){slot * 3},INC_ORDER);'
    if op == 'VAXPY':
        return f'vaxpy({destination},{b},1.25f,{mask},MODE_ZEROING);'
    if op.startswith('VCMPS_'):
        return f'{op.lower()}({destination},{a},0.75f,{mask});'
    if op == 'VMULS' and form == 'uint32':
        return f'vmuls({destination},{a},(uint32_t)2,{mask},MODE_ZEROING);'
    if op == 'VDUPS':
        return f'vdup({destination},{scalar},{mask},MODE_ZEROING);'
    if op == 'VDUP':
        return f'vdup({destination},{a},{mask},POS_LOWEST,MODE_ZEROING);'
    if op in ('VCADD', 'VCMAX'):
        return f'{op.lower()}({destination},{a},{mask},MODE_ZEROING);'
    if op == 'VEXPDIF':
        return f'vexpdif({destination},{a},{b},{mask},PART_EVEN);'
    if op == 'VMULSCVT':
        return f'vmulscvt({destination},{a},1.0f,{mask},PART_EVEN);'
    if op.startswith('VCVT_'):
        return conversion.call(dict(api='vcvt', form=form), destination, a, mask)
    if op == 'VPACK':
        return f'vpack({destination},{a},LOWER,MODE_ZEROING);'
    return ordinary.operation(op.lower(), destination, a, b, mask, form)


def reference(key, raw, other, scalar, slot):
    op, form = key.split('.', 1)
    source_type, result_type = types(key)
    if op.startswith('PSET_'):
        active = [1, 2, 4, 8, 16, 32][slot % 6]
        width = int(form[1:]) // 8
        return sum(1 << (i * width) for i in range(active)).to_bytes(32, 'little')
    if op == 'PLT':
        return sum(1 << (4 * i) for i in range(4 * (slot % 6 + 1))).to_bytes(32, 'little')
    if op == 'PAND':
        return (0x11 if slot % 3 == 1 else 1).to_bytes(32, 'little')
    if op == 'VCI':
        return compute.encode([slot * 3 + i for i in range(64)], 'int32')
    if op == 'VMOV':
        return raw
    if op == 'VAXPY':
        return compute.encode([compute.quantize(a + b * 1.25, source_type)
                               for a, b in zip(compute.decode(raw, source_type), compute.decode(other, source_type))], source_type)
    if op == 'VMULS' and form == 'uint32':
        return compute.encode([v * 2 for v in compute.decode(raw, source_type)], source_type)
    if op == 'VDUPS':
        return compute.encode([scalar] * 64, 'fp32')
    if op == 'VDUP':
        width = compute.TYPES[source_type].width
        return raw[:width] * (256 // width)
    if op in ('VCADD', 'VCMAX'):
        return structural.reduce_result(op.lower(), raw, 'fp32', 64)
    if op.startswith('VCVT_') or op == 'VMULSCVT':
        return conversion.reference(raw, dict(api='vcvt', form='f32_to_f16' if op == 'VMULSCVT' else form))
    if op == 'VPACK':
        return conversion.reference(raw, dict(api='vpack', form='u32_to_u16'))
    a, b = compute.decode(raw, source_type), compute.decode(other, source_type)
    if op == 'VEXPDIF':
        return compute.encode([compute.quantize(math.exp(x - y), 'fp32') for x, y in zip(a, b)], 'fp32')
    if op == 'VSEL':
        return raw
    if op.startswith(('VCMP_', 'VCMPS_')):
        condition = op.rsplit('_', 1)[1]
        rhs = [0.75] * len(a) if op.startswith('VCMPS_') else b
        values = [{'EQ': x == y, 'NE': x != y, 'GT': x > y,
                   'GE': x >= y, 'LT': x < y, 'LE': x <= y}[condition]
                  for x, y in zip(a, rhs)]
    else:
        values = ordinary.evaluate(op.lower(), a, b, form)
    if result_type == 'predicate':
        bits = sum(1 << (i * compute.TYPES[source_type].width)
                   for i, active in enumerate(values) if active)
        return bits.to_bytes(32, 'little')
    return compute.encode(values, result_type)


def result_regions(key, offset, raw):
    op = key.split('.', 1)[0]
    result_type = types(key)[1]
    if op == 'VCMAX':
        return [dict(offset=offset, bytes=4, dtype='fp32'),
                dict(offset=offset + 4, bytes=4, dtype='uint32')]
    return [dict(offset=offset, bytes=len(raw), dtype='uint8' if result_type == 'predicate' else result_type)]


def store(key, name, offset):
    op = key.split('.', 1)[0]
    result_type = types(key)[1]
    if result_type == 'predicate':
        return f'psts({name},(__ubuf__ uint32_t *)out,{offset},NORM);'
    if op in ('VCADD', 'VCMAX'):
        mask = 'one' if op == 'VCADD' else 'two'
        return f'vsts({name},(__ubuf__ float *)out,{offset // 4},NORM_B32,{mask});'
    return f'vsts((vector_u8){name},out,{offset},NORM_B8,full);'


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 29 + 11) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        keys = [case['previous_key'], case['current_key']]
        streams = case.get('streams', 3 if all(types(k)[1] == 'predicate' for k in keys) else 6)
        sequence = [(group, slot) for start in range(0, streams, 2)
                    for group in range(2) for slot in range(start, min(start + 2, streams))]
        if case.get('order') == 'reverse_pairs':
            sequence = [(1 - group, slot) for group, slot in sequence]
        elif case.get('order') == 'alternating':
            sequence = [(group, slot) for slot in range(streams) for group in range(2)]
        if case.get('order') == 'blocks':
            sequence.sort()
        group_counts=[case.get('previous_streams',streams),case.get('current_streams',streams)]
        if any(count>streams for count in group_counts):raise ValueError('Group streams exceed allocated streams')
        sequence=[(group,slot) for group,slot in sequence if slot<group_counts[group]]
        base = stride * number
        body = ['vector_bool full=pset_b8(PAT_ALL),one,two;']
        data[base + 3712:base + 3744] = (1).to_bytes(32, 'little')
        data[base + 3744:base + 3776] = (1 | (1 << 4)).to_bytes(32, 'little')
        body += [f'plds(one,(__ubuf__ uint32_t *)input,{base + 3712},NORM);',
                 f'plds(two,(__ubuf__ uint32_t *)input,{base + 3744},NORM);']
        wide_predicates = case.get('wide_predicates', False)
        if wide_predicates and any(key.startswith('PAND.') for key in keys):
            data[base + 3776:base + 3808] = (0x111).to_bytes(32, 'little')
            body += ['vector_bool three;',
                     f'plds(three,(__ubuf__ uint32_t *)input,{base + 3776},NORM);']
        dtypes = sorted({types(k)[0] for k in keys})
        arrays, other_raw, scalars = {}, {}, {}
        # Scalar VDUPS operands come from runtime UB loads outside the VF.
        parameters, scalar_setup, arguments = [], [], []
        for group, key in enumerate(keys):
            source_type, result_type = types(key)
            a_type = compute.TYPES[source_type]
            result_vector = 'bool' if result_type == 'predicate' else compute.TYPES[result_type].vector
            for slot in range(streams):
                index = group * streams + slot
                raw = compute.encode(input_values(source_type, index, case['replica']), source_type)
                arrays[group, slot] = raw
                data[base + index * 256:base + (index + 1) * 256] = raw
                body += [f'vector_{a_type.vector} a{group}_{slot};',
                         f'vector_{result_vector} r{group}_{slot};',
                         f'vlds(a{group}_{slot},(__ubuf__ {a_type.scalar} *)input,{(base + index * 256) // a_type.width},NORM);']
                if key.startswith('VAXPY.'):
                    body.append(f'r{group}_{slot}=a{group}_{slot};')
                value = compute.quantize(0.5 + (index + case['replica']) / 16, 'fp32')
                scalars[group, slot] = value
                if key.startswith('VDUPS.'):
                    position = base + 3584 + index * 8
                    data[position:position + 4] = compute.encode([value], 'fp32')
                    parameters.append(f'float s{group}_{slot}')
                    scalar_setup.append(f'float scalar_{number}_{index}=*((__ubuf__ float *)(input+{position}));')
                    arguments.append(f'scalar_{number}_{index}')
                elif case.get('runtime_scalar_operands') and key.split('.', 1)[0] in ('PLT', 'VCI'):
                    is_plt = key.startswith('PLT.')
                    scalar_type = 'uint32_t' if is_plt else 'int32_t'
                    scalar_dtype = 'uint32' if is_plt else 'int32'
                    scalar_value = 4 * (index % 6 + 1) if is_plt else index * 3
                    position = base + 3584 + index * 8
                    data[position:position + 4] = compute.encode([scalar_value], scalar_dtype)
                    parameters.append(f'{scalar_type} n{group}_{slot}')
                    scalar_setup.append(f'{scalar_type} scalar_{number}_{index}=*((__ubuf__ {scalar_type} *)(input+{position}));')
                    arguments.append(f'scalar_{number}_{index}')
        for index, dtype in enumerate(dtypes):
            t = compute.TYPES[dtype]
            offset = base + 3072 + index * 256
            values = input_values(dtype, 29 + index, case['replica'])
            other_raw[dtype] = compute.encode(values, dtype)
            data[offset:offset + 256] = other_raw[dtype]
            body += [f'vector_{t.vector} b_{dtype};',
                     f'vlds(b_{dtype},(__ubuf__ {t.scalar} *)input,{offset // t.width},NORM);']
        golden[base:base + stride] = data[base:base + stride]
        if any(unmasked(key) for key in keys):
            # Backpressure a long live chain so unmasked PSET targets reach
            # dispatch after the independent target operands have warmed.
            warm_op=case.get('warm_operation','vor')
            if warm_op=='vadd' and any(key.startswith('VADD.') for key in keys):
                warm_op='vsub'
            warm_type='float' if warm_op in ('vadd','vsub') else 'uint32_t'
            warm_vector='f32' if warm_op in ('vadd','vsub') else 'u32'
            body += [f'vector_{warm_vector} warm,word;',
                     f'vlds(warm,(__ubuf__ {warm_type} *)input,{base // 4},NORM);',
                     f'vlds(word,(__ubuf__ {warm_type} *)input,{(base + 256) // 4},NORM);']
            if case.get('warm_lead',0):
                body += ['vector_u32 lead;', 'vor(lead,(vector_u32)word,(vector_u32)word,full,MODE_ZEROING);']
                golden[base + 3072:base + 3328] = data[base + 256:base + 512]
            count=case.get('warm_length',80) + case['replica'] % 3
            body += [f'{warm_op}(warm,warm,word,full,MODE_ZEROING);'] * count
            warm = bytes(x | y for x, y in zip(data[base:base + 256], data[base + 256:base + 512]))
            if warm_op in ('vadd','vsub'):
                values=compute.decode(data[base:base+256],'fp32')
                addend=compute.decode(data[base+256:base+512],'fp32')
                for _ in range(count):
                    values=[compute.quantize(x+y if warm_op=='vadd' else x-y,'fp32') for x,y in zip(values,addend)]
                warm=compute.encode(values,'fp32')
            golden[base + 3328:base + 3584] = warm
        else:
            for group, key in enumerate(keys):
                source_type = types(key)[0]
                t = compute.TYPES[source_type]
                for slot in range(streams):
                    body.append(f'vsts(a{group}_{slot},(__ubuf__ {t.scalar} *)input,{(base + (group * streams + slot) * 256) // t.width},NORM_B{t.width * 8},full);')
            data[base + 3840:base + 3872] = b'\xff' * 32
            golden[base + 3840:base + 3872] = b'\xff' * 32
            body += ['mem_bar(VV_ALL);', 'vector_bool late;',
                     f'plds(late,(__ubuf__ uint32_t *)input,{base + 3840},NORM);']
        if case.get('gate_unmasked',False):
            gate_mask='full' if any(unmasked(key) for key in keys) else 'late'
            for group,key in enumerate(keys):
                if key.startswith('VPACK.'):
                    for slot in range(streams):
                        body.append(f'vor(a{group}_{slot},a{group}_{slot},a{group}_{slot},{gate_mask},MODE_ZEROING);')
        regions = []
        if case.get('warm_lead',0) and any(unmasked(key) for key in keys):
            regions.append(dict(offset=base + 3072,bytes=256,dtype='uint8'))
        for group, slot in sequence:
            key = keys[group]
            dtype = types(key)[0]
            mask = 'full' if any(unmasked(k) for k in keys) else 'late'
            if case.get('runtime_scalar_operands') and key.startswith('PLT.'):
                body.append(f'r{group}_{slot}=plt_b32(n{group}_{slot},POST_UPDATE);')
            elif case.get('runtime_scalar_operands') and key.startswith('VCI.'):
                body.append(f'vci(r{group}_{slot},n{group}_{slot},INC_ORDER);')
            elif wide_predicates and key.startswith('PAND.'):
                lhs, rhs = [('one', 'full'), ('two', 'full'), ('three', 'full'),
                            ('one', 'two'), ('two', 'three'), ('one', 'three')][slot % 6]
                body.append(f'pand(r{group}_{slot},{lhs},{rhs},{mask});')
            else:
                body.append(emit(key, f'r{group}_{slot}', f'a{group}_{slot}', f'b_{dtype}',
                                 mask, f's{group}_{slot}', group * streams + slot))
            raw = reference(key, arrays[group, slot], other_raw[dtype], scalars[group, slot], group * streams + slot)
            if wide_predicates and key.startswith('PAND.'):
                raw = [1, 0x11, 0x111, 1, 0x11, 1][slot % 6].to_bytes(32, 'little')
            offset = base + (group * streams + slot) * 256
            golden[offset:offset + len(raw)] = raw
            regions += result_regions(key, offset, raw)
            if case.get('store_immediately', False):
                body.append(store(key, f'r{group}_{slot}', offset))
        for group, key in enumerate(keys):
            for slot in range(group_counts[group]):
                if not case.get('store_immediately', False):
                    body.append(store(key, f'r{group}_{slot}', base + (group * streams + slot) * 256))
        if any(unmasked(key) for key in keys):
            if case.get('warm_lead',0):
                body.append(f'vsts((vector_u8)lead,out,{base + 3072},NORM_B8,full);')
            body.append(f'vsts((vector_u8)warm,out,{base + 3328},NORM_B8,full);')
            regions.append(dict(offset=base + 3328, bytes=256, dtype='uint8'))
        name = f'probe_{number}'
        suffix = ',' + ','.join(parameters) if parameters else ''
        functions.append(f'__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out{suffix}){{__VEC_SCOPE__{{\n' + '\n'.join(body) + '\n}}')
        calls += scalar_setup + [f'{name}(input,out' + (',' + ','.join(arguments) if arguments else '') + ');']
        contracts.append(dict(case, api='operator_pair', dtype='fp32', form='mixed', streams=streams,
                              target_opcodes=sorted({op for key in keys for op in physical(key)}),
                              target_count=len(sequence), output_offset=base, output_bytes=3584,
                              guard_offset=base + 3584, guard_bytes=512, output_regions=regions,
                              record_supporting_compute=True, warm_operands=True))
        if any(key.startswith('VAXPY.') for key in keys):
            contracts[-1]['reads_old_destination_opcodes'] = ['RV_VAXPY']
    size = len(data)
    if size > 28 * stride:
        raise ValueError('Batch exceeds separate input/output UB windows and stack reserve')
    output_base = 0x10000 if size <= 65536 else 0x20000
    source = '#define __aicore__ [aicore]\n' + '\n'.join(functions) + f'''
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *src,__gm__ uint8_t *dst){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm({output_base});
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


def validate_regions(actual, golden, case):
    allowed = set()
    mismatches = []
    for region in case['output_regions']:
        start, size, dtype = region['offset'], region['bytes'], region['dtype']
        allowed.update(range(start, start + size))
        width = compute.TYPES[dtype].width
        for offset in range(start, start + size, width):
            a, b = actual[offset:offset + width], golden[offset:offset + width]
            if a == b:
                continue
            good = False
            if dtype in compute.FLOATING:
                x, y = compute.decode(a, dtype)[0], compute.decode(b, dtype)[0]
                good = math.isfinite(x) and math.isfinite(y) and math.isclose(
                    x, y, rel_tol=0.004 if dtype != 'fp32' else 2e-5,
                    abs_tol=0.004 if dtype != 'fp32' else 2e-6)
            if not good:
                mismatches.append(offset)
    for offset in range(case['output_offset'], case['output_offset'] + 4096):
        if offset not in allowed and actual[offset] != golden[offset]:
            mismatches.append(offset)
    return sorted(set(mismatches))


def analyze(work):
    contracts = json.loads((work / 'cases.json').read_text())
    golden, actual = (work / 'golden.bin').read_bytes(), (work / 'output.bin').read_bytes()
    rows = compute.analyze(work, contracts, golden)
    for row in rows:
        keys = [row['previous_key'], row['current_key']]
        row['all_compute_events'] = [event for event in row['events'] if event['port'] is not None] + row['supporting_compute_events']
        row['events'] = [dict(event, form_key=key) for event in row['events']
                         if (key := event_key(event, keys)) is not None]
        ids = {event['id'] for event in row['events']}
        row['independent_same_port_gaps'] = [gap for gap in row['independent_same_port_gaps']
                                            if gap['previous_id'] in ids and gap['current_id'] in ids]
        errors = validate_regions(actual, golden, row)
        row['generic_single_dtype_validation'] = row['passed']
        row['passed'] = not errors
        row['mismatch_offsets'] = errors[:64]
        row['target_count_actual'] = len(row['events'])
        row['single_op_sample'] = len(row['events']) == row['target_count']
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--inventory', type=Path)
    source.add_argument('--cases', type=Path, help='Re-run saved probe case specifications')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--replicas', type=int, default=6)
    parser.add_argument('--replica-start', type=int, default=0)
    parser.add_argument('--warm-lead', type=int, choices=[0,1], default=0)
    parser.add_argument('--warm-operation', choices=['vor','vadd'], default='vadd')
    parser.add_argument('--warm-length',type=int,nargs='+',default=[80])
    parser.add_argument('--gate-unmasked',action='store_true')
    parser.add_argument('--wide-predicates', action='store_true')
    parser.add_argument('--store-immediately', action='store_true')
    parser.add_argument('--runtime-scalar-operands', action='store_true')
    parser.add_argument('--streams',type=int,choices=[1,2,3,4,6])
    parser.add_argument('--previous-streams',type=int,choices=[1,2,3,4,6])
    parser.add_argument('--current-streams',type=int,choices=[1,2,3,4,6])
    parser.add_argument('--order',choices=['paired','reverse_pairs','alternating','blocks'])
    parser.add_argument('--pairs', nargs='+', help='Exact previous::current form keys')
    parser.add_argument('--batch-size', type=int, choices=range(1, 29), default=8)
    parser.add_argument('--run-timeout', type=int, default=300)
    parser.add_argument('--jobs', type=int, choices=[1, 2, 3], default=2)
    parser.add_argument('--cann', type=Path, default=Path(os.environ.get('ACL_PATH', '/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1')))
    args = parser.parse_args()
    if args.cases:
        cases = json.loads(args.cases.read_text())
        if any(case.get('kind') != 'ii' for case in cases):
            parser.error('--cases requires II probe specifications')
    else:
        inventory = json.loads(args.inventory.read_text())
        needed = [row for row in inventory['ii'] if row['explicit'] is None]
        if args.pairs:
            needed = [row for row in needed if row['previous'] + '::' + row['current'] in args.pairs]
        selected = {}
        for row in needed:
            selected.setdefault(tuple(sorted((row['previous'], row['current']))), row)
        cases = [dict(previous_key=row['previous'], current_key=row['current'],
                      kind='ii', replica=replica, warm_length=length, warm_lead=args.warm_lead,
                      warm_operation=args.warm_operation, gate_unmasked=args.gate_unmasked,
                      order=args.order or ('paired' if replica % 2 == 0 else 'reverse_pairs'))
                 for row in selected.values() for length in args.warm_length
                 for replica in range(args.replica_start, args.replica_start + args.replicas)]
    for flag in ['wide_predicates', 'store_immediately', 'runtime_scalar_operands']:
        if getattr(args, flag):
            for case in cases:
                case[flag] = True
    if args.streams:
        for case in cases:case['streams']=args.streams
    for group in ['previous','current']:
        count=getattr(args,group+'_streams')
        if count:
            for case in cases:case[group+'_streams']=count
    if not cases:
        parser.error('No missing pairs selected')
    batches = [cases[i:i + args.batch_size] for i in range(0, len(cases), args.batch_size)]

    def run(item):
        index, batch = item
        work = args.out / f'batch_{index:03}'
        compute.run_batch(batch, work, args.cann, factory=make_batch, run_timeout=args.run_timeout)
        rows = analyze(work)
        (work / 'validation.json').write_text(json.dumps(rows, indent=2) + '\n')
        result = dict(batch=index, cases=len(rows), passed=sum(row['passed'] and row['single_op_sample'] for row in rows),
                      failures=[dict(previous=row['previous_key'], current=row['current_key'],
                                     mismatches=row['mismatch_offsets'], actual=row['target_count_actual'],
                                     expected=row['target_count']) for row in rows
                                if not row['passed'] or not row['single_op_sample']])
        print(json.dumps(result), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, enumerate(batches)))
    if any(row['passed'] != row['cases'] for row in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
