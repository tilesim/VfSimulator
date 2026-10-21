"""A5 register gather, squeeze, integral rounding and masked move probes."""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path

import vector_compute as compute


def bitmap(width, lanes):
    return sum(1 << (i * width) for i in lanes).to_bytes(32, "little")


def round_integral(x, mode):
    if mode == "ROUND_R":
        return round(x)
    if mode == "ROUND_A":
        return math.copysign(math.floor(abs(x) + 0.5), x)
    return {"ROUND_F": math.floor, "ROUND_C": math.ceil, "ROUND_Z": math.trunc}[mode](x)


def reference(api, source, indices, old, predicate, dtype, rounding="ROUND_R"):
    width = compute.TYPES[dtype].width
    lanes = 256 // width
    active = [
        bool(int.from_bytes(predicate, "little") & (1 << (i * width)))
        for i in range(lanes)
    ]
    if api == "vmov":
        return source
    if api == "vmov_merge":
        return b"".join(
            (source if active[i] else old)[i * width : (i + 1) * width]
            for i in range(lanes)
        )
    if api in {"vselr", "vselrv2"}:
        values = [
            int.from_bytes(indices[i * width : (i + 1) * width], "little")
            for i in range(lanes)
        ]
        return b"".join(
            source[(n % lanes) * width : (n % lanes + 1) * width] for n in values
        )
    if api == "vsqz":
        values = [
            source[i * width : (i + 1) * width] for i in range(lanes) if active[i]
        ]
        return b"".join(values) + b"\0" * (256 - len(values) * width)
    if api == "vusqz":
        values = []
        total = 0
        for i, enabled in enumerate(active):
            values.append(compute.quantize(total, dtype))
            if enabled:
                total += 1
        return compute.encode(values, dtype)
    if api == "vtrc":
        values = compute.decode(source, dtype)
        return compute.encode(
            [
                round_integral(v, rounding) if active[i] else 0
                for i, v in enumerate(values)
            ],
            dtype,
        )
    raise ValueError(api)


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for index, case in enumerate(cases):
        api, dtype = case["api"], case["dtype"]
        t = compute.TYPES[dtype]
        width, lanes, base = t.width, 256 // t.width, stride * index
        arrays = []
        for stream in range(6):
            if api == "vtrc":
                values = [(i % 11 - 5) + (0.5 if i % 2 else 0.25) for i in range(lanes)]
            elif api == "vusqz":
                values = [0] * lanes
            elif dtype in compute.FLOATING:
                values = [
                    0 if stream == 0 else ((i + stream * 3) % 23 - 11)
                    for i in range(lanes)
                ]
            else:
                values = [
                    0 if stream == 0 else ((i * 3 + stream * 7) % 31)
                    for i in range(lanes)
                ]
            raw = compute.encode(values, dtype)
            arrays.append(raw)
            data[base + 256 * stream : base + 256 * (stream + 1)] = raw
        index_dtype = {1: "uint8", 2: "uint16", 4: "uint32", 8: "uint32"}[width]
        index_type = compute.TYPES[index_dtype]
        idxraw = compute.encode(
            [(i * 7 + 3) % lanes for i in range(lanes)], index_dtype
        )
        data[base + 1536 : base + 1792] = idxraw
        selected = [i for i in range(lanes) if (i * 7) % 11 < 5]
        gate = b"\xff" * 32
        pred = bitmap(width, selected)
        data[base + 3808 : base + 3840] = pred
        data[base + 3840 : base + 3872] = gate
        golden[base : base + stride] = data[base : base + stride]
        body = [
            f"vector_{t.vector} a0,a1,a2,a3,b,c,r0,r1,r2,r3,r4,r5,r6;",
            f"vector_{index_type.vector} idx;",
            "vector_bool full=pset_b8(PAT_ALL),p,pattern,inverse;",
            *[
                f"vlds((vector_u8 &)a{i},input,{base + 256 * i},NORM);"
                for i in range(4)
            ],
            f"vlds((vector_u8 &)b,input,{base + 1024},NORM);",
            f"vlds((vector_u8 &)c,input,{base + 1280},NORM);",
            f"vlds((vector_u8 &)idx,input,{base + 1536},NORM);",
            *[
                f"vsts((vector_u8)a{i},input,{base + 256 * i},NORM_B8,full);"
                for i in range(4)
            ],
            f"vsts((vector_u8)b,input,{base + 1024},NORM_B8,full);",
            f"vsts((vector_u8)c,input,{base + 1280},NORM_B8,full);",
            "mem_bar(VV_ALL);",
            f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
            f"plds(pattern,(__ubuf__ uint32_t *)input,{base + 3808},NORM);",
            "pnot(inverse,pattern,p);",
        ]
        body += [
            f"vor((vector_u8 &)a{i},(vector_u8)a{i},(vector_u8)a{i},p,MODE_ZEROING);"
            for i in range(4)
        ]
        body += [
            "vor((vector_u8 &)b,(vector_u8)b,(vector_u8)b,p,MODE_ZEROING);",
            "vor((vector_u8 &)c,(vector_u8)c,(vector_u8)c,p,MODE_ZEROING);",
        ]
        if case.get("wait_for_sources"):
            body += [
                f"vsts((vector_u8)a{i},input,{base + 256 * i},NORM_B8,full);"
                for i in range(4)
            ]
            body += [
                f"vsts((vector_u8)b,input,{base + 1024},NORM_B8,full);",
                f"vsts((vector_u8)c,input,{base + 1280},NORM_B8,full);",
                "mem_bar(VV_ALL);",
            ]
        if case.get("port_pressure"):
            body += ["vector_u8 pressure0,pressure1;"]
            for i in range(4):
                body += [
                    f"vintlv(pressure0,pressure1,(vector_u8)a{i},(vector_u8)a{i});",
                    f"vsts(pressure0,input,{base + 2048},NORM_B8,full);",
                    f"vsts(pressure1,input,{base + 2304},NORM_B8,full);",
                ]
        outputs = []
        for slot in range(7):
            source = f"a{slot}" if slot < 4 else "a3"
            image = arrays[slot] if slot < 4 else arrays[3]
            old, oldraw = source, image
            indices, indexraw = "idx", idxraw
            mask, maskraw = "pattern", pred
            target = f"r{slot}"
            rounding = case.get("round", "ROUND_R")
            if api == "vtrc" and case.get("independent_rounds") and slot < 4:
                source, image = "a1", arrays[1]
                rounding = ("ROUND_R", "ROUND_A", "ROUND_F", "ROUND_C")[slot]
            if slot in (4, 5) and api != "vusqz":
                previous = slot - 1 if slot == 5 else 0
                chain = f"r{previous}"
                if case.get("chain_source", "src") == "indices":
                    indices, indexraw = (
                        f"(vector_{index_type.vector}){chain}",
                        outputs[previous],
                    )
                elif (
                    api in {"vmov_merge", "vusqz"} and case.get("chain_source") == "old"
                ):
                    old, oldraw = chain, outputs[previous]
                else:
                    source, image = chain, outputs[previous]
            if api == "vmov":
                if slot == 6:
                    source, image = "b", arrays[4]
                body += [f"vmov({target},{source});"]
            elif api == "vmov_merge":
                # Different new values keep the old-destination RAW observable.
                if case.get("chain_source", "src") == "old" or slot < 4:
                    source, image = ("b", arrays[4]) if slot != 5 else ("c", arrays[5])
                if slot in (4, 6):
                    mask, maskraw = "inverse", bytes(x ^ 255 for x in pred)
                if slot == 6:
                    source, image = "c", arrays[5]
                body += [f"{target}={old};", f"vmov({target},{source},{mask});"]
            elif api == "vusqz":
                if slot in (4, 5):
                    old, oldraw = ("b", arrays[4]) if slot == 4 else ("c", arrays[5])
                    mask, maskraw = "inverse", bytes(x ^ 255 for x in pred)
                if slot == 6:
                    mask, maskraw = "p", gate
                body += [f"{target}={old};", f"vusqz({target},{mask});"]
            elif api == "vtrc":
                if slot == 6:
                    mask, maskraw = "p", gate
                body += [f"vtrc({target},{source},{rounding},{mask},MODE_ZEROING);"]
            elif api == "vsqz":
                if slot == 6:
                    mask, maskraw = "p", gate
                body += [f"vsqz({target},{source},{mask},MODE_NO_STORED);"]
            else:
                if slot == 6:
                    source, image = "b", arrays[4]
                body += [f"{api}({target},{source},{indices});"]
            body += [f"vsts((vector_u8){target},out,{base + 256 * slot},NORM_B8,full);"]
            result = reference(api, image, indexraw, oldraw, maskraw, dtype, rounding)
            outputs.append(result)
            golden[base + 256 * slot : base + 256 * (slot + 1)] = result
        name = f"probe_{index}_{api}_{dtype}"
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
                "form": dtype,
                "result_dtype": dtype,
                "output_offset": base,
                "output_bytes": 1792,
                "guard_offset": base + 1792,
                "guard_bytes": stride - 1792,
                "exact_bytes": True,
                "target_opcodes": ["RV_VMOV", "RV_VSEL"]
                if api == "vmov_merge"
                else ["RV_" + api.upper()],
                "target_count": 7,
                "reads_old_destination_opcodes": ["RV_VMOV"]
                if api == "vmov_merge"
                else ["RV_VUSQZ"]
                if api == "vusqz"
                else [],
                "pressure_opcode": "RV_VINTLV",
                "warm_operands": True,
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


