"""Measure VDUPI parts and record the adapter's partial-write assembly edge."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re

import analyze_vector_compute as analysis
import vector_compute as compute


def derive(records):
    groups = defaultdict(list)
    for row in records:
        if not row["passed"] or not row["single_op_sample"]:
            continue
        for event in row["events"]:
            if event["op"] != "RV_VDUPI":
                continue
            part = int(re.search(r"#part=(\d+)", event["operands"])[1])
            op = "VDUPI_HI" if part else "VDUPI"
            groups[op + "." + row["form"]].append((event, row))
    isa, ii, forms = {}, {}, []
    for key, samples in sorted(groups.items()):
        latencies = {event["latency"] for event, _ in samples}
        ports = {event["port"] for event, _ in samples}
        gaps = []
        by_run_port = defaultdict(list)
        for event, row in samples:
            by_run_port[row["run"], event["port"]].append(event["cycle"])
        for cycles in by_run_port.values():
            cycles.sort()
            gaps.extend(b - a for a, b in zip(cycles, cycles[1:]))
        # Part zero uses the B8 encoding already observed on both ports. Part
        # one is additionally observed under an EXU0 pressure stream.
        family_ports = {
            event["port"]
            for row in records
            if row["passed"]
            for event in row["events"]
            if event["op"] == "RV_VDUPI"
        }
        qualified = family_ports == {0, 1}
        if len(latencies) == 1 and qualified:
            isa[key] = dict(
                latency=next(iter(latencies)),
                dtype=key.split(".")[1],
                EXU="ALU",
                dispatch_exu="EXU01",
            )
        if gaps and min(gaps) == 1:
            ii[key] = {key: 1}
        forms.append(
            dict(
                key=key,
                samples=len(samples),
                ports=sorted(ports),
                latencies=sorted(latencies),
                gaps=sorted(set(gaps)),
                dispatch_policy="VDUPI family ports; high-part EXU0 eligibility is not isolated",
                runs=sorted({row["run"] for _, row in samples}),
            )
        )
    # These edges assemble independent low/high writes into one Canonical SSA
    # definition. Zero is an ordering constraint, not a measured hardware RAW.
    structural = {
        f"VDUPI.{dtype}": {f"VDUPI_HI.{dtype}": 0} for dtype in ("int16", "uint16")
    }
    return dict(
        isa=isa,
        ii=ii,
        forwarding=structural,
        forms=forms,
        structural_edges=structural,
        self_forwarding_applicable=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    records, runs = analysis.collect(args.runs)
    parameters = derive(records)
    changes, conflicts = analysis.apply_parameters(
        parameters, compute.ROOT, args.apply, []
    )
    result = dict(
        runs=runs,
        records=records,
        parameters=parameters,
        changes=changes,
        conflicts=conflicts,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                qualified=len(parameters["isa"]),
                changes=len(changes),
                conflicts=conflicts,
            )
        )
    )


if __name__ == "__main__":
    main()
