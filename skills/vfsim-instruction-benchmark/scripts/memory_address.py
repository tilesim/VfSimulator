"""Probe address-register memory APIs and VLD→VST RAW in a hardware loop."""

from __future__ import annotations
import argparse, json, os, random
from pathlib import Path
import vector_compute as compute


def make_batch(cases):
    stride = 4096
    data = random.Random(20261019).randbytes(stride * len(cases))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for number, case in enumerate(cases):
        api, dt = case["api"], case["dtype"]
        t = compute.TYPES[dt]
        w = t.width
        n = 256 // w
        base = number * stride
        body = [
            f"vector_{t.vector} r;",
            "vector_align state;",
            "vector_bool all=pset_b8(PAT_ALL);",
        ]
        if api == "vld_vst":
            # Keep the mask ready before the first target LOAD and retain the
            # warm-up chain by saving its result outside the target output.
            body += [
                "vector_u32 seed,warm;",
                "vlds(seed,(__ubuf__ uint32_t *)input,0,NORM);",
                "mem_bar(VV_ALL);",
                "warm=seed;",
            ]
            body += ["vor(warm,warm,seed,all,MODE_ZEROING);"] * (
                80 + case.get("replica", 0) % 3
            )
            body += [
                "vsts(warm,(__ubuf__ uint32_t *)out,768,NORM_B32,all);"
            ]

        if api == "vldu":
            body += ["vldas(state,input);"]
        loop = []
        stores = api in {"vst", "vstu", "vsta", "vstur", "vstar", "vsst", "vsts"}
        if stores:
            loop += [f"vlds(r,input,i*{n},NORM);", "mem_bar(VV_ALL);"]
        if api in {"vld", "vst", "vld_vst", "vlda", "vldu", "vstu", "vsta", "vsst"}:
            loop += [
                f"vector_address addr=vag_b{min(w * 8, 32)}({256 // min(w, 4) if api in {'vld', 'vst', 'vld_vst', 'vlda', 'vldu', 'vstu', 'vsta'} else 0});"
            ]
        if api == "vldas":
            loop += [f"vldas(state,input+i*{n});", f"vldus(r,state,input+i*{n});"]
        elif api == "vsts":
            loop += [f"vsts(r,out,i*{n},NORM_B{min(w * 8, 32)},all);"]
        elif api == "vld":
            loop += ["vld(r,input,addr,NORM);"]
        elif api == "vld_vst":
            loop += [
                "vld(r,input,addr,NORM);",
                f"vst(r,out,addr,NORM_B{min(w * 8, 32)},all);",
            ]
        elif api == "vlda":
            loop += ["vlda(state,input,addr);", "vldu(r,state,addr,input,0);"]
        elif api == "vldu":
            loop += [f"vldu(r,state,addr,input,{n});"]
        elif api == "vst":
            loop += [f"vst(r,out,addr,NORM_B{min(w * 8, 32)},all);"]
        elif api in {"vstu", "vsta"}:
            loop += ["vstu(state,addr,r,out,POST_UPDATE);", "vsta(state,out,addr);"]
        elif api in {"vstur", "vstar"}:
            loop += [
                f"vstur(state,r,out+i*{n},POST_UPDATE);",
                f"vstar(state,out+(i+1)*{n});",
            ]
        elif api == "vsldb":
            loop += ["vsldb(r,input,i*8,all);"]
        elif api == "vsst":
            loop += ["vsst(r,out,addr,S8_B16);"]
        else:
            raise ValueError(api)
        if not stores and api != "vld_vst":
            loop += [f"vsts(r,out,i*{n},NORM_B{min(w * 8, 32)},all);"]
        loop += ["mem_bar(VV_ALL);"]
        body += ["#pragma unroll 1", "for(int i=0;i<4;++i){", "\n".join(loop), "}"]
        if dt == "s4x2":
            body = [
                line.replace(
                    "vlds(r,input,", "vlds(r.val,(__ubuf__ int8_t *)input,"
                ).replace("vsts(r,out,", "vsts(r.val,(__ubuf__ int8_t *)out,")
                for line in body
            ]
        scalar = "int4x2_t" if dt == "s4x2" else t.scalar
        name = f"probe_{number}_{api}_{dt}"
        functions += [
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ {scalar} *input,__ubuf__ {scalar} *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        ]
        input_base = base + 1024 if api == "vld_vst" else base
        calls += [
            f"{name}((__ubuf__ {scalar} *)(input+{input_base}),(__ubuf__ {scalar} *)(out+{base}));"
        ]
        if api == "vsldb":
            for i in range(4):
                golden[base + 256 * i : base + 256 * (i + 1)] = (
                    data[base + 256 * i : base + 256 * i + 32] * 8
                )
        elif api == "vsst":
            for lane in range(128):
                golden[base + lane * 16 : base + lane * 16 + 2] = data[
                    base + 768 + 2 * lane : base + 770 + 2 * lane
                ]
        else:
            golden[base : base + 1024] = data[input_base : input_base + 1024]
        if api == "vld_vst":
            golden[base + 3072 : base + 3328] = data[input_base : input_base + 256]
        output_bytes = 3328 if api == "vld_vst" else 2048 if api == "vsst" else 1024
        opcode = api.upper()
        contracts += [
            {
                **case,
                "form": dt,
                "result_dtype": dt,
                "dtype": "uint8",
                "target_kind": "memory",
                "target_count": 8 if api == "vld_vst" else 4,
                "target_opcodes": ["RV_VLD", "RV_VST"]
                if api == "vld_vst"
                else ["RV_VSTS", "RV_VSTI"]
                if api == "vsts"
                else ["RV_" + opcode],
                "exact_bytes": True,
                "output_offset": base,
                "output_bytes": output_bytes,
                "guard_offset": base + output_bytes,
                "guard_bytes": stride - output_bytes,
                "isolated": True,
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
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return src, data, bytes(golden), contracts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--operations",
        nargs="+",
        default=[
            "vld",
            "vst",
            "vlda",
            "vldu",
            "vstu",
            "vsta",
            "vstur",
            "vstar",
            "vsldb",
            "vsst",
        ],
    )
    p.add_argument("--dtypes", nargs="+", default=["uint16"])
    p.add_argument("--replicas", type=int, default=1)
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
    cat = json.loads((compute.ROOT / "configs/instruction_catalog.json").read_text())[
        "instructions"
    ]
    cases = [
        dict(api=op, dtype=dt, replica=replica)
        for op in a.operations
        for dt in a.dtypes
        for replica in range(a.replicas)
        if dt in cat["VLD" if op == "vld_vst" else op.upper()]["forms"]
    ]
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
                            r["api"],
                            r["form"],
                            r["mismatch_offsets"][:12],
                            r["target_count_actual"],
                            r["all_opcodes"],
                        )
                        for r in rows
                        if not r["passed"] or not r["single_op_sample"]
                    ],
                )
            ),
            flush=True,
        )


if __name__ == "__main__":
    main()
