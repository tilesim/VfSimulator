"""Numerical probes for difference-exponential, block stores and paired indexing."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import random

import vector_compute as compute


def make_batch(cases):
    stride = 4096
    data = bytearray(random.Random(20261019).randbytes(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        api, dtype = case["api"], case["dtype"]
        t, base = compute.TYPES[dtype], number * stride
        body = ["vector_bool p=pset_b8(PAT_ALL), all=pset_b8(PAT_ALL);"]
        target, count = ["RV_" + api.upper()], 4
        if api == "vexpdif":
            part = case.get("part", "PART_EVEN")
            vectors = []
            for stream in range(8):
                seed = 7 if dtype == "fp16" and stream in (0, 1) else stream
                values = [
                    compute.quantize(
                        0
                        if dtype == "fp16" and stream in (0, 1, 7)
                        else (i % 7 - 3 + seed) / 16,
                        dtype,
                    )
                    for i in range(256 // t.width)
                ]
                vectors.append(values)
                data[base + stream * 256 : base + (stream + 1) * 256] = compute.encode(
                    values, dtype
                )
            golden[base : base + stride] = data[base : base + stride]
            body += [
                f"vector_{t.vector} a0,a1,a2,a3,a4,a5,a6,b;",
                "vector_f32 r0,r1,r2,r3,r4,r5,r6;",
            ]
            for i, name in enumerate(["a0", "a1", "a2", "a3", "a4", "a5", "a6", "b"]):
                body += [
                    f"vlds({name},input,{(base + 256 * i) // t.width},NORM);",
                    f"vsts({name},input,{(base + 256 * i) // t.width},NORM_B{t.width * 8},all);",
                ]
            body += ["mem_bar(VV_ALL);"]
            results = []
            for slot in range(7):
                source = f"a{slot}"
                values = vectors[slot]
                rhs, rhs_values = "b", vectors[7]
                if slot in (4, 5):
                    previous = 0 if slot == 4 else 4
                    source = f"(vector_{t.vector})r{previous}"
                    values = compute.decode(
                        compute.encode(results[previous], "fp32"), dtype
                    )
                    if case.get("chain_source") == "rhs":
                        rhs, rhs_values = source, values
                        source, values = "b", vectors[7]
                body += [
                    f"vexpdif(r{slot},{source},{rhs},p,{part});",
                    f"vsts(r{slot},(__ubuf__ float *)out,{(base + 256 * slot) // 4},NORM_B32,all);",
                ]
                selected = (
                    list(range(64))
                    if dtype == "fp32"
                    else list(range(0 if part == "PART_EVEN" else 1, 128, 2))
                )
                result = [
                    compute.quantize(math.exp(values[i] - rhs_values[i]), "fp32")
                    for i in selected
                ]
                results.append(result)
                golden[base + 256 * slot : base + 256 * (slot + 1)] = compute.encode(
                    result, "fp32"
                )
            count, out_bytes = 7, 1792
        elif api == "movvp":
            body += [
                f"vector_{t.vector} a0,a1,a2,a3,a4,a5,a6,result;",
                "vector_bool p0,p1,p2,p3,p4,p5,p6;",
            ]
            arrays = []
            for slot in range(7):
                values = [
                    (
                        (lane * 0x9E3779B1 + slot * 0x6C8E9CF5)
                        & ((1 << (t.width * 8)) - 1)
                    )
                    for lane in range(256 // t.width)
                ]
                arrays.append(values)
                data[base + 256 * slot : base + 256 * (slot + 1)] = compute.encode(
                    values, dtype
                )
                body += [f"vlds(a{slot},input,{(base + 256 * slot) // t.width},NORM);"]
            golden[base : base + stride] = data[base : base + stride]
            for slot in range(7):
                part = (case.get("bit_start", 0) + slot) % (t.width * 8)
                body += [f"movvp(p{slot},a{slot},{part});"]
                if case.get("port_pressure"):
                    body += [
                        f"vector_u32 c{slot},d{slot};",
                        f"vintlv(c{slot},d{slot},(vector_u32 &)a{slot},(vector_u32 &)a{(slot + 1) % 7});",
                    ]
                # MOVVP selects a 16B/8B source block and broadcasts every bit
                # to two/four adjacent predicate bits (MaskGenWithRegTensor).
                raw = compute.encode(arrays[slot], dtype)
                block_bytes = 32 // t.width
                selected = raw[part * block_bytes : (part + 1) * block_bytes]
                expanded = [
                    bit
                    for byte in selected
                    for shift in range(8)
                    for bit in [((byte >> shift) & 1)] * t.width
                ]
                expected = sum(bit << i for i, bit in enumerate(expanded)).to_bytes(
                    32, "little"
                )
                golden[base + 32 * slot : base + 32 * (slot + 1)] = expected
            body += [
                f"psts(p{slot},(__ubuf__ uint32_t *)out,{base + 32 * slot},NORM);"
                for slot in range(7)
            ]
            count, out_bytes = 7, 224
            if case.get("port_pressure"):
                for slot in range(7):
                    left = compute.encode(arrays[slot], dtype)
                    right = compute.encode(arrays[(slot + 1) % 7], dtype)
                    raw = b"".join(
                        left[i : i + 4] + right[i : i + 4] for i in range(0, 256, 4)
                    )
                    start = base + 256 + 512 * slot
                    golden[start : start + 512] = raw
                    body += [
                        f"vsts(c{slot},(__ubuf__ uint32_t *)out,{start // 4},NORM_B32,all);",
                        f"vsts(d{slot},(__ubuf__ uint32_t *)out,{start // 4 + 64},NORM_B32,all);",
                    ]
                out_bytes = 3840
        elif api == "vsstb":
            body += [f"vector_{t.vector} r0,r1,r2,r3;"]
            for i in range(4):
                body += [f"vlds(r{i},input,{(base + 256 * i) // t.width},NORM);"]
            body += ["mem_bar(VV_ALL);"]
            config = case.get("config", (2 << 16) | 1)
            for i in range(4):
                body += [
                    f"vsstb(r{i},(__ubuf__ {t.scalar} *)(out+{base + 512 * i}),{config},all);"
                ]
                if case.get("isolated"):
                    body += ["mem_bar(VV_ALL);"]
            for i in range(4):
                for block in range(8):
                    start = (
                        base
                        + 512 * i
                        + ((config & 65535) + block * (config >> 16)) * 32
                    )
                    golden[start : start + 32] = data[
                        base + 256 * i + block * 32 : base + 256 * i + (block + 1) * 32
                    ]
            target = ["RV_VSTI", "RV_VSTS", "RV_VSSTB"]
            out_bytes = 2048
        elif api in {"paired_gather", "paired_scatter"}:
            component = "s64" if t.signed else "u64"
            body += [f"vector_2xvl_{component} r0,r1,r2,r3;", "vector_u32 idx;"]
            indices = [(i * 7 + 3) % 64 for i in range(64)]
            data[base + 2048 : base + 2304] = compute.encode(indices, "uint32")
            golden[base : base + stride] = data[base : base + stride]
            body += [f"vlds(idx,(__ubuf__ uint32_t *)input,{(base + 2048) // 4},NORM);"]
            if api == "paired_scatter":
                for i in range(4):
                    body += [
                        f"vlds(r{i},(__ubuf__ {t.scalar} *)input,{(base + 512 * i) // 8});"
                    ]
                body += ["mem_bar(VV_ALL);"]
            for i in range(4):
                start = base + 512 * i
                if api == "paired_gather":
                    body += [
                        f"vgather2(r{i},input+{start // 8},idx,p);",
                        f"vsts(r{i},(__ubuf__ {t.scalar} *)(out+{start}),0,p);",
                    ]
                    golden[start : start + 512] = b"".join(
                        data[start + 8 * j : start + 8 * (j + 1)] for j in indices
                    )
                else:
                    body += [
                        f"vscatter(r{i},(__ubuf__ {t.scalar} *)(out+{start}),idx,p);"
                    ]
                    for lane, index in enumerate(indices):
                        golden[start + 8 * index : start + 8 * (index + 1)] = data[
                            start + 8 * lane : start + 8 * (lane + 1)
                        ]
            target = ["RV_VGATHER2" if api == "paired_gather" else "RV_VSCATTER"]
            count, out_bytes = 8, 2048
        else:
            raise ValueError(api)
        name = f"probe_{number}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}"
            f"(__ubuf__ {t.scalar} *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}((__ubuf__ {t.scalar} *)input,out);")
        contracts.append(
            {
                **case,
                "form": dtype,
                "result_dtype": "fp32" if api == "vexpdif" else dtype,
                "dtype": "fp32" if api == "vexpdif" else "uint8",
                "target_kind": "compute" if api in {"vexpdif", "movvp"} else "memory",
                "target_opcodes": target,
                "target_count": count,
                "output_offset": base,
                "output_bytes": out_bytes,
                "guard_offset": base + out_bytes,
                "guard_bytes": stride - out_bytes,
                "exact_bytes": api != "vexpdif",
                "record_supporting_compute": True,
                "warm_operands": True,
                "pressure_opcode": "RV_VINTLV",
            }
        )
    size = len(data)
    source = (
        "#define __aicore__ [aicore]\n"
        + "\n".join(functions)
        + f"""
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *src,__gm__ uint8_t *dst){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,src,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,src,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(dst,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, bytes(data), bytes(golden), contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--operation",
        choices=["vexpdif", "vsstb", "paired_gather", "paired_scatter", "movvp"],
        required=True,
    )
    parser.add_argument("--dtypes", nargs="+", required=True)
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--port-pressure", action="store_true")
    parser.add_argument(
        "--block-start", "--bit-start", dest="bit_start", type=int, default=0
    )
    parser.add_argument("--part", default="PART_EVEN")
    parser.add_argument("--chain-source", choices=["lhs", "rhs"], default="lhs")
    parser.add_argument("--config", type=lambda value: int(value, 0), default=0x20001)
    parser.add_argument("--batch-size", type=int, default=12)
    parser.add_argument(
        "--cann",
        type=Path,
        default=Path(
            os.environ.get(
                "ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"
            )
        ),
    )
    args = parser.parse_args()
    cases = [
        dict(
            api=args.operation,
            dtype=dtype,
            part=args.part,
            config=args.config,
            chain_source=args.chain_source,
            bit_start=args.bit_start,
            port_pressure=args.port_pressure,
            isolated=args.isolated,
        )
        for dtype in args.dtypes
    ]
    for index in range(0, len(cases), args.batch_size):
        rows = compute.run_batch(
            cases[index : index + args.batch_size],
            args.out / f"batch_{index // args.batch_size:03}",
            args.cann,
            factory=make_batch,
        )
        print(
            json.dumps(
                {
                    "cases": len(rows),
                    "passed": sum(row["passed"] for row in rows),
                    "failures": [
                        (
                            row["api"],
                            row["form"],
                            row["mismatch_offsets"][:16],
                            row["target_count_actual"],
                        )
                        for row in rows
                        if not row["passed"]
                    ],
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
