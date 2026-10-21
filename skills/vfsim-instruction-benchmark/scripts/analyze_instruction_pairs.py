"""Extract qualified cross-opcode forwarding and same-port II measurements."""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
import re
from pathlib import Path

import analyze_vector_compute as analysis
import instruction_pairs as probes
import vector_compute as compute


def collect(roots):
    records, runs = [], []
    for root in roots:
        for work in sorted(root.glob("batch_*")):
            if not (work / "output.bin").exists():
                continue
            contracts = json.loads((work / "cases.json").read_text())
            # Numeric experiments do not depend on log spellings. Recognize
            # CAModel's _FP/_INT domain suffixes when reading earlier batches.
            for case in contracts:
                previous, current = case["previous"], case["current"]
                actual = (
                    probes.physical(current)
                    if previous == "vlds"
                    else probes.physical(previous)
                    if current == "vsts"
                    else probes.physical(previous) + probes.physical(current)
                )
                if case["target_opcodes"] != actual:
                    case["original_target_opcodes"] = case["target_opcodes"]
                    case["target_opcodes"] = actual
            rows = compute.analyze(work, contracts, (work / "golden.bin").read_bytes())
            dispatch = {
                int(identity): int(cycle)
                for cycle, identity in re.findall(
                    r"@(\d+)[^\n]*?\binstr_id (\d+)\b",
                    (work / "core0.veccore0.rvec.simd.IDU.DISP.dump").read_text(),
                )
            }
            for row in rows:
                for event in row["events"]:
                    event["dispatch_cycle"] = dispatch.get(event["id"])
            run = root.name + "/" + work.name
            records.extend({**row, "run": run} for row in rows)
            runs.append(
                dict(
                    run=run,
                    source_directory=str(work),
                    sha256={
                        name: analysis.digest(work / name)
                        for name in [
                            "kernel.cce",
                            "cases.json",
                            "commands.json",
                            "input.bin",
                            "golden.bin",
                            "output.bin",
                            "core0.veccore0.instr_popped_log.dump",
                            "core0.veccore0.instr_log.dump",
                            "core0.veccore0.rvec.EXU.dump",
                            "core0.veccore0.rvec.LSU.dump",
                            "core0.veccore0.rvec.GSU.dump",
                            "core0.veccore0.rvec.simd.IDU.DISP.dump",
                        ]
                    },
                )
            )
    return records, runs


