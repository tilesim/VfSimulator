"""A5 mixed-dtype conversion, pack and unpack probes with byte-exact golden.

Full-vector results, inactive lanes and guard bytes are checked. RAW chains use
zero-valued bit reinterpretations so every intermediate remains well defined.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path

import vector_compute as compute
import low_precision as lowp

ROUNDING = ("ROUND_R", "ROUND_A", "ROUND_F", "ROUND_C", "ROUND_Z")
PART = ("PART_EVEN", "PART_ODD")
PP = ("PART_P0", "PART_P1", "PART_P2", "PART_P3")
# Configuration arguments follow the CANN 3101 header order for each form.
SIGNATURES = {
    "bf16_to_f16": ("saturation", "round"),
    "bf16_to_f32": ("part",),
    "bf16_to_s32": ("round", "saturation", "part"),
    "f16_to_bf16": ("round",),
    "f16_to_f32": ("part",),
    "f16_to_s16": ("round", "saturation"),
    "f16_to_s32": ("round", "part"),
    "f16_to_s8": ("round", "saturation", "part"),
    "f16_to_u8": ("round", "saturation", "part"),
    "f32_to_bf16": ("round", "saturation", "part"),
    "f32_to_f16": ("round", "saturation", "part"),
    "f32_to_s16": ("round", "saturation", "part"),
    "f32_to_s32": ("round", "saturation"),
    "f32_to_s64": ("round", "saturation", "part"),
    "s16_to_f16": ("round",),
    "s16_to_f32": ("part",),
    "s16_to_s32": ("part",),
    "s16_to_u32": ("part",),
    "s16_to_u8": ("saturation", "part"),
    "s32_to_f32": ("round",),
    "s32_to_s64": ("part",),
    "s64_to_f32": ("round", "part"),
    "s64_to_s32": ("saturation", "part"),
    "s32_to_s16": ("saturation", "part"),
    "s32_to_u16": ("saturation", "part"),
    "s32_to_u8": ("saturation", "pp"),
    "s8_to_f16": ("part",),
    "s8_to_s16": ("part",),
    "s8_to_s32": ("pp",),
    "u16_to_u32": ("part",),
    "u16_to_u8": ("saturation", "part"),
    "u32_to_s16": ("saturation", "part"),
    "u32_to_u16": ("saturation", "part"),
    "u32_to_u8": ("saturation", "pp"),
    "u8_to_f16": ("part",),
    "u8_to_u16": ("part",),
    "u8_to_u32": ("pp",),
}
LOW_PRECISION_SIGNATURES = {
    "bf16_to_f4e1m2x2": ("round", "pp"),
    "bf16_to_f4e2m1x2": ("round", "pp"),
    "bf16_to_f8e4m3": ("round", "saturation", "part"),
    "bf16_to_f8e5m2": ("round", "saturation", "part"),
    "f16_to_f8e4m3": ("round", "saturation", "part"),
    "f16_to_f8e5m2": ("round", "saturation", "part"),
    "f16_to_hif8": ("round", "saturation", "part"),
    "f16_to_s4x2": ("round", "saturation", "pp"),
    "f32_to_f8e4m3": ("round", "saturation", "pp"),
    "f32_to_f8e5m2": ("round", "saturation", "pp"),
    "f32_to_hif8": ("round", "saturation", "pp"),
    "f4e1m2x2_to_bf16": ("pp",),
    "f4e2m1x2_to_bf16": ("pp",),
    "f8e4m3_to_f32": ("pp",),
    "f8e5m2_to_f32": ("pp",),
    "hif8_to_f16": ("part",),
    "hif8_to_f32": ("pp",),
    "s16_to_s4x2": ("saturation", "pp"),
    "s4x2_to_bf16": ("pp",),
    "s4x2_to_f16": ("pp",),
    "s4x2_to_s16": ("pp",),
}
SIGNATURES.update(LOW_PRECISION_SIGNATURES)
NAMED_ALIASES = {
    "vcvt_f162s4": "f16_to_s4x2",
    "vcvt_s162s4": "s16_to_s4x2",
    "vcvt_s42bf16": "s4x2_to_bf16",
    "vcvt_s42f16": "s4x2_to_f16",
    "vcvt_s42s16": "s4x2_to_s16",
}
PACK_FORMS = ("s16_to_u8", "u16_to_u8", "s32_to_u16", "u32_to_u16")
UNPACK_FORMS = ("s8_to_s16", "u8_to_u16", "s16_to_s32", "u16_to_u32")
DTYPES = {t.vector: dtype for dtype, t in compute.TYPES.items()}


def config_values(form, name):
    if name == "round":
        if form.endswith("_to_hif8"):
            return ("ROUND_A", "ROUND_H")
        if form in ("f32_to_f8e4m3", "f32_to_f8e5m2"):
            return ("ROUND_R",)
        return (*ROUNDING, "ROUND_O") if form == "f32_to_f16" else ROUNDING
    if name == "saturation":
        return ("RS_DISABLE", "RS_ENABLE")
    return PART if name == "part" else PP


def call(case, target, source, mask="p"):
    api, form = case["api"], case["form"]
    if api == "vcvt_unmasked":
        part = (
            case.get("pp", "PART_P0")
            if "pp" in SIGNATURES[form]
            else case.get("part", "PART_EVEN")
        )
        tail = [part]
        if case.get("has_mode", True):
            tail.append(case.get("mode", "MODE_UNKNOWN"))
        return f"vcvt({target},{source},{','.join(tail)});"
    if api in {"vfcvt", "vsfcvt", "vscvt"}:
        from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

        spec = DEFAULT_INSTRUCTION_CATALOG.lookup((api + "_" + form).upper())
        tail = []
        defaults = {
            "round": "ROUND_R",
            "saturation": "RS_ENABLE",
            "part": "PART_EVEN",
            "pp": "PART_P0",
            "mode": "MODE_UNKNOWN",
        }
        for operand in spec.operands[2:]:
            tail.append(
                mask
                if operand.kind.value == "predicate"
                else case.get(operand.name, defaults[operand.name])
            )
        return f"{api}({target},{source},{','.join(tail)});"
    if api in {"vpack", "vunpack"}:
        tail = [case.get("pack_part", "LOWER")]
        if api == "vpack":
            tail.append("MODE_ZEROING")
    else:
        tail = [mask] + [
            case.get(
                name,
                {
                    "round": "ROUND_A" if form.endswith("_to_hif8") else "ROUND_R",
                    "saturation": "RS_ENABLE",
                    "part": "PART_EVEN",
                    "pp": "PART_P0",
                }[name],
            )
            for name in SIGNATURES[form]
        ]
        tail.append("MODE_ZEROING")
    return f"{api}({target},{source},{','.join(tail)});"


def element_bits(dtype):
    return lowp.bits(dtype) if dtype in lowp.TYPES else 8 * compute.TYPES[dtype].width


def encode(values, dtype):
    return (
        lowp.encode(values, dtype)
        if dtype in lowp.TYPES
        else compute.encode(values, dtype)
    )


def decode(raw, dtype):
    return (
        lowp.decode(raw, dtype) if dtype in lowp.TYPES else compute.decode(raw, dtype)
    )


def register_bits(name, dtype):
    return name + ".val" if dtype == "s4x2" else name


def scalar_convert(value, source, dest, rounding="ROUND_R", saturation="RS_ENABLE"):
    if dest in lowp.FLOATING:
        return lowp.value(lowp.code(value, dest, rounding), dest)
    if dest in compute.FLOATING:
        # Probe values are exactly representable for float-to-float conversion;
        # integer conversion uses an independent round-to-nearest encoding.
        return (
            lowp.value(int(value) & 15, dest)
            if dest == "s4x2"
            else compute.quantize(value, dest)
        )
    if source in compute.FLOATING | lowp.FLOATING:
        value = {
            "ROUND_R": round,
            "ROUND_A": lambda x: math.copysign(math.floor(abs(x) + 0.5), x),
            "ROUND_F": math.floor,
            "ROUND_C": math.ceil,
            "ROUND_Z": math.trunc,
        }[rounding](value)
    # A5 signed-to-unsigned conversions clamp negatives even with RS_DISABLE.
    if not compute.TYPES[dest].signed:
        value = max(0, value)
    if saturation == "RS_ENABLE":
        t = compute.TYPES[dest]
        limit = 1 << (element_bits(dest) - int(t.signed))
        value = max(-limit if t.signed else 0, min(limit - 1, int(value)))
    return (
        lowp.value(int(value) & 15, dest)
        if dest == "s4x2"
        else compute.quantize(value, dest)
    )


def reference(raw, case, half=False):
    src, dst = (DTYPES[x] for x in case["form"].split("_to_"))
    a, b = compute.TYPES[src], compute.TYPES[dst]
    values = decode(raw, src)
    result = bytearray(256)
    if case["api"] == "vpack":
        count = 256 // a.width
        start = 0 if case.get("pack_part", "LOWER") == "LOWER" else count
        converted = [compute.quantize(x, dst) for x in values]
        result[start * b.width : (start + count) * b.width] = compute.encode(
            converted, dst
        )
        return bytes(result)
    if case["api"] == "vunpack":
        count = 256 // b.width
        start = 0 if case.get("pack_part", "LOWER") == "LOWER" else count
        return compute.encode(
            [compute.quantize(x, dst) for x in values[start : start + count]], dst
        )
    ab, bb = element_bits(src), element_bits(dst)
    count = 2048 // max(ab, bb)
    ratio = max(ab, bb) // min(ab, bb)
    lanes = [0] * (2048 // bb)
    part = (
        int(case.get("pp", "PART_P0")[-1])
        if "pp" in SIGNATURES[case["form"]]
        else int(case.get("part", "PART_EVEN") == "PART_ODD")
    )
    for i in range(count // 2 if half else count):
        si = ratio * i + part if ab < bb else i
        di = ratio * i + part if ab > bb else i
        # Packed conversion treats each byte as two adjacent 4-bit values;
        # PART_Pn selects one byte within each 32-bit source/result group.
        if ab == 4 and bb == 16:
            si = 8 * (i // 2) + 2 * part + (i % 2)
        if ab == 16 and bb == 4:
            di = 8 * (i // 2) + 2 * part + (i % 2)
        value = scalar_convert(
            values[si],
            src,
            dst,
            case.get(
                "round", "ROUND_A" if case["form"].endswith("_to_hif8") else "ROUND_R"
            ),
            case.get(
                "saturation", "RS_DISABLE" if case["api"] == "vfcvt" else "RS_ENABLE"
            ),
        )
        lanes[di] = value
    return encode(lanes, dst)


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions, calls, contracts = [], [], []
    for index, case in enumerate(cases):
        api, form = case["api"], case["form"]
        src, dst = (DTYPES[x] for x in form.split("_to_"))
        a, b = compute.TYPES[src], compute.TYPES[dst]
        base = index * stride
        arrays = []
        for stream in range(4):
            # The first independent result seeds a defined zero-valued RAW.
            values = (
                [0] * (256 // a.width)
                if stream == 0
                else [((i * 7 + stream * 11) % 31) - 15 for i in range(256 // a.width)]
            )
            if src not in compute.FLOATING and not a.signed:
                values = [x + 16 if stream else 0 for x in values]
            if (
                case.get("rounding_values")
                and src in compute.FLOATING
                and dst not in compute.FLOATING
                and stream
            ):
                values = [x + (0.5 if i % 2 else 0.25) for i, x in enumerate(values)]
            if case.get("boundary_values") and stream and dst not in compute.FLOATING:
                limit = 1 << (8 * b.width - int(b.signed))
                candidates = [
                    -limit - 1,
                    -limit,
                    -2,
                    -1,
                    0,
                    1,
                    limit - 1,
                    limit,
                    limit + 1,
                ]
                if src not in compute.FLOATING:
                    upper = (1 << (8 * a.width - int(a.signed))) - 1
                    lower = -(1 << (8 * a.width - 1)) if a.signed else 0
                    candidates = [max(lower, min(upper, x)) for x in candidates]
                elif src == "fp16":
                    candidates = [max(-65504, min(65504, x)) for x in candidates]
                values = [
                    compute.quantize(candidates[(i + stream) % len(candidates)], src)
                    for i in range(256 // a.width)
                ]
            if form in LOW_PRECISION_SIGNATURES:
                palette = (
                    [0, 1, -1, 2, -2, 3, -3, 4]
                    if "s4x2" in form
                    else [0, 0.5, -0.5, 1, -1, 1.5, -1.5]
                )
                values = [
                    palette[(i + stream) % len(palette)] if stream else 0
                    for i in range(2048 // element_bits(src))
                ]
            raw = encode(values, src)
            arrays.append(raw)
            data[base + 256 * stream : base + 256 * (stream + 1)] = raw
        bitmap = bytes([255]) * 32
        data[base + 3840 : base + 3872] = bitmap
        golden[base : base + stride] = data[base : base + stride]
        body = [
            f"vector_{a.vector} a0,a1,a2,a3;",
            f"vector_{b.vector} r0,r1,r2,r3,r4,r5,r6;",
            "vector_bool full=pset_b8(PAT_ALL),halfmask=pset_b8(PAT_H),p;",
            *[
                f"vlds((vector_u8 &){register_bits(f'a{i}', src)},input,{base + 256 * i},NORM);"
                for i in range(4)
            ],
            *[
                f"vsts((vector_u8){register_bits(f'a{i}', src)},input,{base + 256 * i},NORM_B8,full);"
                for i in range(4)
            ],
            "mem_bar(VV_ALL);",
            f"plds(p,(__ubuf__ uint32_t *)input,{base + 3840},NORM);",
        ]
        if api != "vcvt" or case.get("port_pressure"):
            body += [
                f"vor((vector_u8 &){register_bits(f'a{i}', src)},(vector_u8){register_bits(f'a{i}', src)},(vector_u8){register_bits(f'a{i}', src)},p,MODE_ZEROING);"
                for i in range(4)
            ]
        if case.get("port_pressure"):
            body += ["vector_u8 pressure0,pressure1;"]
            for i in range(4):
                body += [
                    f"vintlv(pressure0,pressure1,(vector_u8){register_bits(f'a{i}', src)},(vector_u8){register_bits(f'a{i}', src)});",
                    f"vsts(pressure0,input,{base + 1280},NORM_B8,full);",
                    f"vsts(pressure1,input,{base + 1536},NORM_B8,full);",
                ]
        wrapped = api in {"vfcvt", "vsfcvt", "vscvt", "vcvt_unmasked"}
        masked_wrapper = False
        if wrapped and api != "vcvt_unmasked":
            from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

            masked_wrapper = any(
                o.kind.value == "predicate"
                for o in DEFAULT_INSTRUCTION_CATALOG.lookup(
                    (api + "_" + form).upper()
                ).operands
            )
        merging = case.get("mode") == "MODE_MERGING"
        old_results = []
        if merging:
            for slot in range(7):
                values = (
                    [0] * (2048 // element_bits(dst))
                    if slot != 6
                    else [20 + (i % 7) for i in range(2048 // element_bits(dst))]
                )
                raw = encode(values, dst)
                old_results.append(raw)
                offset = base + 2048 + 256 * slot
                data[offset : offset + 256] = raw
                golden[offset : offset + 256] = raw
                body.append(
                    f"vlds((vector_u8 &){register_bits(f'r{slot}', dst)},input,{offset},NORM);"
                )
        outputs, defined_outputs, observations = [], [], []
        for slot in range(7):
            half = (
                slot == 6
                and api not in {"vpack", "vunpack"}
                and (not wrapped or masked_wrapper)
            )
            effective = case
            if wrapped and not masked_wrapper and slot == 6:
                effective = {
                    **case,
                    "part": "PART_ODD"
                    if case.get("part", "PART_EVEN") == "PART_EVEN"
                    else "PART_EVEN",
                    "pp": "PART_P3"
                    if case.get("pp", "PART_P0") == "PART_P0"
                    else "PART_P0",
                }
            source = (
                f"a{slot}"
                if slot < 4
                else f"(vector_{a.vector})r{slot - 1 if slot == 5 else 0}"
                if slot in (4, 5)
                else "a3"
            )
            raw = (
                arrays[slot]
                if slot < 4
                else outputs[slot - 1 if slot == 5 else 0]
                if slot in (4, 5)
                else arrays[3]
            )
            if slot in (4, 5):
                previous = slot - 1 if slot == 5 else 0
                bits = register_bits(f"r{previous}", dst)
                if src == "s4x2":
                    body += [
                        f"vector_s4x2 chain{slot};chain{slot}.val=(vector_s8){bits};"
                    ]
                    source = f"chain{slot}"
                else:
                    source = f"(vector_{a.vector}){bits}"
            body.append(
                call(effective, f"r{slot}", source, "halfmask" if half else "p")
            )
            result = reference(raw, effective, half)
            if merging:
                ab, bb = element_bits(src), element_bits(dst)
                count = 2048 // max(ab, bb)
                ratio = max(ab, bb) // min(ab, bb)
                part = (
                    int(effective.get("pp", "PART_P0")[-1])
                    if "pp" in SIGNATURES[form]
                    else int(effective.get("part", "PART_EVEN") == "PART_ODD")
                )
                retained = decode(old_results[slot], dst)
                converted = decode(result, dst)
                for i in range(count // 2 if half else count):
                    lane_index = ratio * i + part if ab > bb else i
                    retained[lane_index] = converted[lane_index]
                result = encode(retained, dst)
            outputs.append(result)
            if api == "vpack":
                previous = slot - 1 if slot == 5 else 0
                valid = defined_outputs[previous] if slot in (4, 5) else [True] * 256
                count = 256 // a.width
                start = 0 if case.get("pack_part", "LOWER") == "LOWER" else 128
                defined = [False] * 256
                for lane in range(count):
                    if all(valid[lane * a.width : (lane + 1) * a.width]):
                        defined[
                            start + lane * b.width : start + (lane + 1) * b.width
                        ] = [True] * b.width
                defined_outputs.append(defined)
                offset = base + 3200 + 32 * slot
                bitmap = sum(1 << i for i, good in enumerate(defined) if good).to_bytes(
                    32, "little"
                )
                data[offset : offset + 32] = bitmap
                golden[offset : offset + 32] = bitmap
                observations += [
                    f"vector_bool store{slot};vector_u8 observed{slot};",
                    f"plds(store{slot},(__ubuf__ uint32_t *)input,{offset},NORM);",
                    f"vor(observed{slot},(vector_u8){register_bits(f'r{slot}', dst)},(vector_u8){register_bits(f'r{slot}', dst)},p,MODE_ZEROING);",
                    f"vsts(observed{slot},out,{base + 256 * slot},NORM_B8,store{slot});",
                ]
                for i, good in enumerate(defined):
                    if good:
                        golden[base + 256 * slot + i] = result[i]
            else:
                body.append(
                    f"vsts((vector_u8){register_bits(f'r{slot}', dst)},out,{base + 256 * slot},NORM_B8,full);"
                )
                golden[base + 256 * slot : base + 256 * (slot + 1)] = result
        body += observations
        name = f"probe_{index}_{api}_{form}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}(__ubuf__ uint8_t *input,__ubuf__ uint8_t *out){{__VEC_SCOPE__{{"
            + "\n".join(body)
            + "}}"
        )
        calls.append(f"{name}(input,out);")
        target = (
            ("RV_VCVT_F2F", "RV_VCVT_F2I", "RV_VCVT_I2F", "RV_VCVT_I2I", "RV_VCVT_F2F2")
            if api not in {"vpack", "vunpack"}
            else ("RV_VPACK",)
            if api == "vpack"
            else ("RV_VZUNPACK", "RV_VSUNPACK")
        )
        contracts.append(
            {
                **case,
                "dtype": dst,
                "index": index,
                "output_offset": base,
                "output_bytes": 1792,
                "guard_offset": base + 1792,
                "guard_bytes": stride - 1792,
                "target_count": 7 if api not in {"vpack", "vunpack"} else 6,
                "exact_bytes": True,
                "source_dtype": src,
                "result_dtype": dst,
                "target_opcodes": target,
                "warm_operands": True,
                "pressure_opcode": "RV_VINTLV",
                "record_supporting_compute": wrapped or merging,
                "defined_output_byte_counts": [sum(v) for v in defined_outputs]
                if defined_outputs
                else [256] * 7,
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
    from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG

    return (
        [{"api": "vcvt", "form": form} for form in SIGNATURES]
        + [{"api": "vpack", "form": form} for form in PACK_FORMS]
        + [{"api": "vunpack", "form": form} for form in UNPACK_FORMS]
        + [{"api": api, "form": form} for api, form in NAMED_ALIASES.items()]
        + [
            {"api": family.lower(), "form": form}
            for family in ("VFCVT", "VSFCVT", "VSCVT")
            for form in DEFAULT_INSTRUCTION_CATALOG.lookup(family).specializations
        ]
    )


def native_unmasked_cases():
    """Enumerate the public, unmasked integer conversions from CANN recipes."""
    from api.common.symbols import compact_dtype

    document = json.loads((compute.ROOT / "configs/cce_lowering.json").read_text())
    cases = []
    for recipe in document["recipes"]:
        operands = recipe["arguments"]
        if (
            recipe["api"] != "vcvt"
            or any("2xvl_" in a["type"] for a in operands)
            or any(a["type"].strip() == "vector_bool" for a in operands)
        ):
            continue
        types = [
            compact_dtype(a["type"].replace("&", "").strip().removeprefix("vector_"))
            for a in operands[:2]
        ]
        case = dict(
            api="vcvt_unmasked",
            form=types[1] + "_to_" + types[0],
            mode="MODE_UNKNOWN",
            saturation="RS_DISABLE",
            has_mode=any(a["name"] == "mode" for a in operands),
            recipe_name=recipe["name"],
        )
        for argument in operands:
            if argument["config"]:
                case[argument["name"]] = recipe["options"][str(argument["position"])]
        cases.append(case)
    return cases


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
    parser.add_argument("--forms", nargs="+")
    parser.add_argument("--operations", nargs="+")
    parser.add_argument("--part", choices=PART, default="PART_EVEN")
    parser.add_argument("--pp", choices=PP, default="PART_P0")
    parser.add_argument(
        "--round", choices=(*ROUNDING, "ROUND_O", "ROUND_H"), default="ROUND_R"
    )
    parser.add_argument(
        "--saturation", choices=("RS_DISABLE", "RS_ENABLE"), default="RS_ENABLE"
    )
    parser.add_argument(
        "--mode",
        choices=("MODE_ZEROING", "MODE_MERGING", "MODE_UNKNOWN"),
        default="MODE_UNKNOWN",
    )
    parser.add_argument("--pack-part", choices=("LOWER", "HIGHER"), default="LOWER")
    parser.add_argument("--port-pressure", action="store_true")
    parser.add_argument("--rounding-values", action="store_true")
    parser.add_argument("--boundary-values", action="store_true")
    parser.add_argument(
        "--native-unmasked",
        action="store_true",
        help="Run all unmasked native integer conversion signatures and options",
    )
    parser.add_argument("--jobs", type=int, default=1, choices=(1, 2, 3))
    parser.add_argument("--batch-size", type=int, default=16, choices=range(1, 17))
    args = parser.parse_args()
    if args.native_unmasked:
        cases = native_unmasked_cases()
        if args.forms:
            cases = [case for case in cases if case["form"] in args.forms]
    else:
        cases = None
    cases = (
        cases
        if cases is not None
        else [
            {
                **case,
                "part": args.part,
                "pp": args.pp,
                "round": (
                    "ROUND_A"
                    if case["form"].endswith("_to_hif8") and args.round == "ROUND_R"
                    else args.round
                ),
                "saturation": args.saturation,
                "mode": args.mode,
                "pack_part": args.pack_part,
                "port_pressure": args.port_pressure,
                "rounding_values": args.rounding_values,
                "boundary_values": args.boundary_values,
            }
            for case in cases_for()
            if (not args.forms or case["form"] in args.forms)
            and (not args.operations or case["api"] in args.operations)
        ]
    )
    if not cases:
        parser.error("No selected conversion forms")
    if args.round == "ROUND_O" and any(c["form"] != "f32_to_f16" for c in cases):
        parser.error("ROUND_O requires --operations vcvt --forms f32_to_f16")
    batches = [
        cases[i : i + args.batch_size] for i in range(0, len(cases), args.batch_size)
    ]

    def run(item):
        i, batch = item
        reports = compute.run_batch(
            batch, args.out / f"batch_{i:03}", args.cann, factory=make_batch
        )
        return {
            "batch": i,
            "cases": len(reports),
            "passed": sum(r["passed"] for r in reports),
            "physical_single_op": sum(r["single_op_sample"] for r in reports),
            "failures": [
                {
                    k: r[k]
                    for k in (
                        "api",
                        "form",
                        "mismatch_offsets",
                        "all_opcodes",
                        "target_count_actual",
                    )
                }
                for r in reports
                if not r["passed"] or not r["single_op_sample"]
            ],
        }

    failed = False
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        for result in executor.map(run, enumerate(batches)):
            print(json.dumps(result), flush=True)
            failed |= bool(result["failures"])
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
