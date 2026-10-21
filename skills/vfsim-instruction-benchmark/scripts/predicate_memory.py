"""Check predicate memory distributions and byte-based pointer updates."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random

import vector_compute as compute


def transform(raw, mode):
    bits = [(byte >> bit) & 1 for byte in raw for bit in range(8)]
    selected = (
        [bit for value in bits[:128] for bit in (value, value)]
        if mode == "US"
        else bits[:512:2]
        if mode == "DS"
        else bits[:256:2]
        if mode == "PK"
        else bits[:256]
    )
    return sum(bit << index for index, bit in enumerate(selected)).to_bytes(
        len(selected) // 8, "little"
    )


def make_batch(cases):
    stride = 4096
    data = random.Random(20261019).randbytes(stride * len(cases))
    initial = bytes(value ^ 255 for value in data)
    golden = bytearray(initial)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        api, mode = case["api"], case["mode"]
        load = api.startswith("pl")
        address = api in {"pld", "pst"}
        post = case.get("post", False)
        literal = case.get("literal", False)
        base = number * stride
        body = [
            "vector_bool p;",
            f"__ubuf__ uint32_t *cursor=(__ubuf__ uint32_t *)"
            + ("input" if load else "out")
            + f"+{base // 4};",
            "#pragma unroll 1",
            "for(int i=0;i<4;++i){",
        ]
        if address:
            body.append("vector_address addr=vag_b8(64);")
        immediate_unit = (
            (16 if mode in {"US", "PK"} else 32) if api in {"pldi", "psti"} else 1
        )
        offset = (
            "addr"
            if address
            else str(64 // immediate_unit)
            if post or literal
            else "i*64"
        )
        pointer = "cursor+i*16" if literal else "cursor"
        suffix = ",POST_UPDATE" if post else ""
        if load:
            body += [
                f"{api}(p,{pointer},{offset},{mode}{suffix});",
                f"psts(p,(__ubuf__ uint32_t *)out,{base}+i*64,NORM);",
            ]
        else:
            body += [
                f"plds(p,(__ubuf__ uint32_t *)input,{base}+i*64,NORM);",
                f"{api}(p,{pointer},{offset},{mode}{suffix});",
            ]
        body += ["mem_bar(VV_ALL);", "}"]
        for i in range(4):
            offset = base + 64 * i
            source = offset + (64 if load and literal else 0)
            destination = offset + (64 if not load and literal else 0)
            expected = transform(data[source : source + 64], mode)
            golden[destination : destination + len(expected)] = expected
        name = f"probe_{number}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        targets = ["RV_" + api.upper()]
        if api in {"plds", "psts"}:
            targets.append("RV_" + ("PLDI" if load else "PSTI"))
        contracts.append(
            {
                **case,
                "form": "b8",
                "dtype": "uint8",
                "result_dtype": "bool",
                "target_kind": "memory",
                "payload_bytes": {"NORM": 32, "US": 16, "DS": 64, "PK": 16}[mode],
                "target_count": 4,
                "target_opcodes": targets,
                "output_offset": base,
                "output_bytes": 320 if literal else 256,
                "guard_offset": base + (320 if literal else 256),
                "guard_bytes": stride - (320 if literal else 256),
                "exact_bytes": True,
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
 copy_gm_to_ubuf_align_v2(out,src+{size},0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(dst,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, data + initial, bytes(golden) + bytes(size), contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--post", action="store_true")
    parser.add_argument(
        "--literal", action="store_true", help="Check immediate pre-update offsets"
    )
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
    operations = (
        ["pldi", "psti"]
        if args.literal
        else (
            ["pldi", "plds", "psti", "psts"]
            if args.post
            else ["pld", "pldi", "plds", "pst", "psti", "psts"]
        )
    )
    cases = [
        dict(api=api, mode=mode, post=args.post, literal=args.literal)
        for api in operations
        for mode in (["NORM", "US", "DS"] if api.startswith("pl") else ["NORM", "PK"])
    ]
    for i in range(0, len(cases), 12):
        rows = compute.run_batch(
            cases[i : i + 12],
            args.out / f"batch_{i // 12:03}",
            args.cann,
            factory=make_batch,
        )
        print(
            json.dumps(
                dict(
                    cases=len(rows),
                    passed=sum(row["passed"] for row in rows),
                    failures=[
                        (
                            row["api"],
                            row["mode"],
                            row["mismatch_offsets"][:8],
                            row["target_count_actual"],
                        )
                        for row in rows
                        if not row["passed"]
                    ],
                )
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
