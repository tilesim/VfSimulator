"""Independent byte references for CANN 3101 aligned memory distributions."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import random

import vector_compute as compute


def chunks(data, width):
    return [data[i : i + width] for i in range(0, len(data), width)]


def load_layout(data, mode):
    if mode == "BDINTLV" or mode.startswith("DINTLV"):
        width = 32 if mode == "BDINTLV" else int(mode.split("_B")[1]) // 8
        items = chunks(data[:512], width)
        return [b"".join(items[::2]), b"".join(items[1::2])]
    if mode.startswith("BRC_"):
        width = int(mode.split("_B")[1]) // 8
        return [data[:width] * (256 // width)]
    if mode == "BLK":
        return [data[:32] * 8]
    if mode.startswith("E2B_"):
        width = int(mode.split("_B")[1]) // 8
        return [
            b"".join(item * (32 // width) for item in chunks(data[: 8 * width], width))
        ]
    if mode.startswith(("UNPK_", "UNPK4_")):
        width = int(mode.split("_B")[1]) // 8
        factor = 4 if mode.startswith("UNPK4") else 2
        return [
            b"".join(
                item + bytes(width * (factor - 1))
                for item in chunks(data[: 256 // factor], width)
            )
        ]
    if mode.startswith("US_"):
        width = int(mode.split("_B")[1]) // 8
        return [b"".join(item * 2 for item in chunks(data[:128], width))]
    if mode.startswith("DS_"):
        width = int(mode.split("_B")[1]) // 8
        return [b"".join(chunks(data[:512], width)[::2])]
    if mode.startswith("SPLT"):
        channels = 4 if mode.startswith("SPLT4") else 2
        width = int(mode.split("_B")[1]) // 8
        result = []
        for block in chunks(data[:256], 32):
            planes = [
                chunks(block[i * 32 // channels : (i + 1) * 32 // channels], width)
                for i in range(channels)
            ]
            result.extend(item for row in zip(*planes) for item in row)
        return [b"".join(result)]
    raise ValueError(mode)


def store_layout(first, second, mode):
    if mode.startswith("ONEPT_"):
        return first[: int(mode.split("_B")[1]) // 8]
    if mode.startswith(("PK_", "PK4_")):
        width = int(mode.split("_B")[1]) // 8
        factor = 4 if mode.startswith("PK4") else 2
        return b"".join(item[: width // factor] for item in chunks(first, width))
    if mode.startswith("INTLV_"):
        width = int(mode.split("_B")[1]) // 8
        return b"".join(
            left + right
            for left, right in zip(chunks(first, width), chunks(second, width))
        )
    if mode.startswith("MRG"):
        channels = 4 if mode.startswith("MRG4") else 2
        width = int(mode.split("_B")[1]) // 8
        result = []
        for block in chunks(first, 32):
            items = chunks(block, width)
            result.extend(
                item for channel in range(channels) for item in items[channel::channels]
            )
        return b"".join(result)
    raise ValueError(mode)


def make_batch(cases):
    stride = 4096
    data = random.Random(cases[0].get("seed", 20261019)).randbytes(stride * len(cases))
    initial = bytes(value ^ 255 for value in data)
    golden = bytearray(initial)
    functions, calls, contracts = [], [], []
    catalog = json.loads(
        (compute.ROOT / "configs/instruction_catalog.json").read_text()
    )["instructions"]
    for number, case in enumerate(cases):
        api, mode, dtype = case["api"], case["mode"], case["dtype"]
        t, base = compute.TYPES[dtype], number * stride
        load = api in {"vlds", "vld"}
        address = api in {"vld", "vst"}
        pair = mode == "BDINTLV" or mode.startswith(("DINTLV_", "INTLV_"))
        body = [f"vector_{t.vector} first,second;", "vector_bool all=pset_b8(PAT_ALL);"]
        loop_body = []
        for iteration in range(4):
            offset = base + 512 * iteration
            code_offset = f"({base}+512*i)" if address else str(offset)
            statements = []
            if load:
                args = "first,second" if pair else "first"
                if address:
                    statements.append(
                        f"vector_address addr=vag_b{min(t.width, 4) * 8}({512 // min(t.width, 4)});"
                    )
                    statements.append(
                        f"vld({args},input+{base // t.width},addr,{mode});"
                    )
                else:
                    statements.append(f"vlds({args},input,{offset // t.width},{mode});")
                for index, expected in enumerate(
                    load_layout(data[offset : offset + 512], mode)
                ):
                    destination = f"{code_offset}+{256 * index}"
                    if case.get("inverse_roundtrip") and mode.startswith("SPLT"):
                        statements.append(
                            f"vsts(first,(__ubuf__ {t.scalar} *)out,({code_offset})/{t.width},{mode.replace('SPLT', 'MRG')},all);"
                        )
                        expected = data[offset : offset + 256]
                    else:
                        statements.append(
                            f"vsts((vector_u8){'first' if index == 0 else 'second'},out,{destination},NORM_B8,all);"
                        )
                    golden[offset + 256 * index : offset + 256 * index + 256] = expected
            else:
                load_mode = (
                    mode.replace("MRG", "SPLT")
                    if case.get("inverse_roundtrip") and mode.startswith("MRG")
                    else "NORM"
                )
                statements += [
                    f"vlds(first,input,({code_offset})/{t.width},{load_mode});"
                ]
                if pair:
                    statements += [
                        f"vlds(second,input,({code_offset}+256)/{t.width},NORM);"
                    ]
                args = "first,second" if pair else "first"
                if address:
                    statements.append(
                        f"vector_address addr=vag_b{min(t.width, 4) * 8}({512 // min(t.width, 4)});"
                    )
                    statements.append(
                        f"vst({args},(__ubuf__ {t.scalar} *)out+{base // t.width},addr,{mode},all);"
                    )
                else:
                    statements.append(
                        f"vsts({args},(__ubuf__ {t.scalar} *)out,{offset // t.width},{mode},all);"
                    )
                expected = store_layout(
                    data[offset : offset + 256], data[offset + 256 : offset + 512], mode
                )
                if case.get("inverse_roundtrip") and mode.startswith("MRG"):
                    expected = data[offset : offset + 256]
                golden[offset : offset + len(expected)] = expected
            if case.get("isolated"):
                statements.append("mem_bar(VV_ALL);")
            if not address:
                body.extend(statements)
            elif iteration == 0:
                loop_body = statements
        if address:
            body += ["#pragma unroll 1", "for(int i=0;i<4;++i){", *loop_body, "}"]
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
                "result_dtype": dtype,
                "dtype": "uint8",
                "target_kind": "memory",
                "target_opcodes": ["RV_" + api.upper()]
                if address
                else ["RV_VLDS", "RV_VLDI"]
                if load
                else ["RV_VSTS", "RV_VSTI"],
                "target_count": 4,
                "output_offset": base,
                "output_bytes": 2048,
                "guard_offset": base + 2048,
                "guard_bytes": 2048,
                "exact_bytes": True,
                "payload_bytes": catalog[api.upper()]["memory_modes"][mode][
                    "ub_transfer_bytes"
                ],
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
    parser.add_argument("--operations", nargs="+", default=["vlds", "vsts"])
    parser.add_argument("--modes", nargs="+")
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--inverse-roundtrip", action="store_true")
    parser.add_argument("--seed", type=int, default=20261019)
    parser.add_argument("--batch-size", type=int, default=8)
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
    catalog = json.loads(
        (compute.ROOT / "configs/instruction_catalog.json").read_text()
    )["instructions"]
    cases = []
    for api in args.operations:
        for mode in catalog[api.upper()]["memory_modes"]:
            if args.modes and mode not in args.modes:
                continue
            suffix = int(mode.split("_B")[1]) if "_B" in mode else 8
            dtype = "int64" if suffix == 64 else "uint" + str(suffix)
            cases.append(
                dict(
                    api=api,
                    mode=mode,
                    dtype=dtype,
                    isolated=args.isolated,
                    inverse_roundtrip=args.inverse_roundtrip,
                    seed=args.seed,
                )
            )
    for index in range(0, len(cases), args.batch_size):
        rows = compute.run_batch(
            cases[index : index + args.batch_size],
            args.out / f"batch_{index // args.batch_size:03}",
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
                            row["mode"],
                            row["mismatch_offsets"][:12],
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
