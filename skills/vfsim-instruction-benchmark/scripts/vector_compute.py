"""Batch A5 ordinary-vector probes with independent scalar golden results.

Each case has a separate VF and output window. The trace records the actual
instruction count; a compiled expansion cannot silently become a single-op
timing sample. Raw runs are retained under the requested output directory.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from api.common.symbols import normalize_dtype


@dataclass(frozen=True)
class Type:
    vector: str
    scalar: str
    width: int
    fmt: str
    signed: bool = False


TYPES = {
    "fp32": Type("f32", "float", 4, "f"),
    "fp16": Type("f16", "half", 2, "e"),
    "bf16": Type("bf16", "bfloat16_t", 2, "bf16"),
    "int8": Type("s8", "int8_t", 1, "b", True),
    "uint8": Type("u8", "uint8_t", 1, "B"),
    "int16": Type("s16", "int16_t", 2, "h", True),
    "uint16": Type("u16", "uint16_t", 2, "H"),
    "int32": Type("s32", "int32_t", 4, "i", True),
    "uint32": Type("u32", "uint32_t", 4, "I"),
    "int64": Type("s64", "int64_t", 8, "q", True),
    "uint64": Type("u64", "uint64_t", 8, "Q"),
    "f8e4m3": Type("f8e4m3", "float8_e4m3_t", 1, "B"),
    "f8e5m2": Type("f8e5m2", "float8_e5m2_t", 1, "B"),
    "f8e8m0": Type("f8e8m0", "float8_e8m0_t", 1, "B"),
    "hif8": Type("hif8", "hifloat8_t", 1, "B"),
    "f4e1m2x2": Type("f4e1m2x2", "float4_e1m2x2_t", 1, "B"),
    "f4e2m1x2": Type("f4e2m1x2", "float4_e2m1x2_t", 1, "B"),
    "s4x2": Type("s4x2", "int8_t", 1, "B", True),
}
UNARY = {"vabs", "vneg", "vrelu", "vnot", "vexp", "vsqrt", "vln"}
BINARY = {
    "vadd",
    "vsub",
    "vmul",
    "vdiv",
    "vmax",
    "vmin",
    "vand",
    "vor",
    "vxor",
    "vabsdif",
    "vprelu",
    "vsadd",
    "vssub",
    "vshl",
    "vshr",
}
SCALAR = {"vadds", "vmuls", "vmaxs", "vmins", "vshls", "vshrs", "vlrelu", "vsadds"}
ACCUMULATE = {"vmula", "vmadd", "vaxpy"}
COMPARE = {
    prefix + condition
    for prefix in ("vcmp_", "vcmps_")
    for condition in ("eq", "ne", "gt", "ge", "lt", "le")
}
BITWISE = {"vand", "vor", "vxor", "vnot"}
FLOATING = {"fp32", "fp16", "bf16"}
SFU = {"vexp", "vsqrt", "vln", "vdiv"}
EVENT = re.compile(
    r"\[info\] \[(\d+)\].*?\(PC:\s*0x([0-9a-fA-F]+)\).*?\(ID:\s*(\d+)\)\s+(RV_\w+)(.*)"
)
PERF = re.compile(
    r"\[PERF\] \[(\d+)\].*instr_name (RV_\w+).*instr_id (\d+).*exu_id:(\d+)"
)
REGISTERS = re.compile(r"\b([VP])([dnmg])\[(\d+)\]")
CONTROL_POLLING = {"RV_SEND", "RV_SMEM_BAR"}


def target_opcode(api, opcode):
    # CAModel uses arithmetic-domain suffixes for NEG/ABS and some ADD forms.
    base = "RV_" + api.upper()
    aliases = {"vshls": {"RV_VSHLI"}, "vshrs": {"RV_VSHRI"}}
    return (
        opcode == base
        or opcode in {base + "_FP", base + "_INT"}
        or opcode in aliases.get(api, set())
    )


def encode(values, dtype):
    t = TYPES[dtype]
    if t.fmt == "bf16":
        bits = []
        for x in values:
            u = struct.unpack("<I", struct.pack("<f", float(x)))[0]
            bits.append(((u + 0x7FFF + ((u >> 16) & 1)) >> 16) & 0xFFFF)
        return struct.pack(f"<{len(bits)}H", *bits)
    return struct.pack(f"<{len(values)}{t.fmt}", *values)


def decode(data, dtype):
    t = TYPES[dtype]
    if t.fmt == "bf16":
        return [
            struct.unpack("<f", struct.pack("<I", x << 16))[0]
            for x in struct.unpack(f"<{len(data) // 2}H", data)
        ]
    return list(struct.unpack(f"<{len(data) // t.width}{t.fmt}", data))


def quantize(value, dtype):
    t = TYPES[dtype]
    if dtype in FLOATING:
        try:
            return decode(encode([value], dtype), dtype)[0]
        except (OverflowError, struct.error):
            return math.copysign(math.inf, value)
    value = int(value) & ((1 << (8 * t.width)) - 1)
    if t.signed and value >= 1 << (8 * t.width - 1):
        value -= 1 << (8 * t.width)
    return value


def evaluate(op, a, b, c, scalar, dtype):
    if op in BITWISE:
        aa = int.from_bytes(encode([a], dtype), "little")
        bb = int.from_bytes(encode([b], dtype), "little")
        value = {
            "vand": lambda: aa & bb,
            "vor": lambda: aa | bb,
            "vxor": lambda: aa ^ bb,
            "vnot": lambda: ~aa,
        }[op]()
        raw = (value & ((1 << (8 * TYPES[dtype].width)) - 1)).to_bytes(
            TYPES[dtype].width, "little"
        )
        return decode(raw, dtype)[0]
    formulas = {
        "vabs": lambda: abs(a),
        "vneg": lambda: -a,
        "vrelu": lambda: max(a, 0),
        "vadd": lambda: a + b,
        "vsub": lambda: a - b,
        "vmul": lambda: a * b,
        "vmax": lambda: max(a, b),
        "vmin": lambda: min(a, b),
        "vdiv": lambda: a / b,
        "vabsdif": lambda: abs(a - b),
        "vprelu": lambda: a if a >= 0 else a * b,
        "vadds": lambda: a + scalar,
        "vmuls": lambda: a * scalar,
        "vmaxs": lambda: max(a, scalar),
        "vmins": lambda: min(a, scalar),
        "vlrelu": lambda: a if a >= 0 else a * scalar,
        "vmula": lambda: a + b * c,
        "vmadd": lambda: a * b + c,
        "vaxpy": lambda: a + b * scalar,
        "vshl": lambda: int(a) << int(b),
        "vshr": lambda: int(a) >> int(b),
        "vshls": lambda: int(a) << int(scalar),
        "vshrs": lambda: int(a) >> int(scalar),
        "vsqrt": lambda: math.sqrt(a),
        "vln": lambda: math.log(a),
        "vexp": lambda: math.exp(a),
        "vsadd": lambda: max(-32768, min(32767, int(a) + int(b))),
        "vssub": lambda: max(-32768, min(32767, int(a) - int(b))),
        "vsadds": lambda: max(-32768, min(32767, int(a) + int(scalar))),
    }
    try:
        return quantize(formulas[op](), dtype)
    except ZeroDivisionError:
        return math.nan if a == 0 else math.copysign(math.inf, a * math.copysign(1, b))
    except ValueError:
        return math.nan
    except OverflowError:
        return math.inf


def call(op, target, rhs, other, scalar, mask):
    if op in UNARY:
        operands = [target, target, mask, "MODE_ZEROING"]
    elif op in SCALAR:
        operands = [target, target, scalar, mask, "MODE_ZEROING"]
    elif op == "vaxpy":
        operands = [target, rhs, scalar, mask, "MODE_ZEROING"]
    elif op in ("vmula", "vmadd"):
        operands = [target, rhs, other, mask, "MODE_ZEROING"]
    else:
        operands = [target, target, rhs, mask, "MODE_ZEROING"]
    return op + "(" + ",".join(operands) + ");"


def compare_case(op, dtype, base, arrays, data):
    t = TYPES[dtype]
    lanes = 256 // t.width
    body = [
        f"vector_bool p=pset_b{8 * t.width}(PAT_ALL),q0,q1,q2,q3;",
        f"vector_{t.vector} a0,a1,a2,a3,b;",
        f"__ubuf__ {t.scalar} *src=(__ubuf__ {t.scalar} *)input;",
        "__ubuf__ uint32_t *bits=(__ubuf__ uint32_t *)out;",
    ]
    body += [f"vlds(a{i},src,{(base + 256 * i) // t.width},NORM);" for i in range(4)]
    body += [f"vlds(b,src,{(base + 1024) // t.width},NORM);"]
    scalar = f"({t.scalar})1"
    rhs = scalar if op.startswith("vcmps_") else "b"
    body += [f"{op}(q{i},a{i},{rhs},p);" for i in range(4)]
    body += [f"psts(q{i},bits,{base + 32 * i},NORM);" for i in range(4)]
    body += [
        f"{op}(q0,a0,{rhs},q0);psts(q0,bits,{base + 128},NORM);",
        f"{op}(q0,a0,{rhs},q0);psts(q0,bits,{base + 160},NORM);",
        f"vector_bool halfmask=pset_b{8 * t.width}(PAT_VL{lanes // 2});",
        f"{op}(q3,a3,{rhs},halfmask);psts(q3,bits,{base + 192},NORM);",
    ]

    def comparison(a, b):
        return {
            "eq": a == b,
            "ne": a != b,
            "gt": a > b,
            "ge": a >= b,
            "lt": a < b,
            "le": a <= b,
        }[op.rsplit("_", 1)[1]]

    values = [
        [
            comparison(a, 1 if op.startswith("vcmps_") else b)
            for a, b in zip(arrays[i], arrays[4])
        ]
        for i in range(4)
    ]
    result = bytearray(data[base : base + 1792])
    rows = [
        *values,
        values[0],
        values[0],
        values[3][: lanes // 2] + [False] * (lanes // 2),
    ]
    for i, row in enumerate(rows):
        bits = sum(1 << (lane * t.width) for lane, value in enumerate(row) if value)
        result[32 * i : 32 * (i + 1)] = bits.to_bytes(32, "little")
    return body, bytes(result)


def make_batch(cases):
    stride = 2048
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for index, case in enumerate(cases):
        op, dtype = case["api"], case["dtype"]
        t = TYPES[dtype]
        lanes = 256 // t.width
        base = index * stride
        arrays = []
        for stream in range(6):
            if op in BITWISE:
                raw = bytes(((i * 17 + stream * 31 + 7) % 256) for i in range(256))
                # Keep floating bitwise inputs finite. The exact byte golden
                # below preserves output NaN payloads too.
                if dtype in FLOATING:
                    raw = encode(
                        [float((i + stream) % 5 + 1) for i in range(lanes)], dtype
                    )
                values = decode(raw, dtype)
            else:
                values = [((i + stream) % 5 - 2) for i in range(lanes)]
                if dtype in FLOATING:
                    values = [x * 0.5 for x in values]
                if not t.signed and dtype not in FLOATING:
                    values = [abs(x) for x in values]
                if stream == 4:
                    values = [1 + (i % 2) for i in range(lanes)]
                if op in ("vsqrt", "vln") and stream < 4:
                    values = [float(2 + i % 3) for i in range(lanes)]
                if op == "vexp" and stream < 4:
                    values = [float(-10 + i % 3) for i in range(lanes)]
                if op == "vdiv" and stream < 4:
                    values = [1.0 + 0.25 * (i % 4) for i in range(lanes)]
                if op in ("vshl", "vshr") and stream == 4:
                    values = [i % 3 for i in range(lanes)]
                if op in ("vshl", "vshr") and case.get("chain_source") == "rhs":
                    if stream < 4:
                        values = [
                            (i % 2) * (2 if op == "vshr" else 1) for i in range(lanes)
                        ]
                    elif stream == 4:
                        values = [1] * lanes
                if op in ("vsadd", "vssub", "vsadds"):
                    if stream < 4:
                        values = [
                            (-32768, -32760, -2, 0, 2, 32760, 32767)[i % 7]
                            for i in range(lanes)
                        ]
                    elif stream == 4:
                        values = [-100 if i % 2 else 100 for i in range(lanes)]
                values = [quantize(x, dtype) for x in values]
                raw = encode(values, dtype)
            arrays.append(values)
            data[base + 256 * stream : base + 256 * (stream + 1)] = raw
        if op in COMPARE:
            body, result = compare_case(op, dtype, base, arrays, data)
            golden[base : base + 1792] = result
            name = f"probe_{index}_{op}_{dtype}"
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
                    "output_offset": base,
                    "output_bytes": 1792,
                    "guard_offset": base + 1792,
                    "guard_bytes": 256,
                    "target_count": 7,
                    "exact_bytes": True,
                }
            )
            continue
        # The input and output use disjoint UB windows.
        # This CANN target lowers BF16 NOT to a B8 operation. Match the
        # physical predicate granularity while checking all 256 output bytes.
        mask_width = 1 if (op, dtype) == ("vnot", "bf16") else t.width
        body = [
            f"vector_bool p=pset_b{8 * mask_width}(PAT_ALL);",
            f"vector_{t.vector} a0,a1,a2,a3,b,c;",
            f"__ubuf__ {t.scalar} *src=(__ubuf__ {t.scalar} *)input;",
            f"__ubuf__ {t.scalar} *dst=(__ubuf__ {t.scalar} *)out;",
        ]
        if op in ("vshl", "vshr") and not t.signed:
            signed = f"int{8 * t.width}_t"
            vector = f"s{8 * t.width}"
            body += [
                f"vector_{vector} shift;",
                f"vlds(shift,(__ubuf__ {signed} *)input,{(base + 1024) // t.width},NORM);",
            ]
            rhs = "shift"
        else:
            rhs = "b"
        body += [
            f"vlds(b,src,{(base + 1024) // t.width},NORM);",
            f"vlds(c,src,{(base + 1280) // t.width},NORM);",
        ]
        body += [
            f"vlds(a{i},src,{(base + 256 * i) // t.width},NORM);" for i in range(4)
        ]
        scalar = "1" if dtype not in FLOATING else "0.5f"
        scalar = f"({t.scalar})({scalar})" if not op.startswith("vsh") else "(int16_t)1"
        runtime_scalar = op in {"vshls", "vshrs"} or (op, dtype) == ("vmuls", "bf16")
        scalar_type = "int16_t" if op in {"vshls", "vshrs"} else t.scalar
        if runtime_scalar:
            # Keep shift counts and the BF16 scalar in runtime S registers;
            # literal shifts may select the distinct VSHLI/VSHRI encodings.
            raw = (
                struct.pack("<h", 1)
                if op in {"vshls", "vshrs"}
                else encode([0.5], dtype)
            )
            data[base + 1536 : base + 1536 + len(raw)] = raw
            scalar = "scalar_value"
        body += [
            f"vector_bool halfmask=pset_b{8 * mask_width}(PAT_VL{128 // mask_width});"
        ]
        if case.get("warm_operands"):
            # Stores make every vector and the full predicate observable before
            # the fence. Their completion can be checked in the actual trace.
            body += [
                f"vsts(a{i},src,{(base + 256 * i) // t.width},NORM_B{8 * t.width},p);"
                for i in range(4)
            ]
            body += [
                f"vsts(b,src,{(base + 1024) // t.width},NORM_B{8 * t.width},p);",
                f"vsts(c,src,{(base + 1280) // t.width},NORM_B{8 * t.width},p);",
                "mem_bar(VV_ALL);",
                f"plds(p,(__ubuf__ uint32_t *)input,{base + 1792},NORM);",
            ]
            # The post-fence mask load gates all target streams on one source.
            # Vector loads have completed by then; a shared predicate already
            # consumed by the previous target also proves mask readiness.
            bitmap = sum(1 << lane for lane in range(0, 256, mask_width)).to_bytes(
                32, "little"
            )
            data[base + 1792 : base + 1824] = bitmap
            golden[base + 1792 : base + 1824] = bitmap
        body += [call(op, f"a{i}", rhs, "c", scalar, "p") for i in range(4)]
        body += [
            f"vsts(a{i},dst,{(base + 256 * i) // t.width},NORM_B{8 * t.width},p);"
            for i in range(4)
        ]
        # Two self-RAW calls add an isolated adjacent producer/consumer pair.
        chain_call = (
            f"{op}(a0,{rhs},a0,p,MODE_ZEROING);"
            if case.get("chain_source") == "rhs" and op in BINARY
            else call(op, "a0", rhs, "c", scalar, "p")
        )
        if op in {"vshl", "vshr"} and case.get("chain_source") == "rhs":
            chain_call = f"{op}(a0,b,(vector_s{8 * t.width})a0,p,MODE_ZEROING);"
        accumulator_sources = case.get("accumulator_sources") and op in ACCUMULATE
        second_call = (
            call(op, "c", "a0", "b", scalar, "p") if accumulator_sources else chain_call
        )
        body += [
            chain_call,
            f"vsts(a0,dst,{(base + 1024) // t.width},NORM_B{8 * t.width},p);",
            second_call,
            f"vsts({'c' if accumulator_sources else 'a0'},dst,{(base + 1280) // t.width},NORM_B{8 * t.width},p);",
        ]
        # A separate partial-mask operation validates inactive lanes.
        final_call = (
            call(op, "b", "c" if op == "vaxpy" else "b", "c", scalar, "halfmask")
            if accumulator_sources
            else call(op, "a3", rhs, "c", scalar, "halfmask")
        )
        body += [
            final_call,
            f"vsts({'b' if accumulator_sources else 'a3'},dst,{(base + 1536) // t.width},NORM_B{8 * t.width},p);",
        ]
        svalue = 1 if dtype not in FLOATING or op.startswith("vsh") else 0.5

        # Bitwise output is constructed from bytes rather than round-tripping
        # NaNs through Python's floating conversion.
        def result_bytes(previous, stream, mask_half=False, swap=False):
            if op in BITWISE:
                other = bytes(data[base + 1024 : base + 1280])
                out = bytearray()
                for a, b in zip(previous, other):
                    out.append(
                        {
                            "vand": lambda: a & b,
                            "vor": lambda: a | b,
                            "vxor": lambda: a ^ b,
                            "vnot": lambda: (~a) & 255,
                        }[op]()
                    )
                if mask_half:
                    out[128:] = bytes(128)
                return bytes(out)
            vals = [
                evaluate(op, b, a, c, svalue, dtype)
                if swap
                else evaluate(op, a, b, c, svalue, dtype)
                for a, b, c in zip(previous, arrays[4], arrays[5])
            ]
            if mask_half:
                vals[lanes // 2 :] = [0] * (lanes // 2)
            return encode(vals, dtype)

        previous = []
        for i in range(4):
            initial = (
                bytes(data[base + 256 * i : base + 256 * (i + 1)])
                if op in BITWISE
                else arrays[i]
            )
            raw = result_bytes(initial, i)
            golden[base + 256 * i : base + 256 * (i + 1)] = raw
            previous.append(raw if op in BITWISE else decode(raw, dtype))
        for slot in (4, 5):
            if accumulator_sources and slot == 5:
                vals = [
                    evaluate(op, c, a, b, svalue, dtype)
                    for a, b, c in zip(previous[0], arrays[4], arrays[5])
                ]
                raw = encode(vals, dtype)
                golden[base + 1280 : base + 1536] = raw
                continue
            raw = result_bytes(
                previous[0], 0, swap=case.get("chain_source") == "rhs" and op in BINARY
            )
            golden[base + 256 * slot : base + 256 * (slot + 1)] = raw
            previous[0] = raw if op in BITWISE else decode(raw, dtype)
        if accumulator_sources:
            c_new = decode(bytes(golden[base + 1280 : base + 1536]), dtype)
            vals = [
                evaluate(op, b, c if op == "vaxpy" else b, c, svalue, dtype)
                for b, c in zip(arrays[4], c_new)
            ]
            vals[lanes // 2 :] = [0] * (lanes // 2)
            raw = encode(vals, dtype)
        else:
            raw = result_bytes(previous[3], 3, True)
        golden[base + 1536 : base + 1792] = raw
        name = f"probe_{index}_{op}_{dtype}"
        parameter = f",{scalar_type} scalar_value" if runtime_scalar else ""
        argument = (
            f",*(__gm__ {scalar_type} *)(inputGM+{base + 1536})"
            if runtime_scalar
            else ""
        )
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out{parameter}){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out{argument});")
        contracts.append(
            {
                **case,
                "index": index,
                "output_offset": base,
                "output_bytes": 1792,
                "guard_offset": base + 1792,
                "guard_bytes": 256,
                "target_count": 7,
                "predicate_width_bits": 8 * mask_width,
                "runtime_scalar": runtime_scalar,
                "readiness_gate": bool(case.get("warm_operands")),
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


def analyze(work, contracts, golden):
    actual = (work / "output.bin").read_bytes()
    if len(actual) != len(golden):
        raise ValueError("Output length mismatch")
    popped = (work / "core0.veccore0.instr_popped_log.dump").read_text()
    done = (work / "core0.veccore0.instr_log.dump").read_text()
    events = [
        dict(
            cycle=int(m[1]),
            pc=int(m[2], 16),
            id=int(m[3]),
            op=m[4],
            operands=m[5],
            line=i,
            binary=int(b[1], 16)
            if (b := re.search(r"Binary: 0x([0-9a-fA-F]+)", line))
            else None,
        )
        for i, line in enumerate(popped.splitlines(), 1)
        if (m := EVENT.search(line)) and m[4] not in CONTROL_POLLING
    ]
    if len({(e["id"], e["op"]) for e in events}) != len(events):
        raise ValueError("Duplicate dynamic instruction IDs in popped events")
    # VF boundaries and barriers contain repeated control polling records.
    completions = [
        ((int(m[3]), m[4]), int(m[1]))
        for m in EVENT.finditer(done)
        if m[4] not in CONTROL_POLLING
    ]
    finishes = dict(completions)
    if len(finishes) != len(completions):
        raise ValueError("Duplicate completion events")
    perf = {
        (int(m[3]), m[2]): (int(m[1]), int(m[4]))
        for m in PERF.finditer((work / "core0.veccore0.rvec.EXU.dump").read_text())
    }
    ports = {key: value[1] for key, value in perf.items()}
    lsu_file = work / "core0.veccore0.rvec.LSU.dump"
    lsu_starts = {}
    if lsu_file.exists():
        for m in re.finditer(
            r"\[PERF\] \[(\d+)\] (?:LSU_I1|\[STU_IB_BUF.RECV\]) instr_name (RV_\w+) instr_id (\d+)",
            lsu_file.read_text(),
        ):
            lsu_starts.setdefault((int(m[3]), m[2]), int(m[1]) - 1)
    gsu_file = work / "core0.veccore0.rvec.GSU.dump"
    if gsu_file.exists():
        for m in re.finditer(
            r"\[PERF\] \[(\d+)\] GSU_RECV instr_name (RV_VGATHER\w*|RV_VSCATTER) instr_id (\d+)",
            gsu_file.read_text(),
        ):
            lsu_starts.setdefault((int(m[3]), m[2]), int(m[1]) - 1)

    dispatch_predicates = {}
    dispatch_starts = {}
    dispatch_operands = {}
    dispatch_log = work / "core0.veccore0.rvec.simd.IDU.DISP.dump"
    if dispatch_log.exists():
        for line in dispatch_log.read_text().splitlines():
            identity = re.search(r"instr_id (\d+)", line)
            if identity:
                if "instr_name RV_VDUPM " in line:
                    sections = {
                        k: re.findall(r"v_idx=(\d+)", v)
                        for k, v in re.findall(
                            r"(SRC_VREGS|DST_VREGS|SRC_PREGS):((?:\[v_idx=\d+\])+)",
                            line,
                        )
                    }
                    dst = sections.get("DST_VREGS", [])
                    src = sections.get("SRC_VREGS", [])
                    pg = sections.get("SRC_PREGS", [])
                    if dst and src and pg:
                        new = (
                            next((r for r in src if r != dst[0]), src[0])
                            if len(src) > 1
                            else src[0]
                        )
                        dispatch_operands[int(identity[1])] = (
                            f" Vd[{dst[0]}], Vn[{new}], Pg[{pg[0]}], "
                        )
                pc = re.search(r"\bPC ([0-9a-fA-F]+)\b", line)
                if pc:
                    dispatch_starts.setdefault(int(pc[1], 16), []).append(
                        int(identity[1])
                    )
                section = re.search(r"SRC_PREGS:((?:\[v_idx=\d+\])+)", line)
                dispatch_predicates[int(identity[1])] = (
                    [int(x) for x in re.findall(r"v_idx=(\d+)", section[1])]
                    if section
                    else []
                )
    for event in events:
        if event["op"] == "RV_VDUPM" and not REGISTERS.search(event["operands"]):
            bits = event["binary"]
            if bits is None:
                raise ValueError(
                    "VDUPM requires encoded operands or complete operand text"
                )
            event["operands"] += (
                f" Vd[{(bits >> 25) & 31}], Vn[{(bits >> 20) & 31}], Pg[{(bits >> 10) & 7}], "
            )
    regions = [
        (int(a, 16), int(a, 16) + 4 * int(b, 16))
        for a, b in re.findall(
            r"VF\s+addr:\s*0x([\da-fA-F]+),instr_num:\s*0x([\da-fA-F]+)", popped
        )
    ]
    if len(regions) != len(contracts):
        raise ValueError(f"Expected {len(contracts)} VF calls; got {len(regions)}")
    # CCEC folds identical VFs across dtype spellings. Dynamic IDs distinguish
    # invocations that share the same static PC range.
    occurrences = {}
    invocation_starts = []
    for start, _ in regions:
        index = occurrences.get(start, 0)
        occurrences[start] = index + 1
        ids = dispatch_starts.get(start, [])
        invocation_starts.append(ids[index] if index < len(ids) else None)
    if len(set(regions)) != len(regions) and any(x is None for x in invocation_starts):
        raise ValueError("Repeated VF PCs require per-invocation IDU boundaries")
    reports = []
    for invocation, (case, (start, end)) in enumerate(zip(contracts, regions)):
        base = case["output_offset"]
        dtype = case["dtype"]
        op = case["api"]
        mismatches = []
        for offset in range(base, base + case["output_bytes"], TYPES[dtype].width):
            a, b = (
                actual[offset : offset + TYPES[dtype].width],
                golden[offset : offset + TYPES[dtype].width],
            )
            if a == b:
                continue
            good = False
            if (
                dtype in FLOATING
                and op not in BITWISE
                and not case.get("exact_bytes", False)
            ):
                x, y = decode(a, dtype)[0], decode(b, dtype)[0]
                good = (math.isnan(x) and math.isnan(y)) or math.isclose(
                    x,
                    y,
                    rel_tol=0.004 if dtype != "fp32" else 2e-5,
                    abs_tol=0.004 if dtype != "fp32" else 2e-6,
                )
            if not good:
                mismatches.append(offset - base)
        g = case["guard_offset"]
        guards = (
            actual[g : g + case["guard_bytes"]] == golden[g : g + case["guard_bytes"]]
        )
        first = invocation_starts[invocation]
        following = (
            invocation_starts[invocation + 1] if invocation + 1 < len(regions) else None
        )
        vf = [
            e
            for e in events
            if start <= e["pc"] < end
            and (first is None or e["id"] >= first)
            and (following is None or e["id"] < following)
        ]
        is_target = (
            lambda opcode: opcode in case["target_opcodes"]
            if case.get("target_opcodes")
            else target_opcode(op, opcode)
        )
        target = [
            e
            for e in vf
            if is_target(e["op"])
            and not (
                op == "vmov_merge"
                and e["op"] == "RV_VMOV"
                and "Pg[" not in e["operands"]
            )
        ]
        target_ids = {e["id"] for e in target}
        timing = []
        for e in target:
            key = (e["id"], e["op"])
            memory_target = case.get("target_kind") == "memory"
            if not memory_target and perf[key][0] != e["cycle"] + 1:
                raise ValueError(f"Unexpected PERF/issue offset for {key}")
            timing.append(
                {
                    **e,
                    "done": finishes[key],
                    "latency": finishes[key] - e["cycle"],
                    "perf_cycle": perf[key][0] if key in perf else None,
                    "port": ports.get(key),
                    "lsu_start": lsu_starts.get(key),
                    "pipeline_latency": finishes[key] - lsu_starts[key]
                    if key in lsu_starts
                    else None,
                    "dispatch_predicate_sources": dispatch_predicates.get(e["id"]),
                }
            )
        producers = {}
        producer_roles = {}
        edges = []
        reads = {}
        read_roles = {}
        sources_done = {}
        for e in sorted(vf, key=lambda x: x["id"]):
            registers = REGISTERS.findall(e["operands"])
            store = e["op"].startswith(("RV_VST", "RV_PST")) or e["op"] in {
                "RV_VSCATTER",
                "RV_VSST",
                "RV_VSSTB",
            }
            destinations = [
                (kind + number, kind + role)
                for kind, role, number in registers
                if role == "d"
            ]
            # Pair-result encodings name the first register only. The second
            # result occupies the next architectural vector register.
            if e["op"] in {"RV_VMULL", "RV_VINTLV", "RV_VDINTLV"}:
                first = next(
                    int(number)
                    for kind, role, number in registers
                    if (kind, role) == ("V", "d")
                )
                destinations.append(("V" + str(first + 1), "Vd1"))
            if e["op"] in {"RV_PINTLV", "RV_PDINTLV"}:
                first = next(
                    int(number)
                    for kind, role, number in registers
                    if (kind, role) == ("P", "d")
                )
                destinations.append(("P" + str(first + 1), "Pd1"))
            sources = [
                (kind + number, kind + role)
                for kind, role, number in registers
                if role != "d" or store
            ]
            if (
                (op in ACCUMULATE or case.get("reads_old_destination"))
                and e["id"] in target_ids
            ) or (
                e["op"] in case.get("reads_old_destination_opcodes", ())
                and (e["op"] != "RV_VMOV" or "Pg[" in e["operands"])
            ):
                sources += [
                    (kind + number, kind + role)
                    for kind, role, number in registers
                    if role == "d"
                ]
            if e["op"] in {"RV_VADDCS", "RV_VSUBCS"}:
                # Carry-in is the old value of the encoded Pd destination.
                # CCEC inserts predicate copies when allocation requires one.
                sources += [
                    (kind + number, "Pd_in")
                    for kind, role, number in registers
                    if (kind, role) == ("P", "d")
                ]
            inputs = [
                (producers[key], role, producer_roles[key])
                for key, role in sources
                if key in producers
            ]
            sources_done[e["id"]] = all(
                finishes[(p["id"], p["op"])] <= e["cycle"] for p, _, _ in inputs
            )
            reads[e["id"]] = {p["id"] for p, _, _ in inputs}
            read_roles[e["id"]] = {(p["id"], role, pr) for p, role, pr in inputs}
            for producer, role, producer_role in inputs:
                others = [
                    (p, r, pr) for p, r, pr in inputs if p["id"] != producer["id"]
                ]
                declared = True
                if role == "Pd_in":
                    # Distinguish semantic carry-in from a scheduler RAW. Some
                    # CAModel builds omit old Pd from IDU's source registers.
                    declared = None
                    if e["id"] in dispatch_predicates:
                        predicates = list(dispatch_predicates[e["id"]])
                        for kind, r, number in registers:
                            if (kind, r) == ("P", "g") and int(number) in predicates:
                                predicates.remove(int(number))
                        destination = next(
                            int(n) for k, r, n in registers if (k, r) == ("P", "d")
                        )
                        declared = destination in predicates
                edges.append(
                    {
                        "producer_id": producer["id"],
                        "consumer_id": e["id"],
                        "producer_op": producer["op"],
                        "consumer_op": e["op"],
                        "producer_role": producer_role,
                        "role": role,
                        "gap": e["cycle"] - producer["cycle"],
                        "dependency_reported_by_dispatch": declared,
                        "other_sources_done_before_producer": all(
                            finishes[(p["id"], p["op"])] <= producer["cycle"]
                            for p, _, _ in others
                        ),
                        "other_sources_ready_at_producer_issue": all(
                            finishes[(p["id"], p["op"])] <= producer["cycle"]
                            or (
                                e["op"] == producer["op"]
                                and (p["id"], r, pr) in read_roles[producer["id"]]
                            )
                            for p, r, pr in others
                        ),
                    }
                )
            if not store:
                for key, role in destinations:
                    producers[key] = e
                    producer_roles[key] = role
        ii = []
        for port in (0, 1):
            stream = [e for e in vf if ports.get((e["id"], e["op"])) == port]
            stream.sort(key=lambda e: (e["cycle"], e["line"]))
            for previous, current in zip(stream, stream[1:]):
                if (
                    previous["id"] in target_ids
                    and current["id"] in target_ids
                    and previous["id"] not in reads[current["id"]]
                ):
                    input_ids = reads[current["id"]]
                    source_events = {p["id"]: p for p in vf if p["id"] in input_ids}
                    ii.append(
                        {
                            "previous_id": previous["id"],
                            "current_id": current["id"],
                            "port": port,
                            "gap": current["cycle"] - previous["cycle"],
                            "sources_done_before_previous": all(
                                finishes[(p["id"], p["op"])] <= previous["cycle"]
                                for p in source_events.values()
                            ),
                            "sources_ready_before_previous": all(
                                finishes[(p["id"], p["op"])] <= previous["cycle"]
                                or (p["id"], role, pr) in read_roles[previous["id"]]
                                for identity, role, pr in read_roles[current["id"]]
                                for p in [source_events[identity]]
                            ),
                        }
                    )
        reports.append(
            {
                **case,
                "passed": not mismatches and guards,
                "mismatch_offsets": mismatches[:64],
                "guards_passed": guards,
                "vf_pc": [hex(start), hex(end)],
                "vf_instruction_id_range": [first, following],
                "target_count_actual": len(target),
                "latencies": sorted({e["latency"] for e in timing}),
                "ports": sorted({e["port"] for e in timing if e["port"] is not None}),
                "events": timing,
                "port_pressure_events": [
                    {
                        **e,
                        "done": finishes[(e["id"], e["op"])],
                        "port": ports[(e["id"], e["op"])],
                    }
                    for e in vf
                    if case.get("port_pressure") and e["op"] == case["pressure_opcode"]
                ],
                "memory_events": [
                    {
                        **e,
                        "done": finishes[(e["id"], e["op"])],
                        "latency": finishes[(e["id"], e["op"])] - e["cycle"],
                        "operands_done_at_issue": sources_done[e["id"]],
                        "lsu_start": lsu_starts.get((e["id"], e["op"])),
                        "pipeline_latency": finishes[(e["id"], e["op"])]
                        - lsu_starts[(e["id"], e["op"])]
                        if (e["id"], e["op"]) in lsu_starts
                        else None,
                    }
                    for e in vf
                    if e["op"]
                    in {
                        "RV_VLDS",
                        "RV_VLDI",
                        "RV_VSTS",
                        "RV_VSTI",
                        "RV_PLDS",
                        "RV_PLDI",
                        "RV_PSTS",
                        "RV_PSTI",
                        "RV_VLDAS",
                        "RV_VLDUS",
                        "RV_VLDUI",
                        "RV_VSTUS",
                        "RV_VSTUI",
                        "RV_VSTAS",
                        "RV_VSTAI",
                        "RV_VLD",
                        "RV_VST",
                        "RV_VLDA",
                        "RV_VLDU",
                        "RV_VSTU",
                        "RV_VSTA",
                        "RV_VSTUR",
                        "RV_VSLDB",
                        "RV_VSST",
                        "RV_VSSTB",
                    }
                ],
                "recipe_events": [
                    {
                        **e,
                        "done": finishes[(e["id"], e["op"])],
                        "latency": finishes[(e["id"], e["op"])] - e["cycle"],
                        "port": ports[(e["id"], e["op"])],
                    }
                    for e in vf
                    if op == "vmuls"
                    and dtype == "bf16"
                    and e["op"] in {"RV_VDUPS", "RV_VMUL"}
                ],
                "supporting_compute_events": [
                    {
                        **e,
                        "done": finishes[(e["id"], e["op"])],
                        "latency": finishes[(e["id"], e["op"])] - e["cycle"],
                        "port": ports[(e["id"], e["op"])],
                    }
                    for e in vf
                    if case.get("record_supporting_compute")
                    and (e["id"], e["op"]) in ports
                    and not is_target(e["op"])
                ],
                "all_opcodes": sorted({e["op"] for e in vf}),
                "forwarding_observations": edges,
                "independent_same_port_gaps": ii,
                "single_op_sample": len(target) == case["target_count"],
            }
        )
    return reports


def run_batch(cases, work, cann, factory=make_batch, *, run_timeout=300):
    work.mkdir(parents=True, exist_ok=False)
    source, data, golden, contracts = factory(cases)
    (work / "kernel.cce").write_text(source)
    (work / "input.bin").write_bytes(data)
    (work / "golden.bin").write_bytes(golden)
    (work / "cases.json").write_text(json.dumps(contracts, indent=2))
    shutil.copy2(
        Path(__file__).resolve().parents[1] / "assets/host.cpp", work / "host.cpp"
    )
    libs = [
        cann / p
        for p in (
            "tools/simulator/Ascend950PR_9599/lib",
            "lib64",
            "x86_64-linux/devlib",
            "x86_64-linux/devlib/device",
        )
    ]
    includes = [
        cann / p
        for p in (
            "x86_64-linux/include",
            "include",
            "x86_64-linux/include/experiment/msprof",
            "x86_64-linux/include/experiment/msprof/toolchain",
            "x86_64-linux/pkg_inc/profiling",
            "x86_64-linux/pkg_inc",
            "x86_64-linux/pkg_inc/runtime",
        )
    ]
    env = dict(
        os.environ,
        ASCEND_TOOLKIT_HOME=str(cann),
        NPU_TYPE="Ascend950PR_9599",
        LD_LIBRARY_PATH=":".join(map(str, libs)),
    )
    commands = []

    def run(argv, name, timeout=900):
        argv = list(map(str, argv))
        commands.append(argv)
        (work / "commands.json").write_text(json.dumps(commands, indent=2))
        with (work / name).open("w") as out:
            subprocess.run(
                argv,
                cwd=work,
                env=env,
                stdout=out,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=timeout,
            )

    run(
        [
            cann / "bin/bisheng",
            "-std=c++17",
            "-O2",
            "host.cpp",
            "-o",
            "host",
            f"-DPROBE_INPUT_BYTES={len(data)}",
            f"-DPROBE_OUTPUT_BYTES={len(golden)}",
            "-Wl,--allow-shlib-undefined",
            *[f"-I{x}" for x in includes],
            *[f"-L{x}" for x in libs],
            "-lruntime_camodel",
            "-lstdc++",
            "-lascendcl",
            "-lm",
            "-ltiling_api",
            "-lplatform",
            "-lc_sec",
            "-ldl",
            "-lnnopbase",
        ],
        "host_build.log",
    )
    run(
        [
            cann / "bin/ccec",
            "-g",
            "-std=c++17",
            "-O2",
            "-c",
            "kernel.cce",
            "-o",
            "kernel_aiv.o",
            "--cce-aicore-arch=dav-c310-vec",
            "--cce-aicore-only",
            "--cce-simd-vf-fusion=false",
            "-mllvm",
            "-cce-aicore-vec-misched=0",
        ],
        "compile.log",
    )
    run(
        [cann / "bin/ld.lld", "-Ttext=0", "kernel_aiv.o", "-static", "-o", "kernel.o"],
        "link.log",
    )
    run([work / "host", work / "kernel.o", "vector_compute"], "run.log", timeout=run_timeout)
    reports = analyze(work, contracts, golden)
    (work / "validation.json").write_text(json.dumps(reports, indent=2) + "\n")
    return reports


def cases_for(operations, dtypes):
    report = json.loads(
        (ROOT / "docs/instruction_coverage/a5/coverage.json").read_text()
    )
    result = []
    for row in report["apis"]:
        op = row["api"]
        if op not in UNARY | BINARY | SCALAR | ACCUMULATE | COMPARE:
            continue
        if operations and op not in operations:
            continue
        forms = {str(normalize_dtype(f)) for f in row["cann_forms"]} & set(TYPES)
        if op == "vdiv":
            forms &= {"fp16", "fp32"}
        if op == "vmuls":
            forms.discard("bf16")
        if dtypes:
            forms &= set(dtypes)
        for dtype in sorted(forms):
            result.append({"api": op, "dtype": dtype})
    return result


def check_target(case, root, cann):
    """Compile one API/form so target rejections cannot hide other cases."""
    work = root / (case["api"] + "_" + case["dtype"])
    work.mkdir(parents=True)
    source, _, _, _ = make_batch([case])
    (work / "kernel.cce").write_text(source)
    command = [
        str(cann / "bin/ccec"),
        "-g",
        "-std=c++17",
        "-O2",
        "-c",
        "kernel.cce",
        "-o",
        "kernel_aiv.o",
        "--cce-aicore-arch=dav-c310-vec",
        "--cce-aicore-only",
        "--cce-simd-vf-fusion=false",
        "-mllvm",
        "-cce-aicore-vec-misched=0",
    ]
    result = subprocess.run(
        command, cwd=work, capture_output=True, text=True, timeout=120
    )
    (work / "compile.log").write_text(result.stdout + result.stderr)
    (work / "command.json").write_text(json.dumps(command, indent=2))
    if result.returncode:
        if "does not support the given target feature" not in result.stderr:
            raise RuntimeError(
                f"Unexpected compile failure in {work}; inspect compile.log"
            )
        return {
            **case,
            "compiled": False,
            "reason": "target_feature_rejected",
            "log": str(work / "compile.log"),
        }
    return {**case, "compiled": True}


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
    parser.add_argument("--batch-size", type=int, default=16, choices=range(1, 33))
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--warm-operands", action="store_true")
    parser.add_argument("--chain-source", choices=("lhs", "rhs"), default="lhs")
    parser.add_argument("--accumulator-sources", action="store_true")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    cases = cases_for(args.operations, args.dtypes)
    cases = [
        {
            **c,
            "warm_operands": args.warm_operands,
            "chain_source": args.chain_source,
            "accumulator_sources": args.accumulator_sources,
        }
        for c in cases
    ]
    if not cases:
        parser.error("No matching CANN declarations")
    (args.out / "cases.json").write_text(json.dumps(cases, indent=2))
    if not args.prepare_only:
        print("Checking target support for", len(cases), "API/forms", flush=True)
        with ThreadPoolExecutor(max_workers=4) as pool:
            checks = list(
                pool.map(
                    lambda c: check_target(c, args.out / "compile_checks", args.cann),
                    cases,
                )
            )
        (args.out / "target_checks.json").write_text(
            json.dumps(checks, indent=2) + "\n"
        )
        cases = [
            {k: v for k, v in c.items() if k not in ("compiled", "reason", "log")}
            for c in checks
            if c["compiled"]
        ]
        print("Target accepts", len(cases), "API/forms", flush=True)
    all_reports = []
    for n, start in enumerate(range(0, len(cases), args.batch_size)):
        batch = cases[start : start + args.batch_size]
        work = args.out / f"batch_{n:03}"
        print("Batch", n, len(batch), "cases", flush=True)
        if args.prepare_only:
            work.mkdir()
            source, data, golden, contracts = make_batch(batch)
            (work / "kernel.cce").write_text(source)
            (work / "input.bin").write_bytes(data)
            (work / "golden.bin").write_bytes(golden)
            (work / "cases.json").write_text(json.dumps(contracts, indent=2))
            continue
        reports = run_batch(batch, work, args.cann)
        all_reports.extend(reports)
        (args.out / "summary.json").write_text(json.dumps(all_reports, indent=2) + "\n")
        print(
            "passed",
            sum(r["passed"] for r in reports),
            "of",
            len(reports),
            "direct",
            sum(r["single_op_sample"] for r in reports),
            flush=True,
        )
    if not args.prepare_only and any(not r["passed"] for r in all_reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
