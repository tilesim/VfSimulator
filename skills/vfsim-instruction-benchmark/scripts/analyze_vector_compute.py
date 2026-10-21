"""Extract qualified vector parameters from completed CAModel probe batches."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path

import vector_compute as probe


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect(roots):
    records, runs = [], []
    for root in roots:
        for work in sorted(root.glob("batch_*")):
            if not (work / "output.bin").exists():
                continue
            contracts = json.loads((work / "cases.json").read_text())
            golden = (work / "golden.bin").read_bytes()
            rows = probe.analyze(work, contracts, golden)
            (work / "validation.json").write_text(json.dumps(rows, indent=2) + "\n")
            run = root.name + "/" + work.name
            runs.append(
                {
                    "run": run,
                    "source_directory": str(work),
                    "sha256": {
                        name: digest(work / name)
                        for name in (
                            "kernel.cce",
                            "input.bin",
                            "golden.bin",
                            "output.bin",
                            "commands.json",
                            "core0.veccore0.instr_popped_log.dump",
                            "core0.veccore0.instr_log.dump",
                            "core0.veccore0.rvec.EXU.dump",
                            "core0.veccore0.rvec.LSU.dump",
                            "core0.veccore0.rvec.GSU.dump",
                            "core0.veccore0.rvec.simd.IDU.DISP.dump",
                        )
                    },
                }
            )
            records.extend({**row, "run": run} for row in rows)
    return records, runs


def required_source_roles(api):
    if api in probe.UNARY | probe.SCALAR:
        return {"Vn"}
    if api in probe.BINARY:
        return {"Vn", "Vm"}
    if api == "vaxpy":
        return {"Vd", "Vn"}
    if api in probe.ACCUMULATE:
        return {"Vd", "Vn", "Vm"}
    if api in probe.COMPARE:
        return {"Pg"}
    return set()


def derive(records):
    groups, memory = defaultdict(list), defaultdict(list)
    bf16_broadcasts = []
    for row in records:
        if row["passed"] and (row["api"], row["dtype"]) == ("vmuls", "bf16"):
            events = row.get("recipe_events", [])
            if sum(e["op"] == "RV_VMUL" for e in events) == row.get("target_count", 7):
                bf16_broadcasts.extend(e for e in events if e["op"] == "RV_VDUPS")
        if not (row["passed"] and row["single_op_sample"]):
            continue
        groups[(row["api"], row["dtype"])].append(row)
        for event in row["memory_events"]:
            if event["op"] in {"RV_VLDS", "RV_VLDI"}:
                memory[("VLDS", row["dtype"])].append((event, row["run"]))
            elif (
                event["op"] in {"RV_VSTS", "RV_VSTI"}
                and event["operands_done_at_issue"]
            ):
                memory[("VSTS", row["dtype"])].append((event, row["run"]))
    isa, forwarding, ii, summaries = {}, {}, {}, []
    for (api, dtype), rows in sorted(groups.items()):
        op, key = api.upper(), api.upper() + "." + dtype
        events = [event for row in rows for event in row["events"]]
        latencies = Counter(e["latency"] for e in events)
        ports = sorted({e["port"] for e in events})
        if len(latencies) == 1 and ports == [0, 1]:
            isa[key] = {
                "latency": next(iter(latencies)),
                "EXU": "SFU" if api in probe.SFU else "ALU",
                "dispatch_exu": "EXU01",
                "dtype": dtype,
            }
        roles, stores = defaultdict(list), defaultdict(list)
        warm_ii, unit_ii = [], []
        for row in rows:
            for edge in row["forwarding_observations"]:
                if not (
                    edge.get(
                        "other_sources_ready_at_producer_issue",
                        edge["other_sources_done_before_producer"],
                    )
                    and edge["gap"] >= 0
                ):
                    continue
                producer = probe.target_opcode(api, edge["producer_op"])
                consumer = probe.target_opcode(api, edge["consumer_op"])
                if producer and consumer:
                    roles[edge["role"]].append(edge["gap"])
                elif producer and edge["consumer_op"] in {"RV_VSTS", "RV_VSTI"}:
                    stores["VSTS." + dtype].append(edge["gap"])
                elif producer and edge["consumer_op"] in {"RV_PSTS", "RV_PSTI"}:
                    stores["PSTS.b8"].append(edge["gap"])
            for gap in row["independent_same_port_gaps"]:
                if gap["gap"] == 1:
                    unit_ii.append(gap)
                if row.get("warm_operands") and gap.get(
                    "sources_ready_before_previous", gap["sources_done_before_previous"]
                ):
                    warm_ii.append(gap)
        expected = required_source_roles(api)
        complete = bool(expected) and expected.issubset(roles)
        if complete:
            # The pair schema has one value for every source position.
            forwarding.setdefault(key, {})[key] = max(min(roles[r]) for r in expected)
        # Burst stores add LSU/decode constraints. Retain these bounds for a
        # dedicated single-pair sweep instead of committing them as forwarding.
        if unit_ii:
            ii[key] = {key: 1}
        elif warm_ii:
            minimum = min(g["gap"] for g in warm_ii)
            samples = [g for g in warm_ii if g["gap"] == minimum]
            if len(samples) >= 2 and {g["port"] for g in samples} == {0, 1}:
                ii[key] = {key: minimum}
        summaries.append(
            {
                "key": key,
                "passed_runs": [r["run"] for r in rows],
                "sample_count": len(events),
                "latency_counts": dict(latencies),
                "ports": ports,
                "self_forwarding_source_minima": {
                    role: min(gaps) for role, gaps in sorted(roles.items())
                },
                "self_forwarding_required_sources": sorted(expected),
                "self_forwarding_complete": complete,
                "store_forwarding_observed_bounds": {
                    consumer: min(gaps) for consumer, gaps in sorted(stores.items())
                },
                "self_ii": ii.get(key, {}).get(key),
                "unit_gap_samples": len(unit_ii),
                "warm_ready_gap_counts": dict(Counter(g["gap"] for g in warm_ii)),
            }
        )
    for (op, dtype), values in sorted(memory.items()):
        latencies = {event["latency"] for event, _ in values}
        if len(latencies) == 1:
            isa[op + "." + dtype] = {"latency": next(iter(latencies)), "dtype": dtype}
    if (
        len(bf16_broadcasts) >= 2
        and len({e["latency"] for e in bf16_broadcasts}) == 1
        and {e["port"] for e in bf16_broadcasts} == {0, 1}
    ):
        isa["VDUP.bf16"] = {
            "latency": bf16_broadcasts[0]["latency"],
            "dtype": "bf16",
            "EXU": "ALU",
            "dispatch_exu": "EXU01",
        }
    return {
        "forms": summaries,
        "isa": isa,
        "forwarding": forwarding,
        "ii": ii,
        "bf16_scalar_broadcast_samples": bf16_broadcasts,
    }


def apply_parameters(parameters, root, apply, replace=()):
    paths = {
        name: root / "configs" / name
        for name in ("isa.json", "forwarding.json", "InitiationInterval.json")
    }
    documents = {name: json.loads(path.read_text()) for name, path in paths.items()}
    changes, conflicts = [], []

    def merge(destination, name, value, path):
        if name in destination:
            if destination[name] != value:
                if path in replace:
                    changes.append(
                        {"path": path, "previous": destination[name], "value": value}
                    )
                    destination[name] = value
                else:
                    conflicts.append(
                        {
                            "path": path,
                            "configured": destination[name],
                            "observed": value,
                        }
                    )
            return
        destination[name] = value
        changes.append({"path": path, "value": value})

    for key, fields in parameters["isa"].items():
        op, form = key.split(".", 1)
        item = documents["isa.json"]["instructions"].setdefault(
            op,
            {
                "op_class": fields.get(
                    "op_class",
                    "LOAD" if op == "VLDS" else "STORE" if op == "VSTS" else "COMPUTE",
                ),
                "forms": {},
            },
        )
        target = item["forms"].setdefault(form, {})
        for field, value in fields.items():
            merge(target, field, value, "isa." + key + "." + field)
    for section, filename, table in [
        ("forwarding", "forwarding.json", "forwarding"),
        ("ii", "InitiationInterval.json", "InitiationInterval"),
    ]:
        for producer, consumers in parameters[section].items():
            target = documents[filename][table].setdefault(producer, {})
            for consumer, value in consumers.items():
                merge(
                    target, consumer, value, section + "." + producer + "->" + consumer
                )
    if apply:
        for name, path in paths.items():
            path.write_text(
                json.dumps(documents[name], indent=2, ensure_ascii=False) + "\n"
            )
    return changes, conflicts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=probe.ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--replace",
        action="append",
        default=[],
        help="Exact reported parameter path approved for recalibration",
    )
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    records, runs = collect(args.runs)
    parameters = derive(records)
    changes, conflicts = apply_parameters(
        parameters, args.root, args.apply, args.replace
    )
    if args.compact:
        compact = []
        for row in records:
            item = {
                key: value
                for key, value in row.items()
                if key not in ("memory_events", "forwarding_observations")
            }
            item["self_forwarding_samples"] = [
                edge
                for edge in row["forwarding_observations"]
                if probe.target_opcode(row["api"], edge["producer_op"])
                and probe.target_opcode(row["api"], edge["consumer_op"])
            ]
            item["memory_latency_counts"] = {
                op: dict(
                    Counter(e["latency"] for e in row["memory_events"] if e["op"] == op)
                )
                for op in sorted({e["op"] for e in row["memory_events"]})
            }
            compact.append(item)
        records = compact
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
        "config_sha256": {
            name: digest(args.root / "configs" / name)
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
                "cases": len(records),
                "passed": sum(r["passed"] for r in records),
                "forms": len(parameters["forms"]),
                "new_fields": len(changes),
                "conflicts": len(conflicts),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
