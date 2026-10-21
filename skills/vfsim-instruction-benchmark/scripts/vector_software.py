"""Check CANN multi-instruction wrappers against independent integer references.

The wrapper is compiled normally with CCEC and executed by CAModel. Its entire
output window, including inactive lanes and guards, is compared byte for byte.
These functional runs do not qualify an expansion as one timing instruction.
"""

from __future__ import annotations
import argparse, json, os, struct
from pathlib import Path
import vector_compute as compute
from vector_misc import bitmap, round_integral


def round_float32(value, mode):
    nearest = compute.quantize(value, "fp32")
    if nearest == value or mode == "ROUND_R":
        return nearest
    bits = struct.unpack("<I", struct.pack("<f", nearest))[0]
    if mode == "ROUND_A":
        if abs(nearest) < abs(value):
            away = struct.unpack("<f", struct.pack("<I", bits + 1))[0]
            if abs(value - nearest) == abs(away - value):
                return away
        return nearest
    downward = mode == "ROUND_F" or (mode == "ROUND_Z" and value >= 0)
    if (downward and nearest > value) or (not downward and nearest < value):
        bits += (-1 if nearest >= 0 else 1) if downward else (1 if nearest >= 0 else -1)
        nearest = struct.unpack("<f", struct.pack("<I", bits))[0]
    return nearest


def dtype_of(typ):
    name = typ.replace("&", "").strip().removeprefix("vector_")
    if name.startswith("2xvl_"):
        name = name[5:]
    return (
        str(compute.normalize_dtype(name))
        if typ.startswith("vector_")
        else {
            "int64_t": "int64",
            "uint64_t": "uint64",
            "int32_t": "int32",
            "uint32_t": "uint32",
            "float": "fp32",
        }.get(name, name)
    )


def is_pair_type(typ):
    return "2xvl_" in typ


