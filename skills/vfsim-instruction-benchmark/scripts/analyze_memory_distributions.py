"""Extract mode-specific LSU latency from numerical and inverse-layout probes."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path

import analyze_vector_compute as analysis
import vector_compute as compute


def derive(records):
    groups = defaultdict(list)
    for row in records:
        if row["passed"] and row["single_op_sample"]:
            groups[row["api"].upper(), row["mode"]].append(row)
    modes, summaries = {}, []
    for (op, mode), rows in sorted(groups.items()):
        latencies = {
            event["pipeline_latency"] for row in rows for event in row["events"]
        }
        if len(latencies) != 1 or None in latencies:
            continue
        latency = next(iter(latencies))
        modes.setdefault(op, {})[mode] = latency
        summaries.append(
            dict(
                op=op,
                mode=mode,
                latency=latency,
                payload_bytes=rows[0]["payload_bytes"],
                forms=sorted({row["form"] for row in rows}),
                samples=sum(len(row["events"]) for row in rows),
                reference="inverse layout and independent bytes"
                if any(row.get("inverse_roundtrip") for row in rows)
                else "independent byte layout",
                runs=sorted({row["run"] for row in rows}),
            )
        )
    return dict(memory_mode_latency=modes, forms=summaries)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    records, runs = analysis.collect(args.runs)
    parameters = derive(records)
    path = compute.ROOT / "configs/isa.json"
    isa = json.loads(path.read_text())
    changes, conflicts = [], []
    for op, modes in parameters["memory_mode_latency"].items():
        configured = isa["instructions"][op].setdefault("memory_mode_latency", {})
        for mode, latency in modes.items():
            if mode in configured and configured[mode] != latency:
                conflicts.append(
                    dict(
                        op=op, mode=mode, configured=configured[mode], observed=latency
                    )
                )
            elif mode not in configured:
                changes.append(
                    dict(path=f"isa.{op}.memory_mode_latency.{mode}", value=latency)
                )
                configured[mode] = latency
    if args.apply and not conflicts:
        path.write_text(json.dumps(isa, indent=2) + "\n")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            dict(
                parameters=parameters,
                records=records,
                runs=runs,
                changes=changes,
                conflicts=conflicts,
            ),
            indent=2,
        )
        + "\n"
    )
    print(
        json.dumps(
            dict(
                modes=len(parameters["forms"]),
                changes=len(changes),
                conflicts=conflicts,
            )
        )
    )


if __name__ == "__main__":
    main()