def cases_for():
    catalog = json.loads(
        (compute.ROOT / "configs/instruction_catalog.json").read_text()
    )["instructions"]
    return [
        {"api": api, "dtype": dtype}
        for api in ("vselr", "vselrv2", "vsqz", "vusqz", "vtrc", "vmov_merge", "vmov")
        for dtype in catalog[api.upper()]["forms"]
        if dtype in compute.TYPES
    ]


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
    parser.add_argument("--operations", nargs="+")
    parser.add_argument("--dtypes", nargs="+")
    parser.add_argument(
        "--chain-source", choices=["src", "indices", "old"], default="src"
    )
    parser.add_argument(
        "--round",
        choices=["ROUND_R", "ROUND_A", "ROUND_F", "ROUND_C", "ROUND_Z"],
        default="ROUND_R",
    )
    parser.add_argument("--port-pressure", action="store_true")
    parser.add_argument("--wait-for-sources", action="store_true")
    parser.add_argument("--independent-rounds", action="store_true")
    parser.add_argument("--batch-size", type=int, choices=range(1, 17), default=16)
    args = parser.parse_args()
    cases = [
        {
            **c,
            "chain_source": args.chain_source,
            "round": args.round,
            "port_pressure": args.port_pressure,
            "wait_for_sources": args.wait_for_sources,
            "independent_rounds": args.independent_rounds,
        }
        for c in cases_for()
        if (not args.operations or c["api"] in args.operations)
        and (not args.dtypes or c["dtype"] in args.dtypes)
    ]
    if not cases:
        parser.error("No selected cases")
    for index in range(0, len(cases), args.batch_size):
        rows = compute.run_batch(
            cases[index : index + args.batch_size],
            args.out / f"batch_{index // args.batch_size:03}",
            args.cann,
            factory=make_batch,
        )
        failures = [
            (
                r["api"],
                r["dtype"],
                r["mismatch_offsets"][:12],
                r["target_count_actual"],
                r["all_opcodes"],
            )
            for r in rows
            if not r["passed"] or not r["single_op_sample"]
        ]
        print(
            json.dumps(
                {
                    "cases": len(rows),
                    "passed": sum(r["passed"] for r in rows),
                    "failures": failures,
                }
            ),
            flush=True,
        )
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
