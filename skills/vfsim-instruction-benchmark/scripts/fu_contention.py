"""Numerically checked, independent FP32 streams for FU contention studies."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

import instruction_pairs as pairs
import vector_compute as compute
import vector_structural as structural


PATTERNS = {
    "vcmax_self": ("vcmax",),
    "vadd_self": ("vadd",),
    "vexp_self": ("vexp",),
    "vmul_self": ("vmul",),
    "vdiv_self": ("vdiv",),
    "vcmax_vadd_vcmax": ("vcmax", "vadd", "vcmax"),
    "vadd_vcmax_vadd": ("vadd", "vcmax", "vadd"),
    "vexp_vcmax_vexp": ("vexp", "vcmax", "vexp"),
    "vcmax_vexp_vcmax": ("vcmax", "vexp", "vcmax"),
    "vadd_vexp_vadd": ("vadd", "vexp", "vadd"),
    "vexp_vadd_vexp": ("vexp", "vadd", "vexp"),
    "vadd_vmul_vadd": ("vadd", "vmul", "vadd"),
    "vmul_vadd_vmul": ("vmul", "vadd", "vmul"),
    "vexp_vdiv_vexp": ("vexp", "vdiv", "vexp"),
    "vdiv_vexp_vdiv": ("vdiv", "vexp", "vdiv"),
    "vexp_vdiv_vexp_paired": ("vexp", "vexp", "vdiv", "vdiv", "vexp", "vexp"),
    "vdiv_vexp_vdiv_paired": ("vdiv", "vdiv", "vexp", "vexp", "vdiv", "vdiv"),
    "vcmax_then_vadd": ("vcmax",) + ("vadd",) * 17,
    "vadd_then_vcmax": ("vadd",) * 17 + ("vcmax",),
    "vcmax_then_vexp": ("vcmax",) + ("vexp",) * 17,
    "vexp_then_vcmax": ("vexp",) * 17 + ("vcmax",),
    "vexp_then_vdiv": ("vexp",) + ("vdiv",) * 17,
    "vdiv_then_vexp": ("vdiv",) + ("vexp",) * 17,
}


def cases_for(names=None, replicas=2, count=18):
    selected = names or list(PATTERNS)
    return [
        dict(
            name=name,
            dtype="fp32",
            replica=replica,
            sequence=[PATTERNS[name][i % len(PATTERNS[name])] for i in range(count)],
        )
        for replica in range(replicas)
        for name in selected
    ]


def make_batch(cases):
    stride = 8192
    data = bytearray((i * 37 + 19) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        base = stride * number
        sequence = case["sequence"]
        count = len(sequence)
        arrays = []
        for slot in range(count):
            # Distinct loads prevent common-subexpression elimination. All
            # operands are positive, finite and exactly representable in FP32.
            values = [
                0.25 + ((lane * 7 + slot * 3 + case["replica"]) % 59) / 64
                for lane in range(64)
            ]
            values[(slot * 5 + case["replica"]) % 64] = 1.5 + slot / 64
            arrays.append(values)
            data[base + slot * 256 : base + (slot + 1) * 256] = compute.encode(
                values, "fp32"
            )
        other = [0.75 + (lane % 5) / 32 for lane in range(64)]
        data[base + count * 256 : base + (count + 1) * 256] = compute.encode(
            other, "fp32"
        )
        data[base + 7936 : base + 7968] = bytes([255]) * 32
        golden[base : base + stride] = data[base : base + stride]
        body = [
            "vector_bool full=pset_b32(PAT_ALL),two=pset_b32(PAT_VL2),late;",
            "vector_f32 " + ",".join([f"a{i}" for i in range(count)] + ["b"]) + ";",
            "vector_f32 " + ",".join(f"r{i}" for i in range(count)) + ";",
        ]
        for slot, name in enumerate([*[f"a{i}" for i in range(count)], "b"]):
            body.append(
                f"vlds({name},(__ubuf__ float *)input,{(base + slot * 256) // 4},NORM);"
            )
        for slot, name in enumerate([*[f"a{i}" for i in range(count)], "b"]):
            body.append(
                f"vsts({name},(__ubuf__ float *)input,{(base + slot * 256) // 4},NORM_B32,full);"
            )
        body += [
            "mem_bar(VV_ALL);",
            f"plds(late,(__ubuf__ uint32_t *)input,{base + 7936},NORM);",
        ]
        # Four predicate inversions retain the full mask while giving IDU time
        # to dispatch the independent target stream before the gate opens.
        body += ["pnot(late,late,full);"] * case.get("gate_chain", 0)
        # Keep the original full predicate for the first two target groups.
        # Delaying the final pair prevents OoO issue from merging the two
        # outer opcode groups before the middle group gets to execute.
        tail_chain = case.get("tail_gate_chain", 0)
        if tail_chain:
            if tail_chain < 2 or tail_chain % 2:
                raise ValueError("Tail predicate gate requires a positive even chain")
            body += ["vector_bool tail;", "pnot(tail,late,full);"]
            body += ["pnot(tail,tail,full);"] * (tail_chain - 1)
        exact = []
        for slot, op in enumerate(sequence):
            predicate = "tail" if tail_chain and slot >= count - 2 else "late"
            if op == "vcmax":
                body.append(f"vcmax(r{slot},a{slot},{predicate},MODE_ZEROING);")
                result = structural.reduce_result(
                    op, compute.encode(arrays[slot], "fp32"), "fp32", 64
                )
                # Both the maximum and its raw integer index are exact.
                exact.append([base + slot * 256, 256])
            else:
                body.append(
                    pairs.operation(op, f"r{slot}", f"a{slot}", "b", predicate, "fp32")
                )
                result = compute.encode(
                    pairs.evaluate(op, arrays[slot], other, "fp32"), "fp32"
                )
            golden[base + slot * 256 : base + slot * 256 + len(result)] = result
        for slot, op in enumerate(sequence):
            mask = "two" if op == "vcmax" else "full"
            body.append(
                f"vsts(r{slot},(__ubuf__ float *)out,{(base + slot * 256) // 4},NORM_B32,{mask});"
            )
        name = f"probe_{number}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        contracts.append(
            dict(
                case,
                api="fu_contention",
                form="fp32",
                output_offset=base,
                output_bytes=count * 256,
                guard_offset=base + count * 256,
                guard_bytes=stride - count * 256,
                exact_regions=exact,
                target_opcodes=sorted(
                    {x for op in sequence for x in pairs.physical(op)}
                ),
                target_count=count,
                record_supporting_compute=True,
                warm_operands=True,
            )
        )
    size = len(data)
    if size > 65536:
        raise ValueError("Batch input must fit below the separate output UB window")
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


def analyze(work):
    contracts = json.loads((work / "cases.json").read_text())
    golden = (work / "golden.bin").read_bytes()
    actual = (work / "output.bin").read_bytes()
    rows = compute.analyze(work, contracts, golden)
    for row in rows:
        target_ids = {e["id"] for e in row.get("events", [])}
        row["independent_targets"] = not any(
            e["producer_id"] in target_ids and e["consumer_id"] in target_ids
            for e in row.get("forwarding_observations", [])
        )
        row["gate_chain_actual"] = sum(
            e["op"] == "RV_PNOT" for e in row.get("supporting_compute_events", [])
        )
        row["gate_chain_passed"] = row["gate_chain_actual"] == (
            row.get("gate_chain", 0) + row.get("tail_gate_chain", 0)
        )
        row["exact_regions_passed"] = all(
            actual[start : start + size] == golden[start : start + size]
            for start, size in row["exact_regions"]
        )
        row["passed"] &= (
            row["exact_regions_passed"]
            and row["independent_targets"]
            and row["gate_chain_passed"]
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--names", nargs="+", choices=list(PATTERNS))
    parser.add_argument("--replicas", type=int, default=2)
    parser.add_argument("--count", type=int, choices=[6, 12, 18, 24], default=18)
    parser.add_argument("--gate-chain", type=int, choices=[0, 4], default=0)
    parser.add_argument(
        "--tail-gate-chain",
        type=int,
        choices=[0, 2, 4, 6, 8, 10],
        default=0,
        help="Delay only the final two target predicates using even PNOT chains",
    )
    parser.add_argument("--batch-size", type=int, choices=range(1, 9), default=8)
    parser.add_argument("--jobs", type=int, choices=[1, 2], default=1)
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
    cases = cases_for(args.names, args.replicas, args.count)
    cases = [
        dict(case, gate_chain=args.gate_chain, tail_gate_chain=args.tail_gate_chain)
        for case in cases
    ]
    if not cases:
        parser.error("At least one replica is required")
    batches = [
        cases[i : i + args.batch_size] for i in range(0, len(cases), args.batch_size)
    ]

    def run(item):
        index, batch = item
        work = args.out / f"batch_{index:03}"
        compute.run_batch(batch, work, args.cann, factory=make_batch)
        rows = analyze(work)
        (work / "validation.json").write_text(json.dumps(rows, indent=2) + "\n")
        failures = [
            dict(
                name=r["name"],
                replica=r["replica"],
                mismatch_offsets=r["mismatch_offsets"],
                exact_regions_passed=r["exact_regions_passed"],
                target_count_actual=r["target_count_actual"],
            )
            for r in rows
            if not (r["passed"] and r["single_op_sample"])
        ]
        print(
            json.dumps(
                dict(
                    batch=index,
                    cases=len(rows),
                    passed=len(rows) - len(failures),
                    failures=failures,
                )
            ),
            flush=True,
        )
        if failures:
            raise ValueError("Numerical or physical-instruction validation failed")

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(run, enumerate(batches)))


if __name__ == "__main__":
    main()
