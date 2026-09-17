#!/usr/bin/env python3
"""Reproduce the address-state-only AABBCC experiment without CAmodel tools."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import CanonicalLoop, canonical_vf_info_to_dict
from api.simulator_costmodel import CoreVfCostModel


def source(unroll, post):
    declarations = "".join(f"vector_f32 a{n}, b{n}, c{n}, t{n}, y{n};" for n in range(unroll))
    stages = [[] for _ in range(6)]
    for n in range(unroll):
        offset = "64" if post else f"64*(i+{n})"
        update = ",POST_UPDATE" if post else ""
        stages[0].append(f"vlds(a{n},p,{offset},NORM{update});")
        stages[1].append(f"vlds(b{n},q,{offset},NORM{update});")
        stages[2].append(f"vadd(t{n},a{n},b{n},mask);")
        stages[3].append(f"vlds(c{n},r,{offset},NORM{update});")
        stages[4].append(f"vmul(y{n},t{n},c{n},mask);")
        stages[5].append(f"vsts(y{n},s,{offset},NORM_B32,mask{update});")
    body = "\n".join(line for stage in stages for line in stage)
    return ("void probe(__ubuf__ float *p, __ubuf__ float *q, __ubuf__ float *r, __ubuf__ float *s) {\n"
            "__VEC_SCOPE__ {\nvector_bool mask=pset_b32(PAT_ALL);\n" + declarations +
            f"\nfor(int i=0;i<64;i+={unroll}) {{\n" + body + "\n}\n}\n}\n")


def without_address_state(vf):
    def convert(node):
        if isinstance(node, CanonicalLoop):
            return replace(node, body=tuple(convert(n) for n in node.body))
        def operand(op):
            if op.memory_access is None:
                return op
            return replace(op, memory_access=replace(op.memory_access, address_state_id=None,
                           update_mode="none", post_update_delta_bytes=None))
        return replace(node, inputs=tuple(operand(o) for o in node.inputs),
                       outputs=tuple(operand(o) for o in node.outputs))
    return replace(vf, context=tuple(convert(n) for n in vf.context))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--native-runner", type=Path)
    args = parser.parse_args()
    rows = []
    for unroll in (1, 2, 4, 8):
        for post in (False, True):
            case = args.out_dir / f"{'post' if post else 'explicit'}_u{unroll}"
            case.mkdir(parents=True, exist_ok=True)
            path = case / "input.cce"
            path.write_text(source(unroll, post))
            vf = parse_cce_canonical_vf_info(path)
            before = CoreVfCostModel(base_dir=ROOT, out_dir=case/"without_address_state").run_canonical_vf_info(
                without_address_state(vf))
            after = CoreVfCostModel(base_dir=ROOT, out_dir=case/"python").run_canonical_vf_info(vf)
            row = {"unroll": unroll, "post_update": post,
                   "without_address_state": before["vf_end_cycle"],
                   "with_address_state": after["vf_end_cycle"]}
            payload_path = case / "canonical.json"
            payload_path.write_text(json.dumps(canonical_vf_info_to_dict(vf), indent=2))
            if args.native_runner:
                proc = subprocess.run([str(args.native_runner), "--trace", str(payload_path),
                                       "--out-dir", str(case/"native")],
                                      capture_output=True, text=True, check=True, timeout=60)
                fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
                row["native"] = int(fields["vfEndCycle"])
                if row["native"] != row["with_address_state"]:
                    raise AssertionError(row)
            rows.append(row)
            print(json.dumps(row), flush=True)
    (args.out_dir / "summary.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
