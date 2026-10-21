"""Numerical A5 probes for common cross-opcode forwarding and issue intervals."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path

import vector_compute as compute

COMPUTE_PAIRS = (
    ("vadds", "vmul"),
    ("vmul", "vadds"),
    ("vmuls", "vexp"),
    ("vsub", "vexp"),
    ("vmax", "vsub"),
    ("vexp", "vmul"),
    ("vmul", "vadd"),
    ("vadd", "vmul"),
    ("vmuls", "vadds"),
    ("vmul", "vdiv"),
    ("vdiv", "vmul"),
    ("vmaxs", "vmul"),
    ("vabs", "vmuls"),
    ("vmins", "vmaxs"),
    ("vsqrt", "vmul"),
    ("vln", "vmul"),
    ("vadd", "vrelu"),
    ("vmul", "vrelu"),
)
MEMORY_OPS = (
    "vadd",
    "vadds",
    "vsub",
    "vmul",
    "vmuls",
    "vmax",
    "vexp",
    "vsqrt",
    "vln",
    "vdiv",
)
PREDICATE_PAIRS = (("vcmp_gt", "vsel"), ("vcmp_eq", "vsel"))


def supported(op, dtype):
    from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    return spec is not None and dtype in spec.forms and (op, dtype) != ("vmuls", "bf16")


def source_roles(op):
    if op == "vsel":
        return ["Pg"]
    if op == "vsts":
        return ["Vd"]
    return ["Vn", "Vm"] if op in compute.BINARY else ["Vn"]


def physical(op):
    if op == "vlds":
        return ["RV_VLDS", "RV_VLDI"]
    if op == "vsts":
        return ["RV_VSTS", "RV_VSTI"]
    base = "RV_" + op.upper()
    return [base, base + "_FP", base + "_INT"]


def operation(op, dst, x, y, mask, dtype):
    if op == "vsel" or op.startswith("vcmp_"):
        return f"{op}({dst},{x},{y},{mask});"
    if op in compute.UNARY:
        return f"{op}({dst},{x},{mask},MODE_ZEROING);"
    if op in compute.BINARY or op.startswith("vcmp_"):
        return f"{op}({dst},{x},{y},{mask},MODE_ZEROING);"
    value = {"vmins": 0.625, "vmaxs": 0.375}.get(op, 1.25)
    scalar = f"({compute.TYPES[dtype].scalar}){value}f"
    return f"{op}({dst},{x},{scalar},{mask},MODE_ZEROING);"


def evaluate(op, x, y, dtype):
    out = []
    for a, b in zip(x, y):
        v = {
            "vadd": lambda: a + b,
            "vadds": lambda: a + 1.25,
            "vsub": lambda: a - b,
            "vmul": lambda: a * b,
            "vmuls": lambda: a * 1.25,
            "vdiv": lambda: a / b,
            "vmax": lambda: max(a, b),
            "vmin": lambda: min(a, b),
            "vmaxs": lambda: max(a, 0.375),
            "vmins": lambda: min(a, 0.625),
            "vabs": lambda: abs(a),
            "vrelu": lambda: max(a, 0),
            "vexp": lambda: math.exp(a),
            "vln": lambda: math.log(a),
            "vsqrt": lambda: math.sqrt(a),
            "vcmp_gt": lambda: a > b,
            "vcmp_eq": lambda: a == b,
        }[op]()
        out.append(v if op.startswith("vcmp_") else compute.quantize(v, dtype))
    return out


def make_batch(cases):
    stride = 4096
    data = bytearray(stride * len(cases))
    golden = bytearray(stride * len(cases))
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        prev, cur, dtype = case["previous"], case["current"], case["dtype"]
        t = compute.TYPES[dtype]
        n = 256 // t.width
        base = number * stride
        streams = case.get("streams", 6)
        arrays = [
            [
                compute.quantize(
                    0.25 + ((lane + slot * 3 + case.get("replica", 0)) % 17) / 16, dtype
                )
                for lane in range(n)
            ]
            for slot in range(streams)
        ]
        b = [compute.quantize(0.75 + (lane % 3) / 16, dtype) for lane in range(n)]
        c = [compute.quantize(0.125 + (lane % 5) / 32, dtype) for lane in range(n)]
        for slot, values in enumerate([*arrays, b, c]):
            data[base + slot * 256 : base + (slot + 1) * 256] = compute.encode(
                values, dtype
            )
        data[base + 3840 : base + 3872] = bytes([255]) * 32
        golden[base : base + stride] = data[base : base + stride]
        declarations = [
            f"vector_bool full=pset_b{t.width * 8}(PAT_ALL),late;",
            f"vector_{t.vector} "
            + ",".join(
                [f"a{i}" for i in range(streams)]
                + ["b", "c"]
                + [f"r{i}" for i in range(streams)]
                + [f"t{i}" for i in range(streams)]
            )
            + ";",
        ]
        predicate = prev.startswith("vcmp_")
        if predicate:
            declarations.append(
                "vector_bool " + ",".join(f"p{i}" for i in range(streams)) + ";"
            )
        body = []
        for slot, name in enumerate([*[f"a{i}" for i in range(streams)], "b", "c"]):
            body.append(
                f"vlds({name},(__ubuf__ {t.scalar} *)input,{(base + slot * 256) // t.width},NORM);"
            )
        for slot, name in enumerate([*[f"a{i}" for i in range(streams)], "b", "c"]):
            body.append(
                f"vsts({name},(__ubuf__ {t.scalar} *)input,{(base + slot * 256) // t.width},NORM_B{t.width * 8},full);"
            )
        body += [
            "mem_bar(VV_ALL);",
            f"plds(late,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
        ]
        if case["kind"] == "fwd" and prev != "vlds":
            body += [
                f"vor((vector_u8 &)a{i},(vector_u8)a{i},(vector_u8)a{i},late,MODE_ZEROING);"
                for i in range(streams)
            ]
        first, second = [], []
        for i, a in enumerate(arrays):
            if prev == "vlds":
                first.append(
                    f"vlds(r{i},(__ubuf__ {t.scalar} *)input,{(base + 256 * i) // t.width},NORM);"
                )
                produced = a
            else:
                first.append(
                    operation(
                        prev,
                        f"p{i}" if predicate else f"r{i}",
                        f"a{i}",
                        "b",
                        "late" if case["kind"] == "ii" else "full",
                        dtype,
                    )
                )
                produced = evaluate(prev, a, b, dtype)
            if cur == "vsts":
                second.append(
                    f"vsts(r{i},(__ubuf__ {t.scalar} *)out,{(base + 256 * i) // t.width},NORM_B{t.width * 8},full);"
                )
                consumed = produced
            elif predicate:
                second.append(operation(cur, f"t{i}", "b", "c", f"p{i}", dtype))
                consumed = [x if p else y for p, x, y in zip(produced, b, c)]
            else:
                x = f"r{i}" if case["kind"] == "fwd" else f"a{i}"
                y = "b"
                left = produced if case["kind"] == "fwd" else a
                right = b
                if case.get("source_role") == "Vm":
                    x, y = "b", f"r{i}"
                    left, right = b, produced
                second.append(
                    operation(
                        cur,
                        f"t{i}",
                        x,
                        y,
                        "late" if case["kind"] == "ii" else "full",
                        dtype,
                    )
                )
                consumed = evaluate(cur, left, right, dtype)
            if not predicate and prev != "vlds" and cur != "vsts":
                golden[base + 256 * i : base + 256 * (i + 1)] = compute.encode(
                    produced, dtype
                )
            offset = (
                base
                + 256 * i
                + (
                    (streams * 256)
                    if not predicate and prev != "vlds" and cur != "vsts"
                    else 0
                )
            )
            golden[offset : offset + 256] = compute.encode(consumed, dtype)
        if case.get("order") == "blocks":
            body += first[:3] + second[:3] + first[3:] + second[3:]
        else:
            body += [line for pair in zip(first, second) for line in pair]
        if cur != "vsts":
            if not predicate and prev != "vlds":
                body += [
                    f"vsts(r{i},(__ubuf__ {t.scalar} *)out,{(base + 256 * i) // t.width},NORM_B{t.width * 8},full);"
                    for i in range(streams)
                ]
            offset = (streams * 256) if not predicate and prev != "vlds" else 0
            body += [
                f"vsts(t{i},(__ubuf__ {t.scalar} *)out,{(base + offset + 256 * i) // t.width},NORM_B{t.width * 8},full);"
                for i in range(streams)
            ]
        name = f"probe_{number}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(declarations + body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        target_ops = (
            physical(cur)
            if prev == "vlds"
            else physical(prev)
            if cur == "vsts"
            else physical(prev) + physical(cur)
        )
        out_bytes = (
            (streams * 256)
            if predicate or prev == "vlds" or cur == "vsts"
            else streams * 512
        )
        contracts.append(
            {
                **case,
                "api": "cross_pair",
                "form": dtype,
                "previous_key": prev.upper() + "." + dtype,
                "current_key": cur.upper() + "." + dtype,
                "target_opcodes": target_ops,
                "target_count": streams
                if prev == "vlds" or cur == "vsts"
                else streams * 2,
                "output_offset": base,
                "output_bytes": out_bytes,
                "guard_offset": base + out_bytes,
                "guard_bytes": stride - out_bytes,
                "warm_operands": True,
                "record_supporting_compute": True,
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


def cases_for(dtypes, kind, replicas=2):
    cases = []
    for dtype in dtypes:
        pairs = list(COMPUTE_PAIRS)
        if kind == "ii":
            pairs = sorted(set(pairs + [(b, a) for a, b in pairs]))
        else:
            pairs += (
                [("vlds", op) for op in MEMORY_OPS]
                + [(op, "vsts") for op in MEMORY_OPS]
                + list(PREDICATE_PAIRS)
            )
        for previous, current in pairs:
            if not all(
                op in {"vlds", "vsts"} or supported(op, dtype)
                for op in [previous, current]
            ):
                continue
            roles = source_roles(current) if kind == "fwd" else ["independent"]
            for replica in range(replicas):
                for role in roles:
                    cases.append(
                        dict(
                            previous=previous,
                            current=current,
                            dtype=dtype,
                            kind=kind,
                            source_role=role,
                            replica=replica,
                            order=(
                                "alternating"
                                if replica % 2 == 0 or kind == "fwd"
                                else "blocks"
                            ),
                        )
                    )
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--kind", choices=["fwd", "ii"], required=True)
    parser.add_argument("--dtypes", nargs="+", default=["fp32", "fp16", "bf16"])
    parser.add_argument("--pairs", nargs="+", help="producer:consumer API spellings")
    parser.add_argument("--streams", type=int, choices=[1, 4, 6], default=6)
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--batch-size", type=int, choices=range(1, 17), default=12)
    parser.add_argument("--jobs", type=int, choices=[1, 2, 3], default=1)
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
    cases = cases_for(args.dtypes, args.kind, args.replicas)
    cases = [{**case, "streams": args.streams} for case in cases]
    if args.pairs:
        cases = [c for c in cases if c["previous"] + ":" + c["current"] in args.pairs]
    if not cases:
        parser.error("No supported instruction pairs match the requested scope")
    batches = [
        cases[i : i + args.batch_size] for i in range(0, len(cases), args.batch_size)
    ]

    def run(item):
        index, batch = item
        rows = compute.run_batch(
            batch, args.out / f"batch_{index:03}", args.cann, factory=make_batch
        )
        result = dict(
            batch=index,
            cases=len(rows),
            passed=sum(r["passed"] and r["single_op_sample"] for r in rows),
            failures=[
                {
                    k: r[k]
                    for k in [
                        "previous",
                        "current",
                        "dtype",
                        "kind",
                        "source_role",
                        "mismatch_offsets",
                        "target_count_actual",
                        "target_count",
                    ]
                }
                for r in rows
                if not (r["passed"] and r["single_op_sample"])
            ],
        )
        print(json.dumps(result), flush=True)
        return result

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        results = list(pool.map(run, enumerate(batches)))
    if any(r["passed"] != r["cases"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
