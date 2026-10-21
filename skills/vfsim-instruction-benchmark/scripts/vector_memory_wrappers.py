"""Validate aligned/unaligned CANN vload/vstore, including paired 64-bit tails."""

from __future__ import annotations
import argparse, json, os, random, struct
from pathlib import Path
import vector_compute as compute

DTYPES = [
    "int8",
    "uint8",
    "int16",
    "uint16",
    "int32",
    "uint32",
    "fp16",
    "fp32",
    "bf16",
    "pair_int64",
    "pair_uint64",
]


def make_batch(cases):
    stride = 4096
    data = bytearray(random.Random(20261019).randbytes(stride * len(cases)))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for number, case in enumerate(cases):
        api, dt = case["api"], case["dtype"]
        pair = dt.startswith("pair_")
        scalar_dt = dt.removeprefix("pair_")
        t = compute.TYPES[scalar_dt]
        base = number * stride
        size = 512 if pair else 256
        offset = t.width if case["mode"] == "ADDRESS_UNALIGNED" else 0
        count = case.get("count", size // t.width)
        data[base + 3968 : base + 3972] = struct.pack("<I", count)
        golden[base : base + stride] = data[base : base + stride]
        vector = "2xvl_" + ("s64" if t.signed else "u64") if pair else t.vector
        body = [
            f"vector_{vector} r;",
            f"vector_bool all=pset_b{32 if pair else min(t.width * 8, 32)}(PAT_ALL);",
        ]
        if api == "vload":
            body += [f"vload(r,input+{offset // t.width},{case['mode']});"]
            body += (
                ["vsts(r,out,0,all);"]
                if pair
                else [f"vsts(r,out,0,NORM_B{t.width * 8},all);"]
            )
            golden[base : base + size] = data[base + offset : base + offset + size]
        else:
            body += (
                ["vlds(r,input,64);"]
                if pair
                else [f"vlds(r,input,{512 // t.width},NORM);"]
            )
            body += [f"vstore(out+{offset // t.width},r,count,{case['mode']});"]
            golden[base + offset : base + offset + count * t.width] = data[
                base + 512 : base + 512 + count * t.width
            ]
        name = f"probe_{number}_{api}_{dt}"
        functions += [
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ {t.scalar} *input,__ubuf__ {t.scalar} *out,uint32_t count){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        ]
        calls += [
            f"{name}((__ubuf__ {t.scalar} *)(input+{base}),(__ubuf__ {t.scalar} *)(out+{base}),*((__ubuf__ uint32_t *)(input+{base + 3968})));"
        ]
        contracts += [
            {
                **case,
                "api": "memory_wrapper_" + api,
                "public_api": api,
                "form": dt,
                "dtype": "uint8",
                "target_count": 0,
                "target_opcodes": [],
                "output_offset": base,
                "output_bytes": 1024,
                "guard_offset": base + 1024,
                "guard_bytes": stride - 1024,
                "exact_bytes": True,
                "record_supporting_compute": True,
                "count": count,
            }
        ]
    size = len(data)
    src = (
        "#define __aicore__ [aicore]\n"
        + "\n".join(functions)
        + f"""
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *inputGM,__gm__ uint8_t *outputGM){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,inputGM,0,1,{size},0,0,0,0,0,0);copy_gm_to_ubuf_align_v2(out,inputGM,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return src, bytes(data), bytes(golden), contracts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--operations", nargs="+", default=["vload", "vstore"])
    p.add_argument("--dtypes", nargs="+", default=DTYPES)
    p.add_argument("--batch-size", type=int, default=12)
    p.add_argument(
        "--cann",
        type=Path,
        default=Path(
            os.environ.get(
                "ACL_PATH", "/home/lenovo/Ascend/ascend-toolkit/cann-9.0.0-beta.1"
            )
        ),
    )
    a = p.parse_args()
    cases = []
    for op in a.operations:
        for dt in a.dtypes:
            t = compute.TYPES[dt.removeprefix("pair_")]
            n = (512 if dt.startswith("pair_") else 256) // t.width
            for mode in ("ADDRESS_ALIGNED", "ADDRESS_UNALIGNED"):
                for count in [0, 1, n // 2, n] if op == "vstore" else [n]:
                    cases.append(dict(api=op, dtype=dt, mode=mode, count=count))
    for i in range(0, len(cases), a.batch_size):
        rows = compute.run_batch(
            cases[i : i + a.batch_size],
            a.out / f"batch_{i // a.batch_size:03}",
            a.cann,
            factory=make_batch,
        )
        print(
            json.dumps(
                dict(
                    cases=len(rows),
                    passed=sum(r["passed"] for r in rows),
                    failures=[
                        (
                            r["public_api"],
                            r["form"],
                            r["mode"],
                            r["count"],
                            r["mismatch_offsets"][:12],
                        )
                        for r in rows
                        if not r["passed"]
                    ],
                )
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
