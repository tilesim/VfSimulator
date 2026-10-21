"""A5 reductions, carry arithmetic, long multiplication and rearrangement probes.

All architecturally defined outputs are checked, including predicate carry bits,
both vector results and reduction indices. Undefined reduction lanes are never
read or stored. Each VF has an independent 4096-byte input/output window.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct

import vector_compute as compute

REDUCTIONS = {"vcadd", "vcmax", "vcmin", "vcgadd", "vcgmax", "vcgmin", "vcpadd"}
CARRY = {"vaddc", "vaddcs", "vsubc", "vsubcs"}
PAIRS = {"vmull", "vintlv", "vdintlv"}
SELECT = {"vsel"}
OPERATIONS = REDUCTIONS | CARRY | PAIRS | SELECT
INTEGER_REDUCTIONS = {"fp16", "fp32", "int16", "uint16", "int32", "uint32"}


def output_dtype(op, dtype):
    return (
        {"int16": "int32", "uint16": "uint32"}.get(dtype, dtype)
        if op in {"vcadd", "vcgadd"}
        else dtype
    )


def reduce_result(op, raw, dtype, active):
    """Return only defined result lanes; indices use integer bit patterns."""
    values = compute.decode(raw, dtype)[:active]
    out_type = output_dtype(op, dtype)
    width = compute.TYPES[dtype].width
    if op == "vcadd":
        return compute.encode([compute.quantize(sum(values), out_type)], out_type)
    if op in {"vcmax", "vcmin"}:
        value = (max if op == "vcmax" else min)(values)
        return compute.encode([value], dtype) + values.index(value).to_bytes(
            width, "little"
        )
    if op == "vcpadd":
        return compute.encode(
            [compute.quantize(a + b, dtype) for a, b in zip(values[::2], values[1::2])],
            dtype,
        )
    result = []
    for start in range(0, active, 32 // width):
        group = values[start : start + 32 // width]
        result.append(
            compute.quantize(
                sum(group)
                if op == "vcgadd"
                else (max if op == "vcgmax" else min)(group),
                out_type,
            )
        )
    return compute.encode(result, out_type)


def pair_result(op, left, right, dtype, active=None, carry=None):
    t = compute.TYPES[dtype]
    lanes = 256 // t.width
    if op in {"vintlv", "vdintlv"}:
        a = [left[i : i + t.width] for i in range(0, 256, t.width)]
        b = [right[i : i + t.width] for i in range(0, 256, t.width)]
        if op == "vintlv":
            merged = [x for pair in zip(a, b) for x in pair]
            return b"".join(merged[:lanes]), b"".join(merged[lanes:])
        merged = a + b
        return b"".join(merged[::2]), b"".join(merged[1::2])
    a = compute.decode(left, dtype)
    b = compute.decode(right, dtype)
    outputs, extra = [], []
    active = lanes if active is None else active
    for i, (x, y) in enumerate(zip(a, b)):
        if i >= active:
            outputs.append(0)
            extra.append(0)
            continue
        if op == "vmull":
            value = int(x) * int(y)
            outputs.append(value & 0xFFFFFFFF)
            extra.append((value >> 32) & 0xFFFFFFFF)
        else:
            x, y = int(x) & 0xFFFFFFFF, int(y) & 0xFFFFFFFF
            cin = int(carry[i]) if carry is not None and op.endswith("cs") else 0
            value = (
                x + y + cin
                if op.startswith("vadd")
                else x - y - (1 - cin if op.endswith("cs") else 0)
            )
            outputs.append(value & 0xFFFFFFFF)
            extra.append(
                int(value > 0xFFFFFFFF) if op.startswith("vadd") else int(value >= 0)
            )
    first = struct.pack("<64I", *outputs)
    if op == "vmull":
        return first, struct.pack("<64I", *extra)
    bitmap = sum(bit << (4 * i) for i, bit in enumerate(extra)).to_bytes(32, "little")
    return first, bitmap


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for index, case in enumerate(cases):
        op, dtype = case["api"], case["dtype"]
        port_pressure = bool(case.get("port_pressure")) and op not in SELECT
        t = compute.TYPES[dtype]
        out_type = output_dtype(op, dtype)
        out = compute.TYPES[out_type]
        lanes, base = 256 // t.width, index * stride
        arrays = []
        for stream in range(5):
            if op in CARRY or op == "vmull":
                patterns = (
                    [0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0xFFFFFFFE, 7, 100000]
                    if stream < 4
                    else [1, 0xFFFFFFFF, 2, 0x80000000, 5, 0xFFFFFFFE, 3, 100000]
                )
                values = [
                    compute.quantize(patterns[(i + stream) % len(patterns)], dtype)
                    for i in range(lanes)
                ]
                raw = compute.encode(values, dtype)
            elif op in PAIRS:
                raw = bytes((i * 17 + stream * 31 + 7) % 256 for i in range(256))
            else:
                # Exactly representable values avoid a dependency on reduction
                # tree ordering while making the extremum/index unique.
                values = [((i * 7 + stream * 11) % 31) - 15 for i in range(lanes)]
                if not t.signed and dtype not in compute.FLOATING:
                    values = [x + 16 for x in values]
                values[(9 + stream * 5) % lanes] = (
                    -40 if t.signed or dtype in compute.FLOATING else 0
                )
                values[(19 + stream * 3) % lanes] = 50
                if op in {"vcadd", "vcgadd"} and dtype in {"int16", "uint16"}:
                    values = [x * 1000 for x in values]
                    values[(9 + stream * 5) % lanes] = -30000 if t.signed else 0
                    values[(19 + stream * 3) % lanes] = 31000 if t.signed else 50000
                raw = compute.encode(values, dtype)
            arrays.append(raw)
            data[base + 256 * stream : base + 256 * (stream + 1)] = raw
        if case.get("carry_chain_data_control"):
            b = compute.decode(arrays[4], dtype)
            arrays[1] = compute.encode([0] * lanes, dtype)
            values = [
                compute.quantize(
                    0xFFFFFFFF - (int(x) & 0xFFFFFFFF) if op == "vaddcs" else x, dtype
                )
                for x in b
            ]
            arrays[2] = compute.encode(values, dtype)
            data[base + 256 : base + 512] = arrays[1]
            data[base + 512 : base + 768] = arrays[2]
        # The output window is initialized from the final input image. Keep
        # unwritten bytes identical, including padding between sparse results.
        golden[base : base + stride] = data[base : base + stride]
        body = [
            f"vector_bool p=pset_b{8 * t.width}(PAT_ALL),halfmask=pset_b{8 * t.width}(PAT_VL{lanes // 2});",
            f"vector_{t.vector} a0,a1,a2,a3,b;",
            f"vector_{t.vector if op == 'vcgadd' else out.vector} r0,r1,r2,r3,z0,z1,z2,z3;",
            "vector_bool q0,q1,q2,q3,q4,q5,q6;",
            f"__ubuf__ {t.scalar} *src=(__ubuf__ {t.scalar} *)input;",
            f"__ubuf__ {out.scalar} *dst=(__ubuf__ {out.scalar} *)out;",
        ]
        body += [
            f"vlds(a{i},src,{(base + 256 * i) // t.width},NORM);" for i in range(4)
        ]
        body += [f"vlds(b,src,{(base + 1024) // t.width},NORM);"]
        if op in SELECT and not case.get("warm_operands"):
            body += [f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);"]
        in_place_carry = case.get("in_place_carry") and op in {"vaddcs", "vsubcs"}
        if in_place_carry:
            for i in range(7):
                body += [
                    f"q{i}=pset_b32(PAT_VL{1 << i});",
                    f"psts(q{i},(__ubuf__ uint32_t *)input,{base + 1536 + 32 * i},NORM);",
                ]
        if case.get("warm_operands"):
            body += [
                f"vsts(a{i},src,{(base + 256 * i) // t.width},NORM_B{8 * t.width},p);"
                for i in range(4)
            ]
            body += [
                f"vsts(b,src,{(base + 1024) // t.width},NORM_B{8 * t.width},p);",
                "mem_bar(VV_ALL);",
                f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
            ]
            bitmap = sum(
                1 << lane
                for lane in range(0, 256, t.width * (2 if op in SELECT else 1))
            ).to_bytes(32, "little")
            data[base + 3840 : base + 3872] = bitmap
            golden[base + 3840 : base + 3872] = bitmap
            if op in {"vintlv", "vdintlv"} or port_pressure:
                body += ["vor(b,b,b,p,MODE_ZEROING);"]
        if op in SELECT and not case.get("warm_operands"):
            bitmap = sum(1 << lane for lane in range(0, 256, 2 * t.width)).to_bytes(
                32, "little"
            )
            data[base + 3840 : base + 3872] = bitmap
            golden[base + 3840 : base + 3872] = bitmap
        if port_pressure:
            if op in REDUCTIONS:
                body += [f"vor(a{i},a{i},a{i},p,MODE_ZEROING);" for i in range(4)]
            body.append(f"vector_{t.vector} pressure0,pressure1;")
            pressure_api = "vdintlv" if op == "vintlv" else "vintlv"
            for i in range(4):
                body += [
                    f"{pressure_api}(pressure0,pressure1,a{i},b);",
                    f"vsts(pressure0,src,{(base + 256 * i) // t.width},NORM_B{8 * t.width},p);",
                    f"vsts(pressure1,src,{(base + 1280) // t.width},NORM_B{8 * t.width},p);",
                ]

        def emit(
            slot,
            left,
            right="b",
            mask="p",
            carry="p",
            active=lanes,
            raw_left=None,
            raw_right=None,
            carry_bits=None,
        ):
            n = slot if slot < 4 else 0
            q = (
                slot
                if in_place_carry
                and not (case.get("chain_source") == "carry" and slot in {4, 5})
                else n
            )
            if op in REDUCTIONS:
                body.append(f"{op}(r{n},{left},{mask},MODE_ZEROING);")
                result = reduce_result(op, raw_left, dtype, active)
                count = len(result) // out.width
                body.append(
                    f"vector_bool store{slot}=pset_b{8 * out.width}(PAT_VL{count});"
                )
                body.append(
                    f"vsts((vector_{out.vector})r{n},dst,{(base + slot * 512) // out.width},NORM_B{8 * out.width},store{slot});"
                )
                golden[base + slot * 512 : base + slot * 512 + len(result)] = result
                return result, b""
            if op in CARRY:
                if in_place_carry and not (
                    case.get("chain_source") == "carry" and slot in {4, 5}
                ):
                    carry = f"q{q}"
                    carry_bits = [i < (1 << slot) for i in range(lanes)]
                cin = "," + carry if op.endswith("cs") else ""
                body.append(f"{op}(q{q},r{n},{left},{right}{cin},{mask});")
            elif op == "vmull":
                body.append(f"vmull(r{n},z{n},{left},{right},{mask});")
            elif op in SELECT:
                body.append(f"vsel(r{n},{left},{right},{mask});")
            else:
                body.append(f"{op}(r{n},z{n},{left},{right});")
            body.append(
                f"vsts(r{n},dst,{(base + slot * 512) // out.width},NORM_B{8 * out.width},pset_b{8 * out.width}(PAT_ALL));"
            )
            if op in CARRY:
                body.append(
                    f"psts(q{q},(__ubuf__ uint32_t *)out,{base + slot * 512 + 256},NORM);"
                )
            elif op not in SELECT:
                body.append(
                    f"vsts(z{n},dst,{(base + slot * 512 + 256) // out.width},NORM_B{8 * out.width},pset_b{8 * out.width}(PAT_ALL));"
                )
            if op in SELECT:
                bits = [
                    i < active if mask == "halfmask" else i % 2 == 0
                    for i in range(lanes)
                ]
                first = b"".join(
                    (raw_left if bit else raw_right)[i * t.width : (i + 1) * t.width]
                    for i, bit in enumerate(bits)
                )
                second = b""
            else:
                first, second = pair_result(
                    op, raw_left, raw_right, dtype, active, carry_bits
                )
            golden[base + slot * 512 : base + slot * 512 + 256] = first
            golden[base + slot * 512 + 256 : base + slot * 512 + 256 + len(second)] = (
                second
            )
            return first, second

        results = [
            emit(
                i,
                f"a{i}",
                raw_left=arrays[i],
                raw_right=arrays[4],
                carry_bits=[True] * lanes,
            )
            for i in range(4)
        ]
        chain_source = case.get("chain_source", "lhs")
        result_index = case.get("producer_result", 0)
        previous = results[0]
        for slot in (4, 5):
            if op in REDUCTIONS:
                needed = (4 if slot == 4 else 2) if op == "vcpadd" else 1
                body.append(
                    f"vector_bool chain{slot}=pset_b{8 * t.width}(PAT_VL{needed});"
                )
                raw = previous[0].ljust(256, b"\0")
                previous = emit(
                    slot,
                    f"(vector_{t.vector})r0",
                    mask=f"chain{slot}",
                    active=needed,
                    raw_left=raw,
                )
            elif chain_source in {"carry", "mask"}:
                bits = [
                    bool((int.from_bytes(previous[1], "little") >> (4 * i)) & 1)
                    for i in range(lanes)
                ]
                if chain_source == "carry":
                    i = slot - 3 if case.get("carry_chain_data_control") else 0
                    previous = emit(
                        slot,
                        f"a{i}",
                        carry="q0",
                        raw_left=arrays[i],
                        raw_right=arrays[4],
                        carry_bits=bits,
                    )
                else:
                    # A sparse, data-dependent predicate validates the complete
                    # mask result. pair_result takes the mask lane by lane here.
                    mask_bytes = previous[1]
                    cin = (
                        [i < (1 << slot) for i in range(lanes)]
                        if in_place_carry
                        else [True] * lanes
                    )
                    first, second = pair_result(
                        op, arrays[0], arrays[4], dtype, lanes, cin
                    )
                    mask_register = "q4" if in_place_carry and slot == 5 else "q0"
                    previous = emit(
                        slot,
                        "a0",
                        mask=mask_register,
                        raw_left=arrays[0],
                        raw_right=arrays[4],
                        carry_bits=[True] * lanes,
                    )
                    masked_first = b"".join(
                        first[4 * i : 4 * i + 4] if bit else bytes(4)
                        for i, bit in enumerate(bits)
                    )
                    masked_second = (
                        int.from_bytes(second, "little")
                        & int.from_bytes(mask_bytes, "little")
                    ).to_bytes(32, "little")
                    golden[base + slot * 512 : base + slot * 512 + 256] = masked_first
                    golden[base + slot * 512 + 256 : base + slot * 512 + 288] = (
                        masked_second
                    )
                    previous = masked_first, masked_second
            else:
                value = f"{'r' if result_index == 0 else 'z'}0"
                if chain_source == "rhs":
                    previous = emit(
                        slot,
                        "b",
                        value,
                        raw_left=arrays[4],
                        raw_right=previous[result_index],
                        carry_bits=[True] * lanes,
                    )
                else:
                    previous = emit(
                        slot,
                        value,
                        raw_left=previous[result_index],
                        raw_right=arrays[4],
                        carry_bits=[True] * lanes,
                    )
        if op in {"vintlv", "vdintlv"}:
            emit(6, "b", "a3", raw_left=arrays[4], raw_right=arrays[3])
        else:
            emit(
                6,
                "a3",
                mask="halfmask",
                active=lanes // 2,
                raw_left=arrays[3],
                raw_right=arrays[4],
                carry_bits=[True] * lanes,
            )
        name = f"probe_{index}_{op}_{dtype}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        contracts.append(
            {
                **case,
                "index": index,
                "output_offset": base,
                "port_pressure": port_pressure,
                "output_bytes": 3584,
                "guard_offset": base + 3584,
                "guard_bytes": 512,
                "target_count": 7,
                "exact_bytes": True,
                "result_dtype": out_type,
                "chain_input_lanes": [4, 2]
                if op == "vcpadd"
                else [1, 1]
                if op in REDUCTIONS
                else None,
                "pressure_opcode": "RV_" + ("VDINTLV" if op == "vintlv" else "VINTLV"),
            }
        )
    size = len(data)
    source = (
        "#define __aicore__ [aicore]\n"
        + "\n".join(functions)
        + f"""
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *inputGM,__gm__ uint8_t *outputGM){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0);
 __ubuf__ uint8_t *out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,inputGM,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,inputGM,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, bytes(data), bytes(golden), contracts


