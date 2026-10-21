"""Translate CANN software wrappers, preserving vector and predicate SSA dependencies."""

from pathlib import Path
import argparse, re, json, sys
from collections import Counter

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG as catalog
from api.common.symbols import normalize_dtype

parser = argparse.ArgumentParser(
    description="Convert verified CANN linear SSA expansions to VFSim recipes"
)
parser.add_argument("--input", type=Path, required=True)
parser.add_argument("--out", type=Path, default=PROJECT / "configs/cce_lowering.json")
cli = parser.parse_args()
root = cli.input
source = json.loads((root / "recipes.json").read_text())


def split(text):
    out = []
    start = 0
    depth = 0
    for i, c in enumerate(text):
        depth += int(c in "<{[(") - int(c in ">}])")
        if c == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return out


def typed(text):
    text = text.strip()
    if text[0] in "<{[":
        depth = 0
        end = 0
        for i, c in enumerate(text):
            depth += int(c in "<{[") - int(c in ">}]")
            if depth == 0:
                end = i + 1
                break
        return text[:end], text[end:].strip()
    a = text.split(None, 1)
    return a[0], a[1]


def scalar_type(t):
    return {"float": "fp32", "half": "fp16", "bfloat": "bf16"}.get(
        t, "int" + t[1:] if t.startswith("i") else t
    )


def kind_type(t, form=""):
    if t == "<256 x i1>":
        return "PredicateRegister", "bool"
    if t.startswith("<"):
        scalar = t.split(" x ")[1].removesuffix(">")
        dt = scalar_type(scalar)
        if form.startswith("uint") and dt.startswith("int"):
            dt = "u" + dt
        return "Register", dt
    return "Scalar", scalar_type(t)


def size(t):
    if t.startswith("<"):
        n, scalar = re.fullmatch(r"<(\d+) x (\w+)>", t).groups()
        return (
            int(n)
            * {"float": 32, "half": 16}.get(
                scalar, int(scalar[1:]) if scalar.startswith("i") else 0
            )
            // 8
        )
    if t.startswith("["):
        n, elem = re.fullmatch(r"\[(\d+) x (.*)\]", t).groups()
        return int(n) * size(elem)
    if t.startswith("{"):
        return sum(size(a) for a in split(t[1:-1]))
    return int(t[1:]) // 8


functions = {}
for path in root.glob("batch_*/*.ll"):
    for name, params, body in re.findall(
        r"define[^\n]*@(vfsim_recipe_\d+)\(([^\n]*)\)[^\n]*\{\n([\s\S]*?)\n\}",
        path.read_text(),
    ):
        functions[name] = (params, body)

