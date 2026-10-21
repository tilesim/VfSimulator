"""A5 predicate permutations and logic with complete bitmap/guard checking."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import os

import vector_compute as compute

PAIRS = {"pintlv", "pdintlv"}
LOGIC = {"pand", "por", "pxor", "pnot", "psel"}
PERMUTE = PAIRS | {"pslide", "ppack", "punpack"}


def bits(raw):
    n = int.from_bytes(raw, "little")
    return [(n >> i) & 1 for i in range(256)]


def raw(values):
    return sum(int(x) << i for i, x in enumerate(values)).to_bytes(32, "little")


def reference(family, left, right, width, mask, part="LOWER", slide=3):
    a, b, p = bits(left), bits(right), bits(mask)
    if family in PAIRS:
        groups = [
            [a[i : i + width] for i in range(0, 256, width)],
            [b[i : i + width] for i in range(0, 256, width)],
        ]
        if family == "pintlv":
            merged = [g for pair in zip(*groups) for g in pair]
            half = 256 // width
            return raw([v for group in merged[:half] for v in group]), raw(
                [v for group in merged[half:] for v in group]
            )
        merged = groups[0] + groups[1]
        return raw([v for group in merged[::2] for v in group]), raw(
            [v for group in merged[1::2] for v in group]
        )
    if family == "pslide":
        shift = slide * width
        joined = a + b
        return raw(joined[shift : shift + 256]), b""
    if family == "ppack":
        result = a[::2]
        return raw(
            (result + [0] * 128) if part == "LOWER" else ([0] * 128 + result)
        ), b""
    if family == "punpack":
        selected = a[:128] if part == "LOWER" else a[128:]
        return raw([v for x in selected for v in (x, 0)]), b""
    op = {
        "pand": lambda x, y: x & y,
        "por": lambda x, y: x | y,
        "pxor": lambda x, y: x ^ y,
        "pnot": lambda x, y: 1 - x,
    }.get(family)
    result = (
        [x if z else y for x, y, z in zip(a, b, p)]
        if family == "psel"
        else [op(x, y) & z for x, y, z in zip(a, b, p)]
    )
    return raw(result), b""


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for index, case in enumerate(cases):
        family = case["family"]
        width = case.get("width", 1)
        form = f"b{width * 8}"
        base = index * stride
        api = family + "_" + form if family in PAIRS | {"pslide"} else family
        arrays = []
        for stream in range(5):
            values = [int(((i * 7 + stream * 3) % 11) < 5) for i in range(256 // width)]
            image = raw([bit for value in values for bit in [value] * width])
            arrays.append(image)
            data[base + 32 * stream : base + 32 * (stream + 1)] = image
        data[base + 3840 : base + 3872] = b"\xff" * 32
        golden[base : base + stride] = data[base : base + stride]
        body = [
            "vector_bool halfmask=pset_b8(PAT_H);vector_bool a0,a1,a2,a3,b,p;vector_bool r0,r1,r2,r3,r4,r5,r6,z0,z1,z2,z3,z4,z5,z6;",
            *[
                f"plds(a{i},(__ubuf__ uint32_t *)input,{base + 32 * i},NORM);"
                for i in range(4)
            ],
            f"plds(b,(__ubuf__ uint32_t *)input,{base + 128},NORM);",
            *[
                f"psts(a{i},(__ubuf__ uint32_t *)input,{base + 32 * i},NORM);"
                for i in range(4)
            ],
            f"psts(b,(__ubuf__ uint32_t *)input,{base + 128},NORM);",
            "mem_bar(VV_ALL);",
            f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
        ]
        if family in PERMUTE:
            body += [f"pand(a{i},a{i},a{i},p);" for i in range(4)] + ["pand(b,b,b,p);"]
        if case.get("port_pressure"):
            body += ["vector_u8 v0,v1,v2,v3,pressure0,pressure1;"]
            for i in range(4):
                body += [
                    f"vlds(v{i},input,{base + 512 + 256 * i},NORM);",
                    f"vor(v{i},v{i},v{i},p,MODE_ZEROING);",
                    f"vintlv(pressure0,pressure1,v{i},v{i});",
                    f"vsts(pressure0,input,{base + 2560},NORM_B8,p);",
                    f"vsts(pressure1,input,{base + 2816},NORM_B8,p);",
                ]
        outputs = []
        for slot in range(7):
            left, right, mask = f"a{slot}" if slot < 4 else "a3", "b", "p"
            lraw, rraw, mraw = (
                arrays[slot] if slot < 4 else arrays[3],
                arrays[4],
                b"\xff" * 32,
            )
            if slot in (4, 5):
                previous = slot - 1 if slot == 5 else 0
                result = case.get("producer_result", 0)
                name = ("z" if result else "r") + str(previous)
                image = outputs[previous][result]
                source = case.get("chain_source", "lhs")
                if source == "mask":
                    mask, mraw = name, image
                elif source == "rhs":
                    right, rraw = name, image
                else:
                    left, lraw = name, image
            elif slot == 6 and family not in {"ppack", "punpack", "pnot"}:
                left, right, lraw, rraw = right, left, rraw, lraw
            if slot == 6 and family in LOGIC:
                mask, mraw = "halfmask", b"\xff" * 16 + b"\x00" * 16
            part = case.get("part", "LOWER")
            if slot == 6 and family in {"ppack", "punpack"}:
                part = "HIGHER" if part == "LOWER" else "LOWER"
            if family in PAIRS:
                body.append(f"{api}(r{slot},z{slot},{left},{right});")
            elif family == "pslide":
                body.append(f"{api}(r{slot},{left},{right},{case.get('slide', 3)});")
            elif family in {"ppack", "punpack"}:
                body.append(f"{api}(r{slot},{left},{part});")
            elif family == "pnot":
                body.append(f"pnot(r{slot},{left},{mask});")
            else:
                body.append(f"{api}(r{slot},{left},{right},{mask});")
            body.append(
                f"psts(r{slot},(__ubuf__ uint32_t *)out,{base + 64 * slot},NORM);"
            )
            if family in PAIRS:
                body.append(
                    f"psts(z{slot},(__ubuf__ uint32_t *)out,{base + 64 * slot + 32},NORM);"
                )
            first, second = reference(
                family, lraw, rraw, width, mraw, part, case.get("slide", 3)
            )
            outputs.append((first, second))
            golden[base + 64 * slot : base + 64 * slot + 32] = first
            golden[base + 64 * slot + 32 : base + 64 * slot + 32 + len(second)] = second
        name = f"probe_{index}_{family}_{form}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        contracts.append(
            {
                **case,
                "api": api,
                "form": form,
                "dtype": "uint8",
                "output_offset": base,
                "output_bytes": 448,
                "guard_offset": base + 448,
                "guard_bytes": stride - 448,
                "exact_bytes": True,
                "target_count": 7,
                "target_opcodes": ["RV_" + family.upper()],
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
    return [
        {"family": name, "width": width}
        for name in sorted(PAIRS | {"pslide"})
        for width in (1, 2, 4)
    ] + [{"family": name, "width": 1} for name in sorted({"ppack", "punpack"} | LOGIC)]


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
    parser.add_argument("--families", nargs="+")
    parser.add_argument("--chain-source", choices=["lhs", "rhs", "mask"], default="lhs")
    parser.add_argument("--producer-result", type=int, choices=[0, 1], default=0)
    parser.add_argument("--port-pressure", action="store_true")
    parser.add_argument("--part", choices=["LOWER", "HIGHER"], default="LOWER")
    parser.add_argument("--slide", type=int, default=3)
    args = parser.parse_args()
    cases = [
        {
            **c,
            "chain_source": args.chain_source,
            "producer_result": args.producer_result,
            "port_pressure": args.port_pressure,
            "part": args.part,
            "slide": args.slide,
        }
        for c in cases_for()
        if not args.families or c["family"] in args.families
    ]
    if args.producer_result and any(c["family"] not in PAIRS for c in cases):
        parser.error("Second result requires PINTLV/PDINTLV")
    if args.chain_source == "rhs" and any(
        c["family"] in {"ppack", "punpack", "pnot"} for c in cases
    ):
        parser.error("Selected instruction lacks a right input")
    if args.chain_source == "mask" and any(c["family"] not in LOGIC for c in cases):
        parser.error("Selected instruction lacks a mask input")
    for index in range(0, len(cases), 16):
        rows = compute.run_batch(
            cases[index : index + 16],
            args.out / f"batch_{index // 16:03}",
            args.cann,
            factory=make_batch,
        )
        failures = [
            (r["api"], r["mismatch_offsets"][:12], r["target_count_actual"])
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
