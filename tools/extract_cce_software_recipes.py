"""Reproduce the CANN frontend IR used by generate_cce_software_lowering.py."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from itertools import product
import argparse, hashlib, json, re, subprocess

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(
    description="Extract CANN 3101 software wrappers into optimized SSA recipes"
)
parser.add_argument("--out", type=Path, required=True)
parser.add_argument("--cann", type=Path, required=True)
args = parser.parse_args()
out = args.out
out.mkdir(parents=True, exist_ok=True)
cann = args.cann
report = json.loads((root / "docs/instruction_coverage/a5/coverage.json").read_text())


def split(text):
    result = []
    depth = 0
    start = 0
    for i, c in enumerate(text):
        if c in "(<[":
            depth += 1
        elif c in ")>]":
            depth -= 1
        elif c == "," and not depth:
            result.append(text[start:i].strip())
            start = i + 1
    result.append(text[start:].strip())
    return result


recipes = []
candidates = []
for row in report["apis"]:
    for variant in row["variants"]:
        if "2xvl_" in variant["arguments"] or (
            row["api"] in {"vdiv", "vmod"}
            and not any(x in variant["arguments"] for x in {"vector_f16", "vector_f32"})
        ):
            candidates.append((row, variant))
for row in report["apis"]:
    for variant in row["variants"]:
        if (
            row["api"] == "vcvt"
            and "vector_bool" not in variant["arguments"]
            and "2xvl_" not in variant["arguments"]
        ):
            candidates.append((row, variant))
for row, variant in candidates:
    args = []
    options = []
    for position, raw in enumerate(split(variant["arguments"])):
        raw = raw.replace("__attribute__((cce_unif_buff))", "__ubuf__")
        declaration = raw.split("=")[0].strip()
        match = re.match(r"(.+?)\b(\w+)\s*$", declaration)
        assert match, declaration
        typ, name = match.groups()
        typ = typ.strip()
        default = (
            raw.split("=", 1)[1].strip().replace("__cce_simd::", "")
            if "=" in raw
            else None
        )
        config = bool(re.fullmatch(r"T\w*", typ))
        if config:
            key = name.lower()
            if key == "mode":
                mode_values = (
                    ("MODE_UNKNOWN", "MODE_MERGING")
                    if row["api"] == "vcvt"
                    and "vector_bool" not in variant["arguments"]
                    and "2xvl_" not in variant["arguments"]
                    else ("MODE_ZEROING", "MODE_MERGING")
                )
                values = [v for v in mode_values if v in variant["options"]] or [
                    default or "MODE_UNKNOWN"
                ]
            elif key in ("pos", "position"):
                values = ["POS_LOWEST", "POS_HIGHEST"]
            elif key in ("rnd", "round"):
                values = [
                    v
                    for v in ("ROUND_R", "ROUND_A", "ROUND_F", "ROUND_C", "ROUND_Z")
                    if v in variant["options"]
                ] or [default or "ROUND_R"]
            elif key in ("sat", "rs"):
                values = (
                    ["RS_ENABLE", "RS_DISABLE"]
                    if "RS_ENABLE" in variant["options"]
                    else ["RS_DISABLE"]
                )
            elif key in ("addresstype", "adresstype"):
                values = ["ADDRESS_ALIGNED", "ADDRESS_UNALIGNED"]
            elif key == "part":
                values = ["PART_EVEN", "PART_ODD"]
            elif key == "pp":
                values = ["PART_P0", "PART_P1", "PART_P2", "PART_P3"]
            else:
                values = [default] if default else []
            if not values:
                raise ValueError((row["api"], name, variant["arguments"]))
            options.append((position, values))
        args.append(
            dict(
                position=position,
                name=name,
                type=typ,
                config=config,
                default=default,
            )
        )
    for values in product(*(v for _, v in options)):
        selected = dict(zip((i for i, _ in options), values))
        index = len(recipes)
        name = f"vfsim_recipe_{index:04d}"
        params = [a["type"] + " " + a["name"] for a in args if not a["config"]]
        actual = [selected.get(a["position"], a["name"]) for a in args]
        src = (
            'extern "C" [aicore] __attribute__((noinline)) void '
            + name
            + "("
            + ",".join(params)
            + "){"
            + row["api"]
            + "("
            + ",".join(actual)
            + ");}"
        )
        recipes.append(
            dict(
                name=name,
                api=row["api"],
                variant=variant,
                args=args,
                options=selected,
                source=src,
            )
        )
(out / "recipes.json").write_text(json.dumps(recipes, indent=2))
groups = [recipes[i : i + 20] for i in range(0, len(recipes), 20)]


def compile_group(item):
    i, group = item
    p = out / f"batch_{i:03d}"
    p.mkdir(exist_ok=True)
    stem = f"recipes_{i:03d}"
    (p / (stem + ".cce")).write_text("\n".join(r["source"] for r in group) + "\n")
    bc = p / (stem + "-cce-hiipu64-hisilicon-cce-dav-c310-vec.bc")
    command = [
        str(cann / "bin/ccec"),
        "-std=c++17",
        "-O2",
        "-c",
        "-save-temps",
        stem + ".cce",
        "-o",
        stem + ".o",
        "--cce-aicore-arch=dav-c310-vec",
        "--cce-aicore-only",
    ]
    proc = subprocess.run(command, cwd=p, capture_output=True, text=True, timeout=180)
    (p / "compile.log").write_text(proc.stdout + proc.stderr)
    if bc.exists():
        command = [
            str(cann / "bin/bisheng"),
            "-cc1",
            "-x",
            "ir",
            "-triple",
            "hiipu64-hisilicon-cce",
            "-emit-llvm",
            "-O2",
            str(bc),
            "-o",
            str(p / (stem + ".ll")),
        ]
        result = subprocess.run(command, capture_output=True, text=True, timeout=180)
        (p / "optimize.log").write_text(result.stdout + result.stderr)
        return dict(
            batch=i,
            functions=len(group),
            frontend=True,
            optimized=result.returncode == 0,
        )
    return dict(batch=i, functions=len(group), frontend=False, error=proc.stderr[:300])


with ThreadPoolExecutor(max_workers=2) as pool:
    for row in pool.map(compile_group, enumerate(groups)):
        print(json.dumps(row), flush=True)
resource = Path(
    subprocess.check_output(
        [str(cann / "bin/ccec"), "-print-resource-dir"], text=True
    ).strip()
)
headers = [
    "__clang_cce_vector_intrinsics.h",
    "npu_arch_3101/__clang_cce_vector_intrinsics.h",
]
provenance = {
    "cann": "9.0.0-beta.1",
    "compiler_target": "dav-c310-vec",
    "__NPU_ARCH__": 3101,
    "headers_sha256": {
        name: hashlib.sha256((resource / "include" / name).read_bytes()).hexdigest()
        for name in headers
    },
    "scope": "optimized frontend LLVM IR; final backend scheduling and register copies may differ",
}
(out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
print("recipes", len(recipes), flush=True)
