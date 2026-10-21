"""Validate A5 predicate pattern and scalar-count mask interfaces."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct

import vector_compute as compute


def mask(width, count):
    return sum(
        1 << (i * width) for i in range(min(256 // width, max(0, count)))
    ).to_bytes(32, "little")


def pattern(width, name):
    lanes = 256 // width
    if name.startswith("PAT_VL"):
        return mask(width, int(name[6:]))
    enabled = {
        "PAT_ALL": lambda i: True,
        "PAT_ALLF": lambda i: False,
        "PAT_H": lambda i: i < lanes // 2,
        "PAT_Q": lambda i: i < lanes // 4,
        "PAT_M3": lambda i: i % 3 == 0,
        "PAT_M4": lambda i: i % 4 == 0,
    }[name]
    return sum(1 << (i * width) for i in range(lanes) if enabled(i)).to_bytes(
        32, "little"
    )


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for index, case in enumerate(cases):
        family, width, base = case["family"], case["width"], index * stride
        api = (
            family
            if family in {"pld", "pldi", "pst", "psti"}
            else f"{family}_b{8 * width}"
        )
        count = 256 // width
        values = [0, 1, count - 1, count, count + 5, 3 * count + 17, 7 * count + 3]
        starts = [0, 0, 0, 1, 1, 2, 7]
        for i, value in enumerate(values):
            data[base + 2048 + 4 * i : base + 2052 + 4 * i] = struct.pack("<I", value)
        golden[base : base + stride] = data[base : base + stride]
        body = ["vector_bool p0,p1,p2,p3,p4,p5,p6;"]
        remaining = []
        patterns = [
            case.get("pattern", "PAT_ALL"),
            "PAT_H",
            "PAT_Q",
            "PAT_M3",
            "PAT_M4",
            "PAT_VL3",
            "PAT_ALLF",
        ]
        for slot in range(7):
            if family in {"pset", "pge"}:
                body.append(f"p{slot}={api}({patterns[slot]});")
                expected = pattern(width, patterns[slot])
            elif family == "plt":
                body.append(f"p{slot}={api}(s{slot},POST_UPDATE);")
                expected = mask(width, values[slot])
                golden[base + 512 + 32 * slot : base + 516 + 32 * slot] = struct.pack(
                    "<I", max(0, values[slot] - count)
                )
                remaining += [
                    f"vector_u32 left{slot};vbr(left{slot},s{slot});",
                    f"vsts(left{slot},(__ubuf__ uint32_t *)out,{(base + 512 + 32 * slot) // 4},NORM_B32,one);",
                ]
            else:
                body.append(f"p{slot}={api}((uint16_t){starts[slot]},s{slot});")
                expected = mask(width, values[slot] - starts[slot] * count)
            body.append(
                f"psts(p{slot},(__ubuf__ uint32_t *)out,{base + 32 * slot},NORM);"
            )
            golden[base + 32 * slot : base + 32 * (slot + 1)] = expected
        if family in {"pld", "pldi", "pst", "psti"}:
            body = ["vector_bool p;", "#pragma unroll 1", "for(int i=0;i<7;++i){"]
            if family.startswith("pld"):
                body += [
                    "vector_address addr=vag_b8(32);" if family == "pld" else "",
                    f"{family}(p,(__ubuf__ uint32_t *)input,"
                    + ("addr" if family == "pld" else "i*32")
                    + ",NORM);",
                    "psts(p,(__ubuf__ uint32_t *)out,i*32,NORM);",
                ]
            else:
                body += [
                    "plds(p,(__ubuf__ uint32_t *)input,i*32,NORM);",
                    "vector_address addr=vag_b8(32);" if family == "pst" else "",
                    f"{family}(p,(__ubuf__ uint32_t *)out,"
                    + ("addr" if family == "pst" else "i*32")
                    + ",NORM);",
                ]
            body += ["mem_bar(VV_ALL);", "}"]
            golden[base : base + 224] = data[base : base + 224]
        if remaining:
            body.append("vector_bool one=pset_b32(PAT_VL1);")
            body += remaining
        name = f"probe_{index}_{api}"
        parameters = ",".join(f"uint32_t &s{i}" for i in range(7))
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,{parameters}){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        setup = [
            f"uint32_t s{index}_{i}=*((__ubuf__ uint32_t *)(input+{base + 2048 + 4 * i}));"
            for i in range(7)
        ]
        setup.append(
            f"{name}(input,out," + ",".join(f"s{index}_{i}" for i in range(7)) + ");"
        )
        if family in {"pld", "pldi", "pst", "psti"}:
            setup[-1] = setup[-1].replace("(input,out,", f"(input+{base},out+{base},")
        calls += setup
        contracts.append(
            {
                **case,
                "api": api,
                "form": f"b{width * 8}",
                "dtype": "uint8",
                "result_dtype": "bool",
                "target_count": 7,
                "target_opcodes": [
                    "RV_PSET" if family in {"pset", "pge"} else "RV_" + family.upper()
                ],
                "output_offset": base,
                "output_bytes": 1024,
                "guard_offset": base + 1024,
                "guard_bytes": stride - 1024,
                "exact_bytes": True,
                "warm_operands": True,
                "target_kind": "memory"
                if family in {"pld", "pldi", "pst", "psti"}
                else "compute",
                "patterns": patterns if family in {"pset", "pge"} else [],
                "counts": values,
                "starts": starts,
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
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 set_flag(PIPE_S,PIPE_MTE3,(event_t)1);wait_flag(PIPE_S,PIPE_MTE3,(event_t)1);
 copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, bytes(data), bytes(golden), contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--cann",
        type=Path,
        default=Path(
            os.environ.get(
                "ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"
            )
        ),
    )
    parser.add_argument("--families", nargs="+", default=["pset", "pge", "plt", "pltm"])
    args = parser.parse_args()
    cases = [
        {"family": family, "width": width}
        for family in args.families
        for width in ((1,) if family in {"pld", "pldi", "pst", "psti"} else (1, 2, 4))
    ]
    rows = compute.run_batch(
        cases, args.out / "batch_000", args.cann, factory=make_batch
    )
    print(
        json.dumps(
            {
                "cases": len(rows),
                "passed": sum(r["passed"] for r in rows),
                "failures": [
                    (
                        r["api"],
                        r["mismatch_offsets"][:16],
                        r["target_count_actual"],
                        r["all_opcodes"],
                    )
                    for r in rows
                    if not r["passed"] or not r["single_op_sample"]
                ],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
