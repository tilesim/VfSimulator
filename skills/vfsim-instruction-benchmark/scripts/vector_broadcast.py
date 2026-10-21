"""Numerical and timing probes for broadcast, immediate, index and histograms."""

from __future__ import annotations
import argparse, json, os
from pathlib import Path
import vector_compute as compute
from vector_misc import bitmap


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for index, case in enumerate(cases):
        api, dtype = case["api"], case["dtype"]
        t = compute.TYPES[dtype]
        w = t.width
        n = 256 // w
        base = index * stride
        hist = api in {"dhistv2", "chistv2"}
        source_type = compute.TYPES["uint8"] if hist else t
        arrays = []
        for stream in range(6):
            values = [
                compute.quantize(
                    (i * 7 + stream * 11) % 43 - 17
                    if t.signed or dtype in compute.FLOATING
                    else (i * 7 + stream * 11) % 43,
                    dtype,
                )
                for i in range(n)
            ]
            image = compute.encode(values, dtype)
            arrays.append(image)
            data[base + 256 * stream : base + 256 * (stream + 1)] = image
        if hist:
            arrays[4] = bytes((i * 73 + 11) % 256 for i in range(256))
            arrays[5] = bytes((i * 29 + 3) % 256 for i in range(256))
            data[base + 1024 : base + 1536] = arrays[4] + arrays[5]
        pred = bitmap(
            1 if hist else w, [i for i in range(256 if hist else n) if i % 5 != 2]
        )
        inv = bytes(x ^ 255 for x in pred)
        data[base + 3808 : base + 3840] = pred
        data[base + 3840 : base + 3872] = b"\xff" * 32
        scalar_values = [compute.quantize(i + 3, dtype) for i in range(7)]
        for i, v in enumerate(scalar_values):
            data[base + 2048 + 8 * i : base + 2048 + 8 * i + w] = compute.encode(
                [v], dtype
            )
        golden[base : base + stride] = data[base : base + stride]
        body = [
            f"vector_{t.vector} a0,a1,a2,a3,r0,r1,r2,r3,r4,r5,r6;",
            f"vector_{source_type.vector} b,c;",
            "vector_bool full=pset_b8(PAT_ALL),p,q;",
            *[
                f"vlds((vector_u8 &)a{i},input,{base + 256 * i},NORM);"
                for i in range(4)
            ],
            f"vlds((vector_u8 &)b,input,{base + 1024},NORM);",
            f"vlds((vector_u8 &)c,input,{base + 1280},NORM);",
            *[
                f"vsts((vector_u8)a{i % 4},input,{base + 256 * i},NORM_B8,full);"
                for i in range(4)
            ],
            "mem_bar(VV_ALL);",
            f"plds(p,(__ubuf__ uint32_t *)input,{base + 3808},NORM);",
            "pnot(q,p,full);",
        ]
        if case.get("port_pressure"):
            body += ["vector_u8 pressure0,pressure1;"]
            for i in range(20):
                left, right = (
                    ["a0", "a1", "a2", "a3", "b", "c"][i % 6],
                    ["a0", "a1", "a2", "a3", "b", "c"][i // 6],
                )
                body += [
                    f"vintlv(pressure0,pressure1,(vector_u8){left},(vector_u8){right});",
                    f"vsts(pressure0,input,{base + 2304},NORM_B8,full);",
                    f"vsts(pressure1,input,{base + 2560},NORM_B8,full);",
                ]
        results = []
        deferred_stores = []
        for slot in range(7):
            src = f"a{slot}" if slot < 4 else "a3"
            raw = arrays[slot] if slot < 4 else arrays[3]
            old = src
            oldraw = raw
            mask, maskraw = ("p", pred) if slot != 6 else ("q", inv)
            mode = case.get("mode", "MODE_ZEROING")
            pos = case.get("position", "POS_LOWEST")
            if slot in (4, 5):
                prev = 0 if slot == 4 else 4
                if case.get("chain_source") == "old":
                    old = f"r{prev}"
                    oldraw = results[prev]
                    src = "b" if slot == 4 else "c"
                    raw = arrays[4 if slot == 4 else 5]
                    mask, maskraw = ("q", inv) if slot == 4 else ("p", pred)
                elif api not in {
                    "vbr",
                    "vci",
                    "vdups",
                    "vdupi_s8",
                    "vdupi_u8",
                    "vdupi_s16",
                    "vdupi_u16",
                }:
                    src = f"r{prev}"
                    raw = results[prev]
            active = [
                bool(
                    int.from_bytes(maskraw, "little") & (1 << (i * (1 if hist else w)))
                )
                for i in range(256 if hist else n)
            ]
            if hist:
                # Bin N selects a 128-value interval; values are unsigned bytes.
                src = "b" if slot % 2 == 0 else "c"
                raw = arrays[4 if slot % 2 == 0 else 5]
                old = f"a{slot}" if slot < 4 else f"r{0 if slot == 4 else slot - 1}"
                oldraw = (
                    arrays[slot] if slot < 4 else results[0 if slot == 4 else slot - 1]
                )
                if slot in (4, 5) and case.get("chain_source") == "src":
                    prev = 0 if slot == 4 else 4
                    src = f"(vector_u8)r{prev}"
                    raw = results[prev]
                    old = "a3"
                    oldraw = arrays[3]
                bn = case.get("bin", 0) & 1
                counts = [
                    sum(
                        enabled and value == bn * 128 + i
                        for value, enabled in zip(raw, active)
                    )
                    for i in range(128)
                ]
                if api == "chistv2":
                    counts = [
                        sum(
                            enabled and value <= bn * 128 + i
                            for value, enabled in zip(raw, active)
                        )
                        for i in range(128)
                    ]
                expected = compute.encode(
                    [
                        compute.quantize(x + y, "uint16")
                        for x, y in zip(compute.decode(oldraw, "uint16"), counts)
                    ],
                    "uint16",
                )
                body += [f"r{slot}={old};", f"{api}(r{slot},{src},{mask},Bin_N{bn});"]
            elif api.startswith("vdupi_"):
                value = case.get("immediate_base", 7) + slot * 17
                expected = compute.encode([compute.quantize(value, dtype)] * n, dtype)
                body += [f"{api}(r{slot},({t.scalar}){value});"]
            elif api == "vci":
                values = [
                    compute.quantize(
                        scalar_values[slot]
                        + (i if case.get("order", "INC_ORDER") == "INC_ORDER" else -i),
                        dtype,
                    )
                    for i in range(n)
                ]
                expected = compute.encode(values, dtype)
                body += [f"vci(r{slot},s{slot},{case.get('order', 'INC_ORDER')});"]
            else:
                if api in {"vbr", "vdups"}:
                    element = compute.encode([scalar_values[slot]], dtype)
                else:
                    lane = n - 1 if pos == "POS_HIGHEST" else 0
                    element = raw[lane * w : (lane + 1) * w]
                expected = (
                    element * n
                    if api == "vbr"
                    else b"".join(
                        element
                        if active[i]
                        else oldraw[i * w : (i + 1) * w]
                        if mode == "MODE_MERGING"
                        else b"\0" * w
                        for i in range(n)
                    )
                )
                body += [f"r{slot}={old};"] if mode == "MODE_MERGING" else []
                if api == "vbr":
                    body += [f"vbr(r{slot},s{slot});"]
                elif api == "vdups":
                    body += [f"vdup(r{slot},s{slot},{mask},{mode});"]
                else:
                    body += [f"vdup(r{slot},{src},{mask},{pos},{mode});"]
            storemask = "full"
            if api == "vdups" and dtype == "bf16" and mode == "MODE_MERGING":
                storemask = mask
                expected = b"".join(
                    expected[i * w : (i + 1) * w]
                    if active[i]
                    else golden[
                        base + 256 * slot + i * w : base + 256 * slot + (i + 1) * w
                    ]
                    for i in range(n)
                )
            store = (
                f"vsts(r{slot},(__ubuf__ {t.scalar} *)out,{(base + 256 * slot) // w},NORM_B{w * 8},{storemask});"
                if storemask != "full"
                else f"vsts((vector_u8)r{slot},out,{base + 256 * slot},NORM_B8,full);"
            )
            (deferred_stores if case.get("defer_stores") else body).append(store)
            results.append(expected)
            golden[base + 256 * slot : base + 256 * (slot + 1)] = expected
        body += deferred_stores
        name = f"probe_{index}_{api}_{dtype}"
        params = ",".join(f"{t.scalar} s{i}" for i in range(7))
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,{params}){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        setup = [
            f"{t.scalar} s{index}_{i}=*((__ubuf__ {t.scalar} *)(input+{base + 2048 + 8 * i}));"
            for i in range(7)
        ]
        calls += setup + [
            f"{name}(input,out," + ",".join(f"s{index}_{i}" for i in range(7)) + ");"
        ]
        target = "RV_" + (
            "VDUPS"
            if api == "vdups"
            else "VDUPM"
            if api == "vdup" and pos == "POS_HIGHEST"
            else "VDUPI"
            if api.startswith("vdupi")
            else api.upper()
        )
        if hist:
            target = "RV_" + api.upper().replace("V2", "v2")
        physical = api
        physical += "_highest" if api == "vdup" and pos == "POS_HIGHEST" else ""
        physical += (
            "_merge" if api in {"vdup", "vdups"} and mode == "MODE_MERGING" else ""
        )
        contracts.append(
            {
                **case,
                "api": physical,
                "public_api": api,
                "form": dtype,
                "result_dtype": dtype,
                "dtype": "uint8",
                "target_count": 14 if api.startswith("vdupi") and w == 2 else 7,
                "target_opcodes": [target],
                "output_offset": base,
                "output_bytes": 1792,
                "guard_offset": base + 1792,
                "guard_bytes": stride - 1792,
                "exact_bytes": True,
                "warm_operands": True,
                "reads_old_destination": hist or mode == "MODE_MERGING",
                "pressure_opcode": "RV_VINTLV",
            }
        )
    size = len(data)
    source = (
        "#define __aicore__ [aicore]\n"
        + "\n".join(functions)
        + f"""
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *inputGM,__gm__ uint8_t *outputGM){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,inputGM,0,1,{size},0,0,0,0,0,0);
 copy_gm_to_ubuf_align_v2(out,inputGM,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
 copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, bytes(data), bytes(golden), contracts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--immediate-base", type=lambda x: int(x, 0), default=7)
    p.add_argument("--defer-stores", action="store_true")
    p.add_argument("--all-variants", action="store_true")
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--operations", nargs="+", default=["vbr", "vdup", "vdups", "vci"])
    p.add_argument("--dtypes", nargs="+", default=["int32"])
    p.add_argument("--position", default="POS_LOWEST")
    p.add_argument("--mode", default="MODE_ZEROING")
    p.add_argument("--chain-source", default="src")
    p.add_argument("--bin", type=int, default=0)
    p.add_argument("--order", default="INC_ORDER")
    p.add_argument("--port-pressure", action="store_true")
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
    cases = [
        dict(
            api=op,
            dtype=dt,
            position=a.position,
            mode=a.mode,
            chain_source=a.chain_source,
            bin=a.bin,
            order=a.order,
            port_pressure=a.port_pressure,
            immediate_base=a.immediate_base,
            defer_stores=a.defer_stores,
        )
        for op in a.operations
        for dt in a.dtypes
    ]
    if a.all_variants:
        cases = []
        for op in a.operations:
            for dt in a.dtypes:
                for pos in (
                    ("POS_LOWEST", "POS_HIGHEST") if op == "vdup" else ("POS_LOWEST",)
                ):
                    for mode in ("MODE_ZEROING", "MODE_MERGING"):
                        for chain in (
                            ("src", "old")
                            if mode == "MODE_MERGING" and op == "vdup"
                            else ("src",)
                        ):
                            cases.append(
                                dict(
                                    api=op,
                                    dtype=dt,
                                    position=pos,
                                    mode=mode,
                                    chain_source=chain,
                                    port_pressure=True,
                                )
                            )
    cases = [
        case
        for case in cases
        if not case["api"].startswith("vdupi_")
        or case["dtype"]
        == ("uint" if case["api"][6] == "u" else "int") + case["api"][7:]
    ]
    if not cases:
        p.error("Selected dtype does not match the immediate API")
    for i in range(0, len(cases), a.batch_size):
        rows = compute.run_batch(
            cases[i : i + a.batch_size],
            a.out / f"batch_{i // a.batch_size:03}",
            a.cann,
            factory=make_batch,
        )
        failed = [
            (
                r["api"],
                r["form"],
                r["mismatch_offsets"][:12],
                r["target_count_actual"],
                r["all_opcodes"],
            )
            for r in rows
            if not r["passed"] or not r["single_op_sample"]
        ]
        print(
            json.dumps(
                dict(
                    cases=len(rows),
                    passed=sum(r["passed"] for r in rows),
                    failures=failed,
                )
            ),
            flush=True,
        )
        if failed:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