def reference(recipe, values, mask, n):
    op = recipe["api"]
    args = recipe["arguments"]
    outs = [a for a in args if "&" in a["type"]]
    ins = [
        a
        for a in args
        if not a["config"] and "&" not in a["type"] and a["type"] != "vector_bool"
    ]
    options = list(recipe["options"].values())
    outtype = dtype_of(outs[0]["type"])
    a = values[ins[0]["position"]] if ins else 0
    b = values[ins[1]["position"]] if len(ins) > 1 else 0
    old = values.get(outs[0]["position"])
    merge = "MODE_MERGING" in options
    scalar = lambda x, i: x[i] if isinstance(x, list) else x
    if op.startswith(("vcmp_", "vcmps_")):
        cond = op.split("_")[1]
        tests = {
            "eq": lambda x, y: x == y,
            "ne": lambda x, y: x != y,
            "lt": lambda x, y: x < y,
            "le": lambda x, y: x <= y,
            "gt": lambda x, y: x > y,
            "ge": lambda x, y: x >= y,
        }
        return [
            bitmap(
                4,
                [
                    i
                    for i in range(n)
                    if mask[i] and tests[cond](scalar(a, i), scalar(b, i))
                ],
            )
        ]
    if op in {"vintlv", "vdintlv"}:
        if op == "vintlv":
            joined = [v for pair in zip(a, b) for v in pair]
            return [joined[:n], joined[n:]]
        joined = a + b
        return [joined[::2], joined[1::2]]
    if op in {"vcadd", "vcmax", "vcmin"}:
        active = [a[i] for i in range(n) if mask[i]]
        value = {"vcadd": sum, "vcmax": max, "vcmin": min}[op](active)
        return [[compute.quantize(value, outtype)]]
    result = []
    for i in range(n):
        x, y = scalar(a, i), scalar(b, i)
        if op == "vcvt":
            v = x
            if outtype in {"int64", "uint64", "int32", "uint32"} and isinstance(
                v, float
            ):
                v = round_integral(
                    v, next((s for s in options if s.startswith("ROUND_")), "ROUND_R")
                )
        elif op == "vci":
            v = a + i
        elif op == "vbr":
            v = a
        elif op == "vdup":
            v = (
                (a[-1] if "POS_HIGHEST" in options else a[0])
                if isinstance(a, list)
                else a
            )
        elif op == "vmov":
            v = x
        elif op == "vsel":
            v = x if mask[i] else y
        elif op == "vselr":
            v = a[int(y) % n]
        elif op in {"vdiv", "vmod"}:
            q = (abs(int(x)) // abs(int(y))) * (-1 if (x < 0) != (y < 0) else 1)
            v = q if op == "vdiv" else int(x) - q * int(y)
        else:
            formulas = {
                "vabs": lambda: abs(x),
                "vneg": lambda: -x,
                "vnot": lambda: ~int(x),
                "vadd": lambda: x + y,
                "vadds": lambda: x + y,
                "vsub": lambda: x - y,
                "vmul": lambda: x * y,
                "vmuls": lambda: x * y,
                "vand": lambda: int(x) & int(y),
                "vor": lambda: int(x) | int(y),
                "vxor": lambda: int(x) ^ int(y),
                "vmax": lambda: max(x, y),
                "vmaxs": lambda: max(x, y),
                "vmin": lambda: min(x, y),
                "vmins": lambda: min(x, y),
                "vshl": lambda: int(x) << int(y),
                "vshls": lambda: int(x) << int(y),
                "vshr": lambda: int(x) >> int(y),
                "vshrs": lambda: int(x) >> int(y),
            }
            v = formulas[op]()
        masked = any(a["type"] == "vector_bool" for a in args) and op not in {
            "vsel",
            "vcvt",
            "vbr",
            "vci",
            "vselr",
        }
        if masked and not mask[i]:
            v = old[i] if merge else 0
        # CANN 9.0 beta.1 selects the original destination on enabled lanes
        # for the paired bitwise MERGING wrappers (header VLOGIC_B64).
        if (
            merge
            and is_pair_type(args[0]["type"])
            and op in {"vand", "vor", "vxor", "vnot"}
        ):
            v = old[i] if mask[i] else 0
        if op == "vcvt" and outtype == "fp32":
            result.append(
                round_float32(
                    v, next((s for s in options if s.startswith("ROUND_")), "ROUND_R")
                )
            )
        else:
            result.append(compute.quantize(v, outtype))
    return [result]


def make_batch(cases):
    stride = 4096
    data = bytearray((i * 73 + 29) % 256 for i in range(stride * len(cases)))
    golden = bytearray(data)
    functions = []
    calls = []
    contracts = []
    for number, case in enumerate(cases):
        recipe = case["recipe"]
        base = number * stride
        op = recipe["api"]
        arguments = []
        declarations = []
        formal = []
        actual = []
        values = {}
        outputs = []
        scalar_setup = []
        offset = 0
        is_pair = any("2xvl_" in a["type"] for a in recipe["arguments"])
        n = (
            64
            if is_pair
            else 128
            if any(
                "s16" in a["type"] or "u16" in a["type"] for a in recipe["arguments"]
            )
            else 64
        )
        mask = [i % 5 != 2 for i in range(n)]
        pred_width = 4 if is_pair or n == 64 else 2
        data[base + 3456 : base + 3488] = bitmap(
            pred_width, [i for i in range(n) if mask[i]]
        )
        for arg in recipe["arguments"]:
            p = arg["position"]
            typ = arg["type"]
            name = f"a{p}"
            if arg["config"]:
                arguments.append(recipe["options"][str(p)])
                continue
            if typ == "vector_bool" or typ == "vector_bool &":
                declarations += [f"vector_bool {name};", f"plds({name},pred,0,NORM);"]
                arguments.append(name)
                if "&" in typ:
                    outputs.append((p, "bool", False))
                continue
            dt = dtype_of(typ)
            t = compute.TYPES[dt]
            pair = "2xvl_" in typ
            lanes = 64 if pair else 256 // t.width
            if typ.startswith("vector_"):
                vs = []
                for lane in range(lanes):
                    if "&" in typ:
                        v = 5 + lane % 7
                    elif op in {"vshl", "vshr", "vselr"} and (
                        dt == "int32" or dt == "uint32"
                    ):
                        v = (lane * 7 + 3) % lanes if op == "vselr" else lane % 31 + 1
                    elif op in {"vdiv", "vmod"} and any(
                        a["type"].startswith("vector_")
                        and "&" not in a["type"]
                        and a["type"] != "vector_bool"
                        for a in recipe["arguments"][:p]
                    ):
                        v = lane % 17 + 1
                    elif dt in compute.FLOATING:
                        v = (lane % 19 - 9) + 0.25
                    else:
                        v = (
                            (1 << 36) + (lane * 0x1000011 + 0xFFFFFFF0)
                            if pair
                            else lane * 11 + 3
                        )
                    if (
                        case.get("rounding_boundaries")
                        and op == "vcvt"
                        and pair
                        and "&" not in typ
                    ):
                        v = [16777217, 16777219, 33554434, 33554438][lane % 4]
                    if (
                        t.signed
                        and lane % 3 == 0
                        and "&" not in typ
                        and dt not in compute.FLOATING
                        and not (op in {"vshl", "vshr"} and not pair)
                    ):
                        v = -v
                    vs.append(compute.quantize(v, dt))
                values[p] = vs
                raw = compute.encode(vs, dt)
                data[base + offset : base + offset + len(raw)] = raw
                formal.append(f"__ubuf__ {t.scalar} *in{p}")
                actual.append(f"(__ubuf__ {t.scalar} *)(input+{base + offset})")
                declarations += [
                    f"{typ.replace(' &', '').replace('&', '')} {name};",
                    f"vlds({name},in{p},0);" if pair else f"vlds({name},in{p},0,NORM);",
                ]
                arguments.append(name)
                if "&" in typ:
                    outputs.append((p, dt, pair))
                offset += 512
            else:
                v = (
                    3
                    if op in {"vshls", "vshrs", "vci"}
                    else (1 << 34) + 13
                    if t.width == 8
                    else 7
                )
                if t.signed and op in {"vdiv", "vmod"}:
                    v = -v
                values[p] = compute.quantize(v, dt)
                raw = compute.encode([values[p]], dt)
                data[base + 3584 + 8 * p : base + 3584 + 8 * p + t.width] = raw
                formal.append(f"{t.scalar} {name}")
                actual.append(f"s{number}_{p}")
                scalar_setup.append(
                    f"{t.scalar} s{number}_{p}=*((__ubuf__ {t.scalar} *)(input+{base + 3584 + 8 * p}));"
                )
                arguments.append(name)
        if offset > 3072:
            raise ValueError("input window overflow")
        golden[base : base + stride] = data[base : base + stride]
        formal += ["__ubuf__ uint32_t *pred"]
        actual += [f"(__ubuf__ uint32_t *)(input+{base + 3456})"]
        declarations += [
            "vector_bool full=pset_b8(PAT_ALL),full32=pset_b32(PAT_ALL),one32=pset_b32(PAT_VL1);",
            op + "(" + ",".join(arguments) + ");",
        ]
        expected = reference(recipe, values, mask, n)
        for slot, ((p, dt, pair), result) in enumerate(zip(outputs, expected)):
            if dt == "bool":
                formal.append(f"__ubuf__ uint32_t *out{p}")
                actual.append(f"(__ubuf__ uint32_t *)(out+{base + 512 * slot})")
                declarations.append(f"psts(a{p},out{p},0,NORM);")
                golden[base + 512 * slot : base + 512 * slot + 32] = result
                continue
            t = compute.TYPES[dt]
            formal.append(f"__ubuf__ {t.scalar} *out{p}")
            actual.append(f"(__ubuf__ {t.scalar} *)(out+{base + 512 * slot})")
            storemask = (
                "one32"
                if op in {"vcadd", "vcmax", "vcmin"}
                else "full32"
                if pair
                else "full"
            )
            # MODE_UNKNOWN inactive lanes have no value contract; observe active lanes only.
            unknown = "MODE_UNKNOWN" in recipe["options"].values() or (
                "MODE_ZEROING" in recipe["options"].values()
                and any(a["type"] == "vector_bool" for a in recipe["arguments"])
            )
            if unknown:
                storemask = next(
                    f"a{a['position']}"
                    for a in recipe["arguments"]
                    if a["type"] == "vector_bool"
                )
            declarations.append(
                f"vsts(a{p},out{p},0,NORM_B{32 if pair else t.width * 8},{storemask});"
            )
            if pair:
                declarations[-1] = f"vsts(a{p},out{p},0,{storemask});"
            raw = compute.encode(result, dt)
            if unknown:
                for i in range(len(result)):
                    if mask[i]:
                        golden[
                            base + 512 * slot + i * t.width : base
                            + 512 * slot
                            + (i + 1) * t.width
                        ] = raw[i * t.width : (i + 1) * t.width]
            else:
                golden[base + 512 * slot : base + 512 * slot + len(raw)] = raw
        name = f"probe_{number}_{recipe['name']}"
        functions.append(
            f"__attribute__((always_inline)) inline [aicore] void {name}("
            + ",".join(formal)
            + "){__VEC_SCOPE__{"
            + "\n".join(declarations)
            + "}}"
        )
        calls += scalar_setup + [name + "(" + ",".join(actual) + ");"]
        contracts.append(
            {
                "api": "software_" + op,
                "dtype": "uint8",
                "form": case["name"],
                "recipe_name": recipe["name"],
                "public_api": op,
                "options": recipe["options"],
                "target_count": 0,
                "target_opcodes": [],
                "output_offset": base,
                "output_bytes": 1024,
                "guard_offset": base + 1024,
                "guard_bytes": stride - 1024,
                "exact_bytes": True,
                "recipe_steps": len(recipe["steps"]),
                "inactive_observation": "merging preserves output; nonmerging masked stores observe enabled lanes",
            }
        )
    size = len(data)
    source = (
        "#define __aicore__ [aicore]\n"
        + "\n".join(functions)
        + f"""
extern "C" __global__ __aicore__ void vector_compute(__gm__ uint8_t *inputGM,__gm__ uint8_t *outputGM){{
 __ubuf__ uint8_t *input=(__ubuf__ uint8_t *)get_imm(0),*out=(__ubuf__ uint8_t *)get_imm(0x10000);
 copy_gm_to_ubuf_align_v2(input,inputGM,0,1,{size},0,0,0,0,0,0);copy_gm_to_ubuf_align_v2(out,inputGM,0,1,{size},0,0,0,0,0,0);
 set_flag(PIPE_MTE2,PIPE_V,(event_t)0);wait_flag(PIPE_MTE2,PIPE_V,(event_t)0);set_flag(PIPE_MTE2,PIPE_S,(event_t)1);wait_flag(PIPE_MTE2,PIPE_S,(event_t)1);
 {"".join(calls)}
 set_flag(PIPE_V,PIPE_MTE3,(event_t)0);wait_flag(PIPE_V,PIPE_MTE3,(event_t)0);copy_ubuf_to_gm_align_v2(outputGM,out,0,1,{size},0,0,0);
}}
"""
    )
    return source, bytes(data), bytes(golden), contracts


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rounding-boundaries", action="store_true")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--operations", nargs="+")
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--limit", type=int)
    p.add_argument("--batch-size", type=int, default=8)
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
    recipes = json.loads((compute.ROOT / "configs/cce_lowering.json").read_text())[
        "recipes"
    ]
    cases = [
        {"name": r["name"], "recipe": r, "rounding_boundaries": a.rounding_boundaries}
        for r in recipes
        if not (
            r["api"] == "vcvt"
            and not any("2xvl_" in arg["type"] for arg in r["arguments"])
        )
        if (not a.operations or r["api"] in a.operations)
        and (
            not a.rounding_boundaries
            or r["api"] == "vcvt"
            and r["arguments"][0]["type"].startswith("vector_f32")
        )
    ]
    cases = cases[a.start : None if a.limit is None else a.start + a.limit]
    for i in range(0, len(cases), a.batch_size):
        rows = compute.run_batch(
            cases[i : i + a.batch_size],
            a.out / f"batch_{i // a.batch_size:03}",
            a.cann,
            factory=make_batch,
        )
        failed = [
            dict(
                api=r["public_api"],
                recipe=r["recipe_name"],
                mismatch=r["mismatch_offsets"][:12],
            )
            for r in rows
            if not r["passed"]
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
        # Retain every functional diagnostic and continue the inventory.


if __name__ == "__main__":
    main()
