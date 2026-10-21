"""Validate A5 typed memory operations and register-indexed LSU dependencies."""

from __future__ import annotations

import argparse
import json
import os
import random
import struct
from pathlib import Path

import vector_compute as compute


def make_batch(cases):
    stride = 4096
    data = bytearray(random.Random(20261019).randbytes(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for number, case in enumerate(cases):
        api, dtype, base = case["api"], case["dtype"], number * stride
        t = compute.TYPES[dtype]
        width, lanes = t.width, 256 // t.width
        output_type = (
            compute.TYPES[{"int8": "int16", "uint8": "uint16"}.get(dtype, dtype)]
            if api == "vgather2"
            else t
        )
        indices_dtype = (
            "uint32" if width >= 4 or api in {"vgatherb", "vgather2_bc"} else "uint16"
        )
        it = compute.TYPES[indices_dtype]
        count = 64 if api == "vgather2_bc" else min(lanes, 256 // it.width)
        index_sets = []
        for stream in range(4):
            values = [((i * 7 + stream * 3) % count) for i in range(256 // it.width)]
            if api == "vgatherb":
                values = [32 * ((i * 3 + stream) % 8) for i in range(64)]
            image = compute.encode(values, indices_dtype)
            data[base + 2048 + 256 * stream : base + 2304 + 256 * stream] = image
            index_sets.append(values)
        data[base + 3840 : base + 3872] = b"\xff" * 32
        data[base + 3968 : base + 3972] = struct.pack("<I", lanes)
        golden[base : base + stride] = data[base : base + stride]
        body = [
            f"vector_{output_type.vector} r0,r1,r2,r3;",
            "vector_bool full=pset_b8(PAT_ALL),p;",
            f"__ubuf__ {t.scalar} *src=(__ubuf__ {t.scalar} *)input;",
            f"__ubuf__ {t.scalar} *dst=(__ubuf__ {t.scalar} *)out;",
        ]
        indexed = api in {"vgather2", "vgather2_bc", "vgatherb", "vscatter"}
        if indexed:
            body += [f"vector_{it.vector} idx0,idx1,idx2,idx3;"]
            for i in range(4):
                body += [
                    f"vlds(idx{i},(__ubuf__ {it.scalar} *)input,{(base + 2048 + 256 * i) // it.width},NORM);",
                    f"vsts((vector_u8)idx{i},input,{base + 2048 + 256 * i},NORM_B8,full);",
                ]
            body += [
                "mem_bar(VV_ALL);",
                f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
            ]
            body += [
                f"vor((vector_u8 &)idx{i},(vector_u8)idx{i},(vector_u8)idx{i},p,MODE_ZEROING);"
                for i in range(4)
            ]
        if api in {
            "vscatter",
            "vstus",
            "vstui",
            "vst",
            "vstu",
            "vsta",
            "vstar",
            "vstur",
            "vsst",
        }:
            for i in range(4):
                body += [
                    f"vlds((vector_u8 &)r{i},input,{base + 256 * i},NORM);",
                    f"vsts((vector_u8)r{i},input,{base + 256 * i},NORM_B8,full);",
                ]
            body += ["mem_bar(VV_ALL);"]
        if api in {
            "vldus",
            "vldui",
            "vstus",
            "vstui",
            "vlda",
            "vldu",
            "vstu",
            "vsta",
            "vstar",
            "vstur",
        }:
            body += ["vector_align state0,state1,state2,state3;"]
        for i in range(1 if case.get("single") else 4):
            image = bytes(data[base + 256 * i : base + 256 * (i + 1)])
            source = f"src+{(base + 256 * i) // width}"
            target = f"dst+{(base + 256 * i) // width}"
            expected = image
            if api in {"vlda", "vldu"}:
                body += [
                    f"vector_address addr{i}=vag_b{min(width * 8, 32)}(0);",
                    f"vlda(state{i},{source},addr{i});",
                    f"vldu(r{i},state{i},addr{i},{source},{lanes});",
                ]
            elif api in {"vstu", "vsta", "vstar", "vstur"}:
                if api not in {"vstur", "vstar"}:
                    body += [f"vector_address addr{i}=vag_b{min(width * 8, 32)}(0);"]
                if api in {"vstur", "vstar"}:
                    body += [
                        f"vstus(state{i},{lanes},r{i},{target});",
                        f"vstur(state{i},r{i},{target},POST_UPDATE);",
                        f"vstar(state{i},{target}+{lanes});",
                    ]
                else:
                    body += [
                        f"vstu(state{i},addr{i},r{i},{target},POST_UPDATE);",
                        f"vsta(state{i},{target},addr{i});",
                    ]
            elif api == "vld":
                body += [f"vld(r{i},{source},vag_b{min(width * 8, 32)}(0),NORM);"]
            elif api == "vst":
                body += [
                    f"vst(r{i},{target},vag_b{min(width * 8, 32)}(0),NORM_B{min(width * 8, 32)},full);"
                ]
            elif api == "vsldb":
                body += [f"vsldb(r{i},{source},0,full);"]
                expected = image[:width] * lanes
            elif api == "vsst":
                body += [
                    f"vsst(r{i},dst+{base // width},vag_b{min(width * 8, 32)}(0),S8_B16);",
                    "mem_bar(VV_ALL);",
                ]
                if i == 3:
                    for block in range(16):
                        golden[base + 128 * block : base + 128 * block + 16] = image[
                            16 * block : 16 * block + 16
                        ]
                expected = None
            elif api in {"vldus", "vldui"}:
                if case.get("variable"):
                    body += [
                        f"__ubuf__ {t.scalar} *read{i}={source};",
                        f"vldas(state{i},read{i});",
                        f"{api}(r{i},state{i},read{i},count,POST_UPDATE);",
                    ]
                else:
                    body += [
                        f"vldas(state{i},{source});",
                        f"{api}(r{i},state{i},{source});",
                    ]
            elif api == "vlds":
                body += [f"vlds(r{i},{source},0,NORM);"]
            elif api in {"vstus", "vstui"}:
                count = "count" if case.get("variable") else str(lanes)
                body += [
                    f"{api}(state{i},{count},r{i},{target});",
                    f"vstas(state{i},{target},{count});",
                ]
            elif api in {"vgather2", "vgather2_bc", "vgatherb"}:
                mask = "" if api == "vgatherb" and case.get("unmasked") else ",p"
                body += [f"{api}(r{i},{source},idx{i}{mask});"]
                if api == "vgatherb":
                    expected = b"".join(image[k : k + 32] for k in index_sets[i][:8])
                else:
                    chunks = []
                    for value in index_sets[i][:count]:
                        item = image[value * width : (value + 1) * width]
                        chunks.append(
                            item
                            + b"\0"
                            * (
                                max(2, output_type.width) - width
                                if api == "vgather2"
                                else 0
                            )
                        )
                        if api == "vgather2_bc" and width == 2:
                            chunks[-1] += b"\0\0"
                    expected = b"".join(chunks)
            elif api == "vscatter":
                body += [f"vscatter(r{i},{target},idx{i},p);"]
                selected = bytearray(golden[base + 256 * i : base + 256 * (i + 1)])
                for lane, value in enumerate(index_sets[i][:count]):
                    src_lane = 2 * lane if width == 1 else lane
                    selected[value * width : (value + 1) * width] = image[
                        src_lane * width : (src_lane + 1) * width
                    ]
                expected = bytes(selected)
            else:
                raise ValueError(api)
            if api not in {
                "vscatter",
                "vstus",
                "vstui",
                "vst",
                "vstu",
                "vsta",
                "vstar",
                "vstur",
                "vsst",
            }:
                body += [f"vsts((vector_u8)r{i},out,{base + 256 * i},NORM_B8,full);"]
            if case.get("isolated"):
                body += ["mem_bar(VV_ALL);"]
            if expected is not None:
                golden[base + 256 * i : base + 256 * (i + 1)] = expected
        name = f"probe_{number}_{api}_{dtype}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out,uint32_t count){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(
            f"{name}(input,out,*((__ubuf__ uint32_t *)(input+{base + 3968})));"
        )
        targets = {
            "vlds": ["RV_VLDS", "RV_VLDI"],
            "vldus": ["RV_VLDUS", "RV_VLDUI"],
            "vldui": ["RV_VLDUI"],
            "vstus": ["RV_VSTUS", "RV_VSTUI"],
            "vstui": ["RV_VSTUI"],
            "vlda": ["RV_VLDA"],
            "vldu": ["RV_VLDU"],
            "vsta": ["RV_VSTA"],
            "vstar": ["RV_VSTAR", "RV_VSTA"],
            "vstur": ["RV_VSTUR"],
        }.get(api, ["RV_" + api.upper()])
        contracts.append(
            {
                **case,
                "form": dtype,
                "result_dtype": {"int8": "int16", "uint8": "uint16"}.get(dtype, dtype)
                if api == "vgather2"
                else dtype,
                "target_kind": "memory",
                "target_count": 1 if case.get("single") else 4,
                "target_opcodes": targets,
                "output_offset": base,
                "output_bytes": 2048 if api == "vsst" else 1024,
                "dtype": "uint8",
                "guard_offset": base + (2048 if api == "vsst" else 1024),
                "guard_bytes": stride - (2048 if api == "vsst" else 1024),
                "exact_bytes": True,
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
 set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);
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
    parser.add_argument(
        "--operations",
        nargs="+",
        default=[
            "vlds",
            "vldus",
            "vldui",
            "vstus",
            "vstui",
            "vgather2",
            "vgather2_bc",
            "vgatherb",
            "vscatter",
        ],
    )
    parser.add_argument("--dtypes", nargs="+")
    parser.add_argument("--single", action="store_true")
    parser.add_argument("--unmasked", action="store_true")
    parser.add_argument("--isolated", action="store_true")
    parser.add_argument("--variable", action="store_true")
    parser.add_argument("--batch-size", type=int, choices=range(1, 17), default=16)
    args = parser.parse_args()
    catalog = json.loads(
        (compute.ROOT / "configs/instruction_catalog.json").read_text()
    )["instructions"]
    cases = [
        {
            "api": op,
            "dtype": dtype,
            "unmasked": args.unmasked,
            "isolated": args.isolated,
            "variable": args.variable,
            "single": args.single,
        }
        for op in args.operations
        for dtype in catalog[op.upper()]["forms"]
        if dtype in compute.TYPES
        and dtype != "s4x2"
        and not (args.unmasked and op == "vgatherb" and dtype == "uint64")
        and (not args.dtypes or dtype in args.dtypes)
    ]
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
                r["form"],
                r["mismatch_offsets"][:8],
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
