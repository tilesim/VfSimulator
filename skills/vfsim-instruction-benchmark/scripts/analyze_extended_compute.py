"""Extract predicate permutation and register rearrangement measurements."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import analyze_vector_compute as analysis
import vector_compute as compute


def required_paths(api):
    if (
        api.startswith("pset_")
        or api.startswith("vdupi")
        or api in {"plt", "pltm", "vbr", "vci", "vdups", "movvp"}
    ):
        return set()
    if api in {"pintlv", "pdintlv"}:
        return {f"{p}->{c}" for p in ("Pd", "Pd1") for c in ("Pn", "Pm")}
    if api in {"ppack", "punpack"}:
        return {"Pd->Pn"}
    if api in {"pand", "por", "pxor", "psel"}:
        return {"Pd->Pn", "Pd->Pm", "Pd->Pg"}
    if api == "pnot":
        return {"Pd->Pn", "Pd->Pg"}
    if api in {"vselr", "vselrv2"}:
        return {"Vd->Vn", "Vd->Vm"}
    if api == "vexpdif":
        return {"Vd->Vn", "Vd->Vm"}
    if api in {"vmov_merge", "vdup_merge", "vdup_highest_merge"}:
        return {"Vd->Vn", "Vd->Vold"}
    if api == "vdups_merge":
        return {"Vd->Vold"}
    if api in {"dhistv2", "chistv2"}:
        return {"Vd->Vn", "Vd->Vold"}
    if api == "vusqz":
        return set()  # A legal VUSQZ destination is zero-initialized.
    return {"Vd->Vn"}


def derive(records):
    groups = defaultdict(list)
    for row in records:
        if row["passed"] and row["single_op_sample"]:
            api = row.get("family", row["api"])
            if row.get("public_api") == "vdups":
                api = "vdups_merge" if row.get("mode") == "MODE_MERGING" else "vdups"
            if api in {"pset", "pge"}:
                api = "pset_" + row["form"]
            groups[(api, row["form"])].append(row)
    isa, forwarding, ii, summaries = {}, {}, {}, []
    for (api, form), rows in sorted(groups.items()):
        key = api.upper() + "." + form
        events = [e for r in rows for e in r["events"]]
        latencies = Counter(e["latency"] for e in events)
        ports = sorted({e["port"] for e in events})
        pressured = [
            r
            for r in rows
            if r.get("port_pressure")
            and len(r["port_pressure_events"]) >= 4
            and {e["port"] for e in r["port_pressure_events"]} == {0}
            and min(e["cycle"] for e in r["events"])
            <= max(e["cycle"] for e in r["port_pressure_events"]) + 2
        ]
        qualified = ports == [0, 1] or (ports == [0] and bool(pressured))
        if len(latencies) == 1 and qualified:
            isa[key] = {
                "latency": next(iter(latencies)),
                "dtype": (
                    "bool" if api.startswith("p") else rows[0].get("result_dtype", form)
                ),
                "EXU": "SFU" if api == "vexpdif" else "ALU",
                "dispatch_exu": "EXU01" if ports == [0, 1] else "EXU0_ONLY",
            }
        paths, ready = defaultdict(list), []
        for row in rows:
            target_ids = {e["id"] for e in row["events"]}
            for edge in row["forwarding_observations"]:
                if (
                    edge["producer_id"] in target_ids
                    and edge["consumer_id"] in target_ids
                    and edge["other_sources_ready_at_producer_issue"]
                    and edge.get("dependency_reported_by_dispatch", True) is True
                    and edge["gap"] >= 0
                ):
                    role = edge["role"]
                    if api in {
                        "vmov_merge",
                        "vdup_merge",
                        "vdup_highest_merge",
                        "dhistv2",
                        "chistv2",
                        "vdups_merge",
                    } and role in {"Vd", "Vm"}:
                        role = "Vold"
                    paths[edge["producer_role"] + "->" + role].append(edge["gap"])
            ready += [
                g
                for g in row["independent_same_port_gaps"]
                if g["gap"] == 1
                or (row.get("warm_operands") and g["sources_ready_before_previous"])
            ]
        required = required_paths(api)
        if api == "vdups_merge" and form == "bf16":
            required = set()
        complete = bool(required) and required.issubset(paths)
        if complete:
            forwarding[key] = {key: max(min(paths[p]) for p in required)}
        if ready and qualified:
            minimum = min(g["gap"] for g in ready)
            samples = [g for g in ready if g["gap"] == minimum]
            if minimum == 1 or (
                len(samples) >= 2 and {g["port"] for g in samples} == set(ports)
            ):
                ii[key] = {key: minimum}
        summaries.append(
            {
                "key": key,
                "passed_runs": [r["run"] for r in rows],
                "sample_count": len(events),
                "latency_counts": dict(latencies),
                "ports": ports,
                "port_competition_runs": [r["run"] for r in pressured],
                "port_qualified": qualified,
                "required_result_source_paths": sorted(required),
                "self_forwarding_path_minima": {
                    p: min(g) for p, g in sorted(paths.items())
                },
                "self_forwarding_complete": complete,
                "self_forwarding_applicable": bool(required),
                "self_ii": ii.get(key, {}).get(key),
                "ready_ii_gap_counts": dict(Counter(g["gap"] for g in ready)),
            }
        )
    return {"forms": summaries, "isa": isa, "forwarding": forwarding, "ii": ii}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=compute.ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="append", default=[])
    args = parser.parse_args()
    rows, runs = analysis.collect(args.runs)
    parameters = derive(rows)
    changes, conflicts = analysis.apply_parameters(
        parameters, args.root, args.apply, args.replace
    )
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
        "records": [{k: v for k, v in r.items() if k != "memory_events"} for r in rows],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "cases": len(rows),
                "passed": sum(r["passed"] for r in rows),
                "forms": len(parameters["forms"]),
                "latency": len(parameters["isa"]),
                "self_forwarding": len(parameters["forwarding"]),
                "self_ii": len(parameters["ii"]),
                "changes": len(changes),
                "conflicts": len(conflicts),
            }
        )
    )


if __name__ == "__main__":
    main()
