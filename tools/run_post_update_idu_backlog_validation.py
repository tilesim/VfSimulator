#!/usr/bin/env python3
"""Run the CAModel backlog probe sources through the canonical Python/Native cores."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict
from api.simulator_costmodel import CoreVfCostModel
from run_post_update_backlog_probe import source


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def signature(folder):
    history = json.loads((folder / "sim_history.json").read_text())
    result = {
        "execution": [(r["id"], r["stream_seq"], r["start"], r["done"])
                      for r in history if r["event"] == "start"],
    }
    for filename in ("idu_to_ooo.json", "idu_address_blocked.json"):
        result[filename] = [
            (r["cy"], r["inst_id"], r["stream_seq"], r["address_dependencies"])
            for r in read_lines(folder / filename)
        ]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--native-runner", type=Path)
    args = parser.parse_args()
    rows = []
    for mode in ("same", "independent", "explicit"):
        case = args.out_dir / mode
        case.mkdir(parents=True, exist_ok=True)
        path = case / f"post_update_backlog_{mode}.cce"
        path.write_text(source(mode, f"post_update_backlog_{mode}"))
        vf = parse_cce_canonical_vf_info(path)
        result = CoreVfCostModel(base_dir=ROOT, out_dir=case / "python").run_vf_info(vf)
        payload = case / "canonical.json"
        payload.write_text(json.dumps(canonical_vf_info_to_dict(vf), indent=2))
        history = json.loads((case / "python/sim_history.json").read_text())
        loads = [r for r in history if r["event"] == "start" and
                 r["op"] == "VLDS" and r["iteration_path"]]
        dispatch = [r for r in read_lines(case / "python/idu_to_ooo.json")
                    if r["op"] == "VLDS" and r["iteration_path"]]
        for r in read_lines(case / "python/idu_to_ooo.json"):
            assert all(r["cy"] >= d["ready_cycle"] for d in r["address_dependencies"])
        if mode == "same":
            assert all(b["cy"] > a["cy"] for a, b in zip(dispatch, dispatch[1:]))
        counts = Counter(r["start"] for r in loads)
        assert len(loads) == 16 and max(counts.values()) == 2, counts
        row = {
            "mode": mode, "vf_end_cycle": result["vf_end_cycle"],
            "dispatch_cycles": [r["cy"] for r in dispatch],
            "load_start_cycles": [r["start"] for r in loads],
            "max_loads_per_cycle": max(counts.values()),
            "address_block_events": len(read_lines(case / "python/idu_address_blocked.json")),
        }
        if args.native_runner:
            proc = subprocess.run([str(args.native_runner), "--trace", str(payload),
                                   "--out-dir", str(case / "native")],
                                  capture_output=True, text=True, check=True, timeout=60)
            fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
            row["native_vf_end_cycle"] = int(fields["vfEndCycle"])
            assert row["native_vf_end_cycle"] == row["vf_end_cycle"]
            assert signature(case / "python") == signature(case / "native")
        rows.append(row)
        print(json.dumps(row), flush=True)
    (args.out_dir / "summary.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    main()
