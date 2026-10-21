"""Extract only validated A5 mixed-dtype conversion and packing parameters."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import analyze_vector_compute as analysis
import vector_compute as compute


def derive(records):
    groups = defaultdict(list)
    for row in records:
        if row["passed"] and row["single_op_sample"]:
            groups[
                (
                    row["api"] if row["api"] in {"vpack", "vunpack"} else "vcvt",
                    row["form"],
                )
            ].append(row)
    isa, forwarding, ii, summaries = {}, {}, {}, []
    for (api, form), rows in sorted(groups.items()):
        opcode = "VCVT_" + form.upper() if api == "vcvt" else api.upper()
        key = opcode + "." + form
        events = [e for row in rows for e in row["events"]]
        latencies = Counter(e["latency"] for e in events)
        ports = sorted({e["port"] for e in events})
        pressured = [
            r
            for r in rows
            if r.get("port_pressure")
            and len(r["port_pressure_events"]) == 4
            and {e["port"] for e in r["port_pressure_events"]} == {0}
            and min(e["cycle"] for e in r["events"])
            <= max(e["cycle"] for e in r["port_pressure_events"]) + 2
        ]
        qualified = ports == [0, 1] or (ports == [0] and bool(pressured))
        if len(latencies) == 1 and qualified:
            isa[key] = {
                "latency": next(iter(latencies)),
                "dtype": rows[0]["result_dtype"],
                "EXU": "ALU",
                "dispatch_exu": "EXU01" if ports == [0, 1] else "EXU0_ONLY",
            }
        gaps, ready_ii = [], []
        for row in rows:
            targets = set(row["target_opcodes"])
            gaps += [
                e["gap"]
                for e in row["forwarding_observations"]
                if e["producer_op"] in targets
                and e["consumer_op"] in targets
                and e["producer_role"] == "Vd"
                and e["role"] == "Vn"
                and e["other_sources_ready_at_producer_issue"]
                and e["gap"] >= 0
            ]
            ready_ii += [
                g
                for g in row["independent_same_port_gaps"]
                if g["gap"] == 1 or g["sources_ready_before_previous"]
            ]
        if gaps:
            forwarding[key] = {key: min(gaps)}
        if ready_ii and qualified:
            minimum = min(g["gap"] for g in ready_ii)
            samples = [g for g in ready_ii if g["gap"] == minimum]
            if minimum == 1 or (
                len(samples) >= 2 and {g["port"] for g in samples} == set(ports)
            ):
                ii[key] = {key: minimum}
        summaries.append(
            {
                "key": key,
                "passed_runs": [r["run"] for r in rows],
                "apis": sorted({r["api"] for r in rows}),
                "sample_count": len(events),
                "latency_counts": dict(latencies),
                "ports": ports,
                "port_competition_runs": [r["run"] for r in pressured],
                "port_qualified": qualified,
                "self_forwarding_path": "Vd->Vn",
                "forwarding_gap_counts": dict(Counter(gaps)),
                "self_ii": ii.get(key, {}).get(key),
                "ready_ii_gap_counts": dict(Counter(g["gap"] for g in ready_ii)),
            }
        )
    return {"forms": summaries, "isa": isa, "forwarding": forwarding, "ii": ii}


def inherit_pack_pairs(parameters, root):
    """Carry explicit B32 pairs to identical 32-to-16 VPACK encodings."""
    aliases = []
    typed = ("VPACK.s32_to_u16", "VPACK.u32_to_u16")
    for section, filename, table in [
        ("forwarding", "forwarding.json", "forwarding"),
        ("ii", "InitiationInterval.json", "InitiationInterval"),
    ]:
        configured = json.loads((root / "configs" / filename).read_text())[table]
        for producer, consumers in configured.items():
            for consumer, value in consumers.items():
                if "VPACK.b32" not in (producer, consumer):
                    continue
                for p in typed if producer == "VPACK.b32" else (producer,):
                    for c in typed if consumer == "VPACK.b32" else (consumer,):
                        target = parameters[section].setdefault(p, {})
                        if c in target and target[c] != value:
                            raise ValueError(
                                f"B32 alias disagrees with measured {p}->{c}"
                            )
                        target[c] = value
                        aliases.append(
                            {
                                "section": section,
                                "producer": p,
                                "consumer": c,
                                "value": value,
                                "origin": producer + "->" + consumer,
                                "basis": "same RV_VPACK B32 encoding; configured pair inherited",
                            }
                        )
    return aliases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=compute.ROOT)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--inherit-pack-b32", action="store_true")
    parser.add_argument("--replace", action="append", default=[])
    args = parser.parse_args()
    rows, runs = analysis.collect(args.runs)
    for run in runs:
        source = (
            Path(run["source_directory"]) / "core0.veccore0.rvec.simd.IDU.DISP.dump"
        )
        if source.exists():
            run["sha256"][source.name] = analysis.digest(source)
    parameters = derive(rows)
    aliases = inherit_pack_pairs(parameters, args.root) if args.inherit_pack_b32 else []
    changes, conflicts = analysis.apply_parameters(
        parameters, args.root, args.apply, args.replace
    )
    records = []
    for row in rows:
        record = {
            k: v
            for k, v in row.items()
            if k not in ("memory_events", "forwarding_observations")
        }
        targets = set(row["target_opcodes"])
        record["self_forwarding_samples"] = [
            e
            for e in row["forwarding_observations"]
            if e["producer_op"] in targets and e["consumer_op"] in targets
        ]
        records.append(record)
    report = {
        "target": {
            "cann": "9.0.0-beta.1",
            "soc": "Ascend950PR_9599",
            "compiler_target": "dav-c310-vec",
            "__NPU_ARCH__": 3101,
        },
        "runs": runs,
        "parameters": parameters,
        "changes": changes,
        "conflicts": conflicts,
        "records": records,
        "inherited_pack_pairs": aliases,
        "config_sha256": {
            name: analysis.digest(args.root / "configs" / name)
            for name in (
                "instruction_catalog.json",
                "isa.json",
                "forwarding.json",
                "InitiationInterval.json",
            )
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                "cases": len(rows),
                "passed": sum(r["passed"] for r in rows),
                "forms": len(parameters["forms"]),
                "latency": len(parameters["isa"]),
                "self_forwarding": sum(
                    f["key"] in parameters["forwarding"].get(f["key"], {})
                    for f in parameters["forms"]
                ),
                "self_ii": sum(f["self_ii"] is not None for f in parameters["forms"]),
                "inherited_pack_pairs": len(aliases),
                "changes": len(changes),
                "conflicts": len(conflicts),
            }
        )
    )


if __name__ == "__main__":
    main()
