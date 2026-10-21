"""Summarize ready-source FP32 issue gaps, actual triples and hardware stalls."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re

import fu_contention as probe


FILES = (
    "kernel.cce",
    "cases.json",
    "commands.json",
    "input.bin",
    "golden.bin",
    "output.bin",
    "core0.veccore0.instr_popped_log.dump",
    "core0.veccore0.instr_log.dump",
    "core0.veccore0.rvec.EXU.dump",
    "core0.veccore0.rvec.ISU.dump",
    "core0.veccore0.rvec.IDU.dump",
    "core0.veccore0.rvec.simd.IDU.DISP.dump",
)


def collect(roots):
    rows, runs = [], []
    for root in roots:
        for work in sorted(root.glob("batch_*")):
            if not (work / "validation.json").exists():
                continue
            batch = probe.analyze(work)
            dispatch = {
                int(identity): int(cycle)
                for cycle, identity in re.findall(
                    r"@(\d+)[^\n]*?\binstr_id (\d+)\b",
                    (work / "core0.veccore0.rvec.simd.IDU.DISP.dump").read_text(),
                )
            }
            blocked = defaultdict(list)
            received = {}
            source_ready = {}
            for number, line in enumerate(
                (work / "core0.veccore0.rvec.ISU.dump").read_text().splitlines(), 1
            ):
                match = re.search(
                    r"\[(\d+)\] \[EXQ([01])\] \[(ISU_\w+)\] instr_name (RV_\w+) instr_id (\d+)",
                    line,
                )
                if not match:
                    continue
                cycle, port, action, op, identity = match.groups()
                identity = int(identity)
                if action == "ISU_RECV":
                    received[identity] = int(cycle)
                if action == "ISU_ISSUE":
                    ready = re.search(r"\bsrc_rdy (\d+)\b", line)
                    if ready:
                        source_ready[identity] = int(ready[1])
                reason = re.search(r"REASON:(\w+)", line)
                if reason:
                    blocked[identity].append(
                        dict(
                            cycle=int(cycle),
                            port=int(port),
                            reason=reason[1],
                            line=number,
                        )
                    )
            for row in batch:
                all_events = {
                    e["id"]: e
                    for name in ["events", "memory_events", "supporting_compute_events"]
                    for e in row[name]
                }
                incoming = defaultdict(list)
                for edge in row["forwarding_observations"]:
                    p = all_events.get(edge["producer_id"])
                    incoming[edge["consumer_id"]].append(
                        dict(
                            producer_id=edge["producer_id"],
                            producer_op=edge["producer_op"],
                            role=edge["role"],
                            done=p.get("done") if p else None,
                        )
                    )
                for e in row["events"]:
                    deps = incoming[e["id"]]
                    e["input_producers"] = deps
                    e["source_done_cycle"] = (
                        max(x["done"] for x in deps)
                        if deps and all(x["done"] is not None for x in deps)
                        else None
                    )
                    e["dispatch_cycle"] = dispatch.get(e["id"])
                    e["exq_recv_cycle"] = received.get(e["id"])
                    e["hardware_source_ready_cycle"] = source_ready.get(e["id"])
                    e["hardware_blocks"] = blocked[e["id"]]
                row["run"] = root.name + "/" + work.name
                rows.append(row)
            runs.append(
                dict(
                    run=root.name + "/" + work.name,
                    source_directory=str(work),
                    sha256={
                        name: hashlib.sha256((work / name).read_bytes()).hexdigest()
                        for name in FILES
                    },
                )
            )
    return rows, runs


def qualified_after(previous, current, unit=False):
    if unit and current["cycle"] - previous["cycle"] == 1:
        return True
    # ISU explicitly reports availability including forwarding. A consumer
    # ready by the next issue slot cannot explain a longer-than-unit gap.
    ready = current.get("hardware_source_ready_cycle")
    deadline = previous["cycle"] + 1
    if ready is None:
        ready = current.get("source_done_cycle")
        deadline = previous["cycle"]
    return (
        ready is not None
        and ready <= deadline
        and current.get("dispatch_cycle") is not None
        and current["dispatch_cycle"] + 2 <= previous["cycle"]
        and current.get("exq_recv_cycle") is not None
        and current["exq_recv_cycle"] <= previous["cycle"] + 1
    )


def resource_counterexamples(rows, self_ii):
    """Idle issue cycles blocked despite ready sources and a satisfied self-II."""
    observations = []
    for row in rows:
        if not (row["passed"] and row["single_op_sample"]):
            continue
        computes = row["events"] + row.get("supporting_compute_events", [])
        occupied = {(e["cycle"], e["port"]) for e in computes}
        for event in row["events"]:
            own = self_ii.get(event["op"])
            if own is None:
                continue
            for block in event["hardware_blocks"]:
                cycle, port = block["cycle"], block["port"]
                if block["reason"] not in {"DST_PORT_LIMIT", "VALU_RESC_CFLT"}:
                    continue
                if (cycle, port) in occupied or cycle >= event["cycle"]:
                    continue
                ready = event.get("hardware_source_ready_cycle")
                if ready is None:
                    ready = event.get("source_done_cycle")
                if (
                    ready is None
                    or ready > cycle
                    or event.get("dispatch_cycle") is None
                    or event["dispatch_cycle"] + 2 > cycle
                    or event.get("exq_recv_cycle") is None
                    or event["exq_recv_cycle"] > cycle
                ):
                    continue
                history = [
                    e
                    for e in row["events"]
                    if e["port"] == port
                    and e["op"] == event["op"]
                    and e["cycle"] < cycle
                ]
                previous = max(history, key=lambda e: e["cycle"]) if history else None
                if previous and previous["cycle"] + own > cycle:
                    continue
                expected_done = cycle + event["latency"]
                conflicts = [
                    dict(id=e["id"], op=e["op"], issue=e["cycle"], done=e["done"])
                    for e in computes
                    if e["port"] == port
                    and e["cycle"] < cycle
                    and e["done"] == expected_done
                ]
                observations.append(
                    dict(
                        run=row["run"],
                        name=row["name"],
                        replica=row["replica"],
                        id=event["id"],
                        op=event["op"],
                        cycle=cycle,
                        actual_issue=event["cycle"],
                        port=port,
                        reason=block["reason"],
                        source_done=event["source_done_cycle"],
                        hardware_source_ready=event.get("hardware_source_ready_cycle"),
                        previous_same_cycle=previous["cycle"] if previous else None,
                        self_ii=own,
                        proposed_done=expected_done,
                        completion_conflicts=conflicts,
                        isu_line=block["line"],
                    )
                )
    return observations


def summarize(rows):
    controls = defaultdict(list)
    for row in rows:
        if (
            not (row["passed"] and row["single_op_sample"])
            or len(set(row["sequence"])) != 1
        ):
            continue
        for gap in row["independent_same_port_gaps"]:
            by_id = {e["id"]: e for e in row["events"]}
            a, b = by_id[gap["previous_id"]], by_id[gap["current_id"]]
            if qualified_after(a, b, unit=True):
                controls[a["op"]].append(
                    dict(
                        gap=gap["gap"],
                        port=gap["port"],
                        run=row["run"],
                        replica=row["replica"],
                        previous_id=a["id"],
                        current_id=b["id"],
                    )
                )
    self_ii = {op: min(v["gap"] for v in values) for op, values in controls.items()}
    adjacent = defaultdict(list)
    triples = defaultdict(list)
    patterns = defaultdict(list)
    for row in rows:
        if not (row["passed"] and row["single_op_sample"]):
            continue
        by_id = {e["id"]: e for e in row["events"]}
        streams = {
            port: sorted(
                (e for e in row["events"] if e["port"] == port),
                key=lambda e: e["cycle"],
            )
            for port in [0, 1]
        }
        accepted = []
        for gap in row["independent_same_port_gaps"]:
            a, b = by_id[gap["previous_id"]], by_id[gap["current_id"]]
            older = [
                e["cycle"]
                for e in streams[gap["port"]]
                if e["op"] == b["op"] and e["cycle"] < b["cycle"]
            ]
            own = self_ii.get(b["op"])
            own_bound = max(older) + own if older and own is not None else None
            ready = qualified_after(a, b, unit=True)
            self_limited = own_bound is not None and own_bound > a["cycle"] + 1
            value = dict(
                gap,
                previous_op=a["op"],
                current_op=b["op"],
                previous_cycle=a["cycle"],
                current_cycle=b["cycle"],
                ready_and_dispatched=ready,
                consumer_self_earliest=own_bound,
                consumer_self_limited=self_limited,
                run=row["run"],
                name=row["name"],
                replica=row["replica"],
            )
            adjacent[a["op"], b["op"]].append(value)
            accepted.append(value)
        observed = []
        for port, stream in streams.items():
            for a, b, c in zip(stream, stream[1:], stream[2:]):
                if a["op"] != c["op"] or a["op"] == b["op"]:
                    continue
                ready = qualified_after(a, b) and qualified_after(a, c)
                value = dict(
                    ops=[a["op"], b["op"], c["op"]],
                    port=port,
                    ids=[e["id"] for e in [a, b, c]],
                    cycles=[e["cycle"] for e in [a, b, c]],
                    gaps=[b["cycle"] - a["cycle"], c["cycle"] - b["cycle"]],
                    outer_gap=c["cycle"] - a["cycle"],
                    sources_ready_by_next_slot=ready,
                    run=row["run"],
                    name=row["name"],
                    replica=row["replica"],
                )
                triples[a["op"], b["op"]].append(value)
                observed.append(value)
        patterns[row["name"]].append(
            dict(
                run=row["run"],
                replica=row["replica"],
                gate_chain=row.get("gate_chain", 0),
                source_sequence=row["sequence"],
                ports={
                    str(p): [
                        dict(id=e["id"], op=e["op"], cycle=e["cycle"], done=e["done"])
                        for e in es
                    ]
                    for p, es in streams.items()
                },
                adjacent=accepted,
                triples=observed,
                stall_reasons=dict(
                    Counter(
                        v["reason"] for e in row["events"] for v in e["hardware_blocks"]
                    )
                ),
            )
        )
    pairs = []
    for (p, c), samples in sorted(adjacent.items()):
        valid = [
            x
            for x in samples
            if x["ready_and_dispatched"]
            and (not x["consumer_self_limited"] or x["gap"] == 1)
        ]
        minimum = min((x["gap"] for x in valid), default=None)
        minima = [x for x in valid if x["gap"] == minimum]
        pairs.append(
            dict(
                previous=p,
                current=c,
                minimum=minimum,
                minimum_samples=len(minima),
                ports=sorted({x["port"] for x in minima}),
                replicas=sorted({x["replica"] for x in minima}),
                gap_counts=dict(Counter(x["gap"] for x in valid)),
                samples=minima,
            )
        )
    return dict(
        self_ii=self_ii,
        self_samples=dict(controls),
        adjacent_pairs=pairs,
        triples=[
            dict(
                outer=a,
                middle=b,
                outer_gap_counts=dict(
                    Counter(
                        x["outer_gap"]
                        for x in values
                        if x["sources_ready_by_next_slot"]
                    )
                ),
                samples=values,
            )
            for (a, b), values in sorted(triples.items())
        ],
        patterns=dict(patterns),
        self_only_counterexamples=resource_counterexamples(rows, self_ii),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    rows, runs = collect(args.runs)
    report = dict(
        target=dict(
            cann="9.0.0-beta.1",
            soc="Ascend950PR_9599",
            compiler="dav-c310-vec",
            dtype="fp32",
        ),
        summary=summarize(rows),
        records=[
            {
                k: v
                for k, v in row.items()
                if k
                not in [
                    "memory_events",
                    "forwarding_observations",
                    "independent_same_port_gaps",
                ]
            }
            for row in rows
        ],
        runs=runs,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                cases=len(rows),
                passed=sum(x["passed"] and x["single_op_sample"] for x in rows),
                self_ii=report["summary"]["self_ii"],
                pairs=[
                    (x["previous"], x["current"], x["minimum"], x["minimum_samples"])
                    for x in report["summary"]["adjacent_pairs"]
                    if x["previous"] != x["current"]
                ],
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