missing = Counter()
results = []
for source_row in source:
    if "__attribute__((cce_unif_buff))" in source_row["variant"]["arguments"]:
        continue
    values = {}
    pointers = {}
    steps = []
    temps = {}
    assignments = []
    serial = 0

    def temp(t, form):
        identity = "t" + str(len(temps))
        kind, dt = kind_type(t, form)
        temps[identity] = {"storage": kind, "dtype": dt}
        return identity

    def value(t, v):
        if v.startswith("%"):
            return values[v]
        if v in ("undef", "poison"):
            return {"undefined": True, "storage": kind_type(t)[0]}
        return {"literal": v, "dtype": scalar_type(t)}

    params, body = functions[source_row["name"]]
    params = split(params)
    formals = [a for a in source_row["args"] if not a["config"]]
    for arg, param in zip(formals, params):
        symbol = re.search(r"(%\d+)$", param)[1]
        pos = arg["position"]
        if param.startswith("ptr"):
            pointers[symbol] = (pos, 0)
        else:
            values[symbol] = {"argument": pos}
    for raw in body.splitlines():
        line = raw.strip().split(", !")[0]
        if (
            not line
            or line.startswith(";")
            or line.endswith(":")
            or "@llvm.dbg." in line
        ):
            continue
        if line.startswith("ret "):
            continue
        if line.startswith("store "):
            item, ptr, *_ = split(line[6:])
            t, v = typed(item)
            pointer = ptr.split()[-1]
            pos, offset = pointers[pointer]
            assignments.append(
                {
                    "destination": {
                        "argument": pos,
                        **(
                            {"part": offset // 256}
                            if "2xvl"
                            in formals[
                                next(
                                    i
                                    for i, a in enumerate(formals)
                                    if a["position"] == pos
                                )
                            ]["type"]
                            else {}
                        ),
                    },
                    "source": value(t, v),
                }
            )
            continue
        match = re.match(r"(%[\w.$-]+) = (.*)", line)
        if not match:
            raise ValueError((source_row["name"], line))
        symbol, operation = match.groups()
        if operation.startswith("load "):
            t, ptr, *_ = split(operation[5:])
            pos, offset = pointers[ptr.split()[-1]]
            arg = next(a for a in formals if a["position"] == pos)
            values[symbol] = {
                "argument": pos,
                **({"part": offset // 256} if "2xvl" in arg["type"] else {}),
            }
        elif operation.startswith("getelementptr "):
            rest = operation[len("getelementptr ") :].removeprefix("inbounds ")
            t, ptr, *indices = split(rest)
            pos, offset = pointers[ptr.split()[-1]]
            nums = [int(x.split()[-1]) for x in indices]
            if len(nums) == 1:
                offset += size(t) * nums[0]
            elif nums[0] == 0 and len(nums) == 2:
                offset += 256 * nums[1]
            else:
                raise ValueError(operation)
            pointers[symbol] = (pos, offset)
        elif operation.startswith("bitcast "):
            left, _ = operation[len("bitcast ") :].rsplit(" to ", 1)
            t, v = typed(left)
            values[symbol] = value(t, v)
        elif operation.startswith("extractvalue "):
            aggregate, index = split(operation[len("extractvalue ") :])
            _, v = typed(aggregate)
            values[symbol] = values[v][int(index)]
        elif operation.startswith(("trunc ", "lshr ", "shl ", "sub ", "add ")):
            op = operation.split()[0]
            rest = operation[len(op) + 1 :]
            if op == "trunc":
                left, target = rest.rsplit(" to ", 1)
                t, v = typed(left)
                values[symbol] = {
                    "expression": "trunc",
                    "dtype": scalar_type(target),
                    "operands": [value(t, v)],
                }
            else:
                rest = re.sub(r"^(nuw |nsw |exact )+", "", rest)
                left, right = split(rest)
                t, v = typed(left)
                values[symbol] = {
                    "expression": op,
                    "dtype": scalar_type(t),
                    "operands": [value(t, v), value(t, right)],
                }
        elif "call " in operation:
            match = re.search(r"call (.*?) @llvm.hivm.([\w.]+)\((.*)\)", operation)
            if not match:
                raise ValueError(operation)
            returned, intrinsic, args = match.groups()
            args = [typed(x) for x in split(args)] if args else []
            parts = intrinsic.split(".")
            base = parts[0]
            code = base.upper()
            form = ""
            if base in ("vcmp", "vcmps"):
                code += "_" + parts[1].upper()
                form = str(normalize_dtype(parts[2]))
            elif base.startswith("vcvt"):
                match = re.fullmatch(r"(f\d+|s\d+|u\d+)2(f\d+|s\d+|u\d+)", parts[1])
                form = match[1] + "_to_" + match[2]
                code = "VCVT_" + form.upper()
            elif base in ("pset", "pge"):
                code = "PSET_" + parts[1].upper()
                form = parts[1]
            elif base in ("pintlv", "pdintlv"):
                form = parts[1]
            elif base.startswith("p"):
                form = "b8"
            else:
                match = next(
                    (
                        re.fullmatch(r"v?\d*(s\d+|u\d+|f\d+|bf16)", p)
                        for p in parts[1:]
                        if re.fullmatch(r"v?\d*(s\d+|u\d+|f\d+|bf16)", p)
                    ),
                    None,
                )
                if base == "vbr":
                    form = str(normalize_dtype(parts[1]))
                elif match:
                    form = str(normalize_dtype(match[1]))
                else:
                    raise ValueError(intrinsic)
                if base == "vmov" and parts[-1] == "m":
                    code = "VMOV_MERGE"
                if base in ("vdup", "vdups", "vdupm"):
                    code = (
                        "VDUP_HIGHEST"
                        if base == "vdupm"
                        else "VDUPS"
                        if base == "vdups"
                        else "VDUP"
                    )
                    if parts[-1] != "z":
                        code += "_MERGE"
                if base == "vadd" and parts[-1] == "m":
                    code = "VADD_MERGE_NATIVE"
            spec = catalog.lookup(code)
            ret_types = (
                split(returned[1:-1]) if returned.startswith("{") else [returned]
            )
            output_refs = [temp(t, form) for t in ret_types]
            operands = [value(t, v) for t, v in args]
            if code == "VADD_MERGE_NATIVE":
                intermediate = temp(ret_types[0], form)
                steps.append(
                    {
                        "opcode": "VADD",
                        "form": form,
                        "inputs": operands[1:],
                        "outputs": [intermediate],
                        "intrinsic": intrinsic,
                    }
                )
                steps.append(
                    {
                        "opcode": "VMOV_MERGE",
                        "form": form,
                        "inputs": [operands[0], intermediate, operands[-1]],
                        "outputs": output_refs,
                        "intrinsic": intrinsic,
                    }
                )
                values[symbol] = output_refs[0]
                continue
            if spec:
                wanted = list(spec.input_operands)
                selected = []
                unused = list(enumerate(zip(args, operands)))
                for operand in wanted:
                    expect = (
                        "PredicateRegister"
                        if operand.kind.value == "predicate"
                        else "Scalar"
                        if operand.kind.value == "scalar"
                        else None
                    )
                    found = next(
                        (
                            i
                            for i, (idx, ((t, v), ref)) in enumerate(unused)
                            if (
                                expect is None
                                and kind_type(t)[0] in ("Register", "Scalar")
                            )
                            or kind_type(t)[0] == expect
                        ),
                        None,
                    )
                    if found is None:
                        raise ValueError((code, wanted, args))
                    idx, ((t, v), ref) = unused.pop(found)
                    selected.append(ref)
                # Resolve arbitrary IR undef lanes by another same-kind input. All defined
                # outputs retain their source dependencies; no initialization op is added.
                for i, ref in enumerate(selected):
                    if isinstance(ref, dict) and ref.get("undefined"):

                        def storage_of(r):
                            if isinstance(r, str):
                                return temps[r]["storage"]
                            if "argument" in r:
                                typ = next(
                                    a["type"]
                                    for a in formals
                                    if a["position"] == r["argument"]
                                )
                                return (
                                    "PredicateRegister"
                                    if typ.replace("&", "").strip() == "vector_bool"
                                    else "Register"
                                    if typ.startswith("vector_")
                                    else "Scalar"
                                )
                            return r.get("storage", "Scalar")

                        replacement = next(
                            (
                                r
                                for r in selected
                                if not (isinstance(r, dict) and r.get("undefined"))
                                and storage_of(r) == ref["storage"]
                            ),
                            None,
                        )
                        if replacement is None:
                            raise ValueError((code, "unresolved undef"))
                        selected[i] = replacement
                available = list(enumerate(ret_types))
                ordered = []
                for output in spec.output_operands:
                    expected = (
                        "PredicateRegister"
                        if output.kind.value == "predicate"
                        else "Register"
                    )
                    found = next(
                        (
                            i
                            for i, (idx, t) in enumerate(available)
                            if kind_type(t, form)[0] == expected
                        ),
                        None,
                    )
                    if found is None:
                        raise ValueError((code, ret_types))
                    idx, t = available.pop(found)
                    ordered.append(output_refs[idx])
            else:
                missing[code + "." + form] += 1
                selected = operands
                ordered = output_refs
            attributes = {}
            if code.startswith("PSET_"):
                attributes["pattern_code"] = int(args[0][1])
            if code.startswith("VCVT_"):
                attributes["configuration_codes"] = ",".join(
                    v for t, v in args if re.fullmatch("i\\d+", t)
                )
            steps.append(
                {
                    "opcode": code,
                    "form": form,
                    "inputs": selected,
                    "outputs": ordered,
                    "intrinsic": intrinsic,
                    "attributes": attributes,
                }
            )
            values[symbol] = output_refs if returned.startswith("{") else output_refs[0]
        else:
            raise ValueError((source_row["name"], operation))
    results.append(
        {
            "api": source_row["api"],
            "name": source_row["name"],
            "arguments": source_row["args"],
            "options": source_row["options"],
            "temporaries": temps,
            "steps": steps,
            "assignments": assignments,
        }
    )
if missing:
    raise ValueError(dict(missing))
cli.out.write_text(
    json.dumps(
        {
            "target": json.loads((root / "provenance.json").read_text()),
            "recipes": results,
        },
        indent=2,
    )
    + "\n"
)
print(
    "recipes",
    len(results),
    "steps",
    sum(len(r["steps"]) for r in results),
    "missing",
    dict(missing),
)