def cases_for(operations=None, dtypes=None):
    result = []
    for op in sorted(OPERATIONS):
        if operations and op not in operations:
            continue
        forms = (
            INTEGER_REDUCTIONS
            if op in REDUCTIONS
            else {"int32", "uint32"}
            if op in CARRY | {"vmull"}
            else {
                d
                for d, t in compute.TYPES.items()
                if t.width <= 4 and d not in {"hif8", "f4e1m2x2", "f4e2m1x2", "s4x2"}
            }
        )
        if op == "vcpadd":
            forms = {"fp16", "fp32"}
        if dtypes:
            forms = forms & set(dtypes)
        result += [{"api": op, "dtype": dtype} for dtype in sorted(forms)]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--cann",
        type=Path,
        default=Path(
            os.environ.get(
                "ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"
            )
        ),
    )
    parser.add_argument("--operations", nargs="+")
    parser.add_argument("--dtypes", nargs="+")
    parser.add_argument("--batch-size", type=int, choices=range(1, 17), default=16)
    parser.add_argument("--warm-operands", action="store_true")
    parser.add_argument(
        "--in-place-carry",
        action="store_true",
        help="Prime distinct carry destinations before the readiness fence",
    )
    parser.add_argument(
        "--carry-chain-data-control",
        action="store_true",
        help="Change carry state before a carry-sensitive dependent result",
    )
    parser.add_argument(
        "--port-pressure",
        action="store_true",
        help="Compete with four observable EXU0 interleave instructions",
    )
    parser.add_argument(
        "--chain-source", choices=("lhs", "rhs", "carry", "mask"), default="lhs"
    )
    parser.add_argument("--producer-result", type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    cases = [
        {
            **c,
            "warm_operands": args.warm_operands,
            "chain_source": args.chain_source,
            "producer_result": args.producer_result,
            "port_pressure": args.port_pressure,
            "in_place_carry": args.in_place_carry,
        }
        for c in cases_for(args.operations, args.dtypes)
    ]
    if args.carry_chain_data_control:
        if args.chain_source != "carry" or any(
            c["api"] not in {"vaddcs", "vsubcs"} for c in cases
        ):
            parser.error("carry data control requires the VADDCS/VSUBCS carry source")
        cases = [{**c, "carry_chain_data_control": True} for c in cases]
    if not cases:
        parser.error("No matching structural API/forms")
    if args.chain_source == "carry" and any(
        c["api"] not in {"vaddcs", "vsubcs"} for c in cases
    ):
        parser.error("carry source requires VADDCS or VSUBCS")
    if args.chain_source == "mask" and any(c["api"] not in CARRY for c in cases):
        parser.error("mask source requires a predicate-output operation")
    if args.producer_result and any(c["api"] not in PAIRS for c in cases):
        parser.error("second vector result requires a two-vector-output operation")
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")
    reports = []
    for n, start in enumerate(range(0, len(cases), args.batch_size)):
        batch = cases[start : start + args.batch_size]
        print(f"Batch {n}: {len(batch)} cases", flush=True)
        rows = compute.run_batch(
            batch, args.out / f"batch_{n:03}", args.cann, factory=make_batch
        )
        reports.extend(rows)
        (args.out / "summary.json").write_text(json.dumps(reports, indent=2) + "\n")
        print(
            f"passed {sum(r['passed'] for r in rows)} of {len(rows)}; direct {sum(r['single_op_sample'] for r in rows)}",
            flush=True,
        )
    if any(not r["passed"] for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