def derive(records, isa, ii_table):
    forwarding = defaultdict(list)
    intervals = defaultdict(list)
    compact = []
    for row in records:
        selected = []
        gaps = []
        if row["passed"] and row["single_op_sample"]:
            if row["kind"] == "fwd":
                event_ports = {
                    event["id"]: event.get("port") for event in row.get("events", [])
                }
                for edge in row["forwarding_observations"]:
                    if (
                        edge["producer_op"] in probes.physical(row["previous"])
                        and edge["consumer_op"] in probes.physical(row["current"])
                        and edge["other_sources_done_before_producer"]
                        and edge.get("dependency_reported_by_dispatch", True) is True
                        and edge["gap"] >= 0
                    ):
                        # A single pair value covers each actual source position.
                        if edge["role"] != row["source_role"]:
                            continue
                        value = {
                            **edge,
                            "run": row["run"],
                            "replica": row["replica"],
                            "streams": row.get("streams", 6),
                            "producer_port": event_ports.get(edge["producer_id"]),
                            "consumer_port": event_ports.get(edge["consumer_id"]),
                        }
                        forwarding[
                            row["previous_key"], row["current_key"], edge["role"]
                        ].append(value)
                        selected.append(value)
            else:
                events = {e["id"]: e for e in row["events"]}

                def key(event):
                    return (
                        row["previous_key"]
                        if event["op"] in probes.physical(row["previous"])
                        else row["current_key"]
                    )

                for gap in row["independent_same_port_gaps"]:
                    a, b = events[gap["previous_id"]], events[gap["current_id"]]
                    p, c = key(a), key(b)
                    unit_gap = gap["gap"] == 1
                    if p == c or (
                        not unit_gap and not gap["sources_done_before_previous"]
                    ):
                        continue
                    if (
                        not unit_gap
                        and b.get("dispatch_cycle") is not None
                        and b["dispatch_cycle"] + 2 > a["cycle"]
                    ):
                        continue
                    # Exclude a consumer delayed by an earlier same-op request's
                    # own pipeline interval, which cannot calibrate the cross pair.
                    self_ii = ii_table.get(c, {}).get(c, 1)
                    earlier = [
                        e
                        for e in row["events"]
                        if e["port"] == gap["port"]
                        and e["op"] == b["op"]
                        and e["cycle"] < b["cycle"]
                    ]
                    if (
                        not unit_gap
                        and self_ii > 1
                        and earlier
                        and max(e["cycle"] for e in earlier) + self_ii > a["cycle"] + 1
                    ):
                        continue
                    value = {
                        **gap,
                        "run": row["run"],
                        "previous_key": p,
                        "current_key": c,
                        "previous_cycle": a["cycle"],
                        "current_cycle": b["cycle"],
                        "order": row["order"],
                        "replica": row["replica"],
                        "qualification": "observed_architectural_unit_interval"
                        if unit_gap
                        else "ready_sources_and_clear_pipeline",
                    }
                    intervals[p, c].append(value)
                    gaps.append(value)
        compact.append(
            {
                **{
                    k: v
                    for k, v in row.items()
                    if k
                    not in {
                        "forwarding_observations",
                        "memory_events",
                        "recipe_events",
                        "supporting_compute_events",
                        "independent_same_port_gaps",
                        "port_pressure_events",
                    }
                },
                "qualified_forwarding": selected,
                "qualified_ii": gaps,
            }
        )
    fwd = {}
    ii = {}
    fwd_summary = []
    ii_summary = []
    by_pair = defaultdict(dict)
    for (p, c, role), values in forwarding.items():
        by_pair[p, c][role] = values
    for (p, c), paths in sorted(by_pair.items()):
        consumer = c.split(".")[0].lower()
        required = set(probes.source_roles(consumer))
        minima = {r: min(v["gap"] for v in values) for r, values in paths.items()}
        counts = {
            r: sum(v["gap"] == minima[r] for v in values) for r, values in paths.items()
        }
        complete = required.issubset(minima) and all(counts[r] >= 2 for r in required)
        # STORE burst gaps are upper bounds until a one-pair control reproduces
        # their minima with a drained LSU and all other sources completed.
        if consumer == "vsts":
            complete &= all(
                sum(v["gap"] == minima[r] and v["streams"] == 1 for v in paths[r]) >= 2
                for r in required
            )
        value = max(minima[r] for r in required) if required.issubset(minima) else None
        if complete:
            fwd.setdefault(p, {})[c] = value
        fwd_summary.append(
            dict(
                producer=p,
                consumer=c,
                required_roles=sorted(required),
                role_minima=minima,
                minimum_samples=counts,
                value=value,
                qualified=complete,
                samples=sum(len(v) for v in paths.values()),
            )
        )
    for (p, c), values in sorted(intervals.items()):

        def ports(k):
            op, form = k.split(".", 1)
            placement = isa["instructions"][op]["forms"][form]["dispatch_exu"]
            return {0} if placement == "EXU0_ONLY" else {0, 1}

        expected = ports(p) & ports(c)
        minima = {
            port: min(v["gap"] for v in values if v["port"] == port)
            for port in expected
            if any(v["port"] == port for v in values)
        }
        counts = {
            port: sum(v["port"] == port and v["gap"] == gap for v in values)
            for port, gap in minima.items()
        }
        complete = (
            set(minima) == expected
            and len(set(minima.values())) == 1
            and all(counts[port] >= 2 for port in expected)
        )
        value = next(iter(minima.values())) if complete else None
        if complete:
            ii.setdefault(p, {})[c] = value
        ii_summary.append(
            dict(
                previous=p,
                current=c,
                expected_ports=sorted(expected),
                port_minima=minima,
                minimum_samples=counts,
                value=value,
                qualified=complete,
                samples=len(values),
            )
        )
    return (
        dict(isa={}, forwarding=fwd, ii=ii),
        dict(forwarding=fwd_summary, ii=ii_summary),
        compact,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="append", default=[])
    args = parser.parse_args()
    rows, runs = collect(args.runs)
    isa = json.loads((compute.ROOT / "configs/isa.json").read_text())
    table = json.loads((compute.ROOT / "configs/InitiationInterval.json").read_text())[
        "InitiationInterval"
    ]
    parameters, summaries, records = derive(rows, isa, table)
    changes, conflicts = analysis.apply_parameters(
        parameters, compute.ROOT, False, args.replace
    )
    if args.apply and not conflicts:
        analysis.apply_parameters(parameters, compute.ROOT, True, args.replace)
    report = dict(
        target=dict(
            cann="9.0.0-beta.1",
            soc="Ascend950PR_9599",
            compiler_target="dav-c310-vec",
            npu_arch=3101,
        ),
        parameters=parameters,
        summaries=summaries,
        records=records,
        runs=runs,
        changes=changes,
        conflicts=conflicts,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                cases=len(rows),
                passed=sum(r["passed"] and r["single_op_sample"] for r in rows),
                forwarding=sum(len(v) for v in parameters["forwarding"].values()),
                ii=sum(len(v) for v in parameters["ii"].values()),
                changes=len(changes),
                conflicts=conflicts,
            )
        )
    )
    if args.apply and conflicts:
        raise SystemExit(
            "Configuration unchanged: resolve the reported conflicts first"
        )


if __name__ == "__main__":
    main()
