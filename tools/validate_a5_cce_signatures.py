"""Validate one representative CCE call for each installed A5 VF signature.

This checks adapter contracts; numeric CAModel coverage and configuration-option
combinations are recorded separately by the benchmark evidence.
"""

from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.input_api import InputAPI
from api.common.schema import CanonicalInstruction, CanonicalLoop


def split(text):
    out = []
    depth = 0
    start = 0
    for i, ch in enumerate(text):
        depth += (ch in "(<[") - (ch in ")>]")
        if ch == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    return out


def build(row, var):
    params = []
    decl = []
    actual = []
    constants = {}
    args = split(var["arguments"])
    vecs = []
    for text in args:
        if not text:
            continue
        text = text.replace("__attribute__((cce_unif_buff))", "__ubuf__")
        raw = text.split("=")[0].strip()
        m = re.match(r"(.+?)\b(\w+)\s*$", raw)
        typ, name = m.groups()
        typ = typ.strip().replace("__cce_half", "half").replace("*&", "* &")
        default = (
            text.split("=", 1)[1].strip().removeprefix("__cce_simd::")
            if "=" in text
            else None
        )
        if re.fullmatch(r"T\w*", typ):
            options = set(var["options"])
            key = name.lower()
            if row["api"].startswith("pset"):
                pref = ["PAT_ALL"]
            elif row["api"] in {"dhistv2", "chistv2"}:
                pref = ["Bin_N0"]
            elif "post" in key:
                pref = ["POST_UPDATE", "NO_POST_UPDATE"]
            elif key in ("mode", "pmode"):
                pref = (
                    ["MODE_MERGING"]
                    if default == "MODE_MERGING"
                    else ["MODE_UNKNOWN", "MODE_ZEROING", "MODE_MERGING"]
                    if default == "MODE_UNKNOWN"
                    else ["MODE_ZEROING", "MODE_MERGING"]
                )
            elif row["api"].startswith("pset") and key == "dist":
                pref = ["PAT_ALL"]
            elif key in ("dist", "pd"):
                dual = len(
                    [
                        a
                        for a in args
                        if re.search(r"\bvector_(?!bool\b|align\b|address\b)\w+", a)
                    ]
                ) == 2 and row["api"] in {"vld", "vlds", "vst", "vsts"}
                pref = (
                    ["DINTLV_B8", "INTLV_B8", "NORM"] if dual else ["NORM", "NORM_B8"]
                )
            elif key in ("rnd", "round"):
                pref = ["ROUND_R", "ROUND_A", "ROUND_H"]
            elif key in ("rs", "sat"):
                pref = ["RS_ENABLE", "RS_DISABLE"]
            elif key in ("part", "pp"):
                pref = ["PART_EVEN", "PART_P0", "LOWER", "HIGHER"]
            elif "pos" in key:
                pref = ["POS_LOWEST"]
            elif "bin" in key:
                pref = ["Bin_N0"]
            elif "pattern" in key or key in ("pat", "ptn"):
                pref = ["PAT_ALL"]
            elif "order" in key:
                pref = ["INC_ORDER"]
            elif "addr" in key:
                pref = ["ADDRESS_ALIGNED", "ADDRESS_UNALIGNED"]
            else:
                pref = (
                    ["Bin_N0"]
                    if "Bin_N0" in var["options"]
                    else [default]
                    if default
                    else []
                )
            if default and not default.startswith("MODE_UNKNOWN"):
                pref = [default, *pref]
            chosen = next((p for p in pref if p in options), None)
            if row["api"].startswith("pset"):
                chosen = "PAT_ALL"
            if row["api"] in {"dhistv2", "chistv2"}:
                chosen = "Bin_N0"
            if row["api"] == "vst" and not dual:
                chosen = "NORM_B8"
            if chosen is None:
                chosen = next(iter(sorted(options)), default or "NORM")
            actual.append(chosen)
        elif typ.startswith("vector_"):
            clean = typ.replace("&", "").strip()
            vecs.append(clean)
            if clean == "vector_address":
                decl.append(f"{clean} {name}=vag_b32(128);")
            else:
                decl.append(
                    f"{clean} {name}"
                    + ("=pset_b8(PAT_ALL)" if clean == "vector_bool" else "")
                    + ";"
                )
            actual.append(name)
        elif "__ubuf__" in typ:
            params.append(f"{typ} {name}")
            actual.append(name)
        elif typ.startswith("__cce_simd::") or typ.endswith("Pattern"):
            actual.append(
                "PAT_ALL"
                if "PAT_ALL" in var["options"]
                else "Bin_N0"
                if "Bin_N0" in var["options"]
                else "LOWER"
                if "LOWER" in var["options"]
                else sorted(var["options"])[0]
                if var["options"]
                else "0"
            )
        else:
            params.append(f"{typ.replace('&', '')} {name}")
            constants[name] = 4
            actual.append("0" if row["api"] == "movvp" and name == "part" else name)
    api = row["api"]
    ret = var["returns"].strip()
    call = api + "(" + ",".join(actual) + ");"
    if ret == "vector_bool":
        decl.append("vector_bool result__;")
        call = "result__=" + call
    if ret == "vector_address":
        call = (
            "vector_address result__="
            + api
            + "("
            + ",".join(
                "128" if i == len(actual) - 1 else "0" for i in range(len(actual))
            )
            + ");"
        )
    return (
        "void vf("
        + ",".join(params)
        + "){__VEC_SCOPE__{for(int __contract_iter=0;__contract_iter<2;++__contract_iter){"
        + "".join(decl)
        + call
        + "}}}",
        constants,
    )


def instructions(nodes):
    for node in nodes:
        if isinstance(node, CanonicalInstruction):
            yield node
        elif isinstance(node, CanonicalLoop):
            yield from instructions(node.body)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--inventory",
        type=Path,
        default=ROOT / "docs/instruction_coverage/a5/coverage.json",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    records = []
    for row in inventory["apis"]:
        for variant in row["variants"]:
            record = dict(api=row["api"], arguments=variant["arguments"])
            source = ""
            try:
                source, constants = build(row, variant)
                path = args.out / "probe.cce"
                path.write_text(source)
                vf = InputAPI.load_cce(path, loop_params=constants)
                record.update(
                    passed=True,
                    physical_forms=sorted(
                        {
                            node.opcode + "." + node.form
                            for node in instructions(vf.context)
                        }
                    ),
                )
            except (ValueError, TypeError, KeyError) as error:
                record.update(passed=False, error=str(error), source=source)
            records.append(record)
    report = dict(
        scope="one representative configuration per CANN signature; adapter parsing and validation",
        signatures=len(records),
        passed=sum(row["passed"] for row in records),
        records=records,
        config_sha256={
            name: hashlib.sha256((ROOT / "configs" / name).read_bytes()).hexdigest()
            for name in ("instruction_catalog.json", "cce_lowering.json")
        },
    )
    (args.out / "signatures.json").write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                signatures=report["signatures"],
                passed=report["passed"],
                failures=dict(
                    Counter(row["api"] for row in records if not row["passed"])
                ),
            )
        )
    )


if __name__ == "__main__":
    main()
