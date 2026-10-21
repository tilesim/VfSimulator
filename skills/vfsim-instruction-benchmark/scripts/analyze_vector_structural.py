"""Extract A5 structural-vector timing with complete result/source coverage."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

import analyze_vector_compute as analysis
import vector_compute as compute
import vector_structural as structural


def required_paths(api):
    if api in structural.REDUCTIONS:
        return {"Vd->Vn"}
    if api in structural.PAIRS:
        return {f"{result}->{source}" for result in ("Vd", "Vd1") for source in ("Vn", "Vm")}
    if api in structural.SELECT:
        return {"Vd->Vn", "Vd->Vm"}
    paths = {"Vd->Vn", "Vd->Vm", "Pd->Pg"}
    if api in {"vaddcs", "vsubcs"}:
        paths.add("Pd->Pd_in")
    return paths


def derive(records):
    groups = defaultdict(list)
    for row in records:
        defined_lanes = row["api"] != "vcpadd" or row.get("chain_input_lanes") == [4,2]
        if row["api"] in structural.OPERATIONS and row["passed"] and row["single_op_sample"] and defined_lanes:
            groups[(row["api"], row["dtype"])].append(row)
    isa, forwarding, ii, summaries = {}, {}, {}, []
    for (api, dtype), rows in sorted(groups.items()):
        key = api.upper()+"."+dtype
        events = [event for row in rows for event in row["events"]]
        counts = Counter(e["latency"] for e in events)
        ports = sorted({e["port"] for e in events})
        pressured = [r for r in rows if r.get("port_pressure") and len(r["port_pressure_events"]) == 4
                     and {e["port"] for e in r["port_pressure_events"]} == {0}
                     and min(e["cycle"] for e in r["events"]) <=
                         max(e["cycle"] for e in r["port_pressure_events"])+2]
        # Competition does not establish an ISA prohibition, but it constrains
        # the scheduler behavior for this CAModel target/build.
        port_qualified = ports == [0, 1] or (ports == [0] and len(pressured) >= 1)
        if len(counts) == 1 and port_qualified:
            isa[key] = {"latency":next(iter(counts)), "dtype":dtype,
                        "EXU":"SFU" if api in {"vcadd", "vcmax", "vcmin"} else "ALU",
                        "dispatch_exu":"EXU01" if ports == [0, 1] else "EXU0_ONLY"}
        paths, untracked, ready_gaps = defaultdict(list), defaultdict(list), []
        for row in rows:
            for edge in row["forwarding_observations"]:
                if (compute.target_opcode(api, edge["producer_op"])
                    and compute.target_opcode(api, edge["consumer_op"])
                    and edge["other_sources_ready_at_producer_issue"] and edge["gap"] >= 0):
                    path = edge["producer_role"]+"->"+edge["role"]
                    if edge.get("dependency_reported_by_dispatch",True) is True:
                        paths[path].append(edge["gap"])
                    else:
                        untracked[path].append(edge["gap"])
            for gap in row["independent_same_port_gaps"]:
                if gap["gap"] == 1 or (row.get("warm_operands") and gap["sources_ready_before_previous"]):
                    ready_gaps.append(gap)
        required = required_paths(api)
        complete = required.issubset(paths)
        if complete:
            forwarding[key] = {key:max(min(paths[p]) for p in required)}
        if ready_gaps and port_qualified:
            minimum = min(g["gap"] for g in ready_gaps)
            samples = [g for g in ready_gaps if g["gap"] == minimum]
            if minimum == 1 or (len(samples) >= 2 and {g["port"] for g in samples} == set(ports)):
                ii[key] = {key:minimum}
        summaries.append({"key":key, "passed_runs":[r["run"] for r in rows],
                          "sample_count":len(events), "latency_counts":dict(counts),
                          "ports":ports, "port_competition_runs":[r["run"] for r in pressured],
                          "port_qualified":port_qualified,
                          "required_result_source_paths":sorted(required),
                          "self_forwarding_path_minima":{p:min(v) for p,v in sorted(paths.items())},
                          "untracked_dependency_observations":{p:dict(Counter(v)) for p,v in sorted(untracked.items())},
                          "self_forwarding_complete":complete,
                          "self_ii":ii.get(key, {}).get(key),
                          "ready_ii_gap_counts":dict(Counter(g["gap"] for g in ready_gaps))})
    return {"forms":summaries, "isa":isa, "forwarding":forwarding, "ii":ii}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", nargs="+", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=compute.ROOT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replace", action="append", default=[])
    args = parser.parse_args()
    rows, runs = analysis.collect(args.runs)
    for run in runs:
        source=Path(run["source_directory"])/"core0.veccore0.rvec.simd.IDU.DISP.dump"
        if source.exists():
            run["sha256"][source.name]=analysis.digest(source)
    parameters = derive(rows)
    changes, conflicts = analysis.apply_parameters(parameters, args.root, args.apply, args.replace)
    # Target and complete self-edges are sufficient for the checked-in evidence.
    # Full memory/dependency logs stay in the reproducible run archives.
    records = []
    for row in rows:
        record = {k:v for k,v in row.items() if k not in ("memory_events", "forwarding_observations")}
        record["self_forwarding_samples"] = [e for e in row["forwarding_observations"]
            if compute.target_opcode(row["api"], e["producer_op"])
            and compute.target_opcode(row["api"], e["consumer_op"])]
        records.append(record)
    report = {"target":{"cann":"9.0.0-beta.1", "soc":"Ascend950PR_9599",
                         "compiler_target":"dav-c310-vec", "__NPU_ARCH__":3101},
              "runs":runs, "parameters":parameters, "changes":changes,
              "conflicts":conflicts, "records":records,
              "excluded_timing_cases":[{"run":r["run"],"api":r["api"],"dtype":r["dtype"],
                                        "reason":"VCPADD chain must read four then two defined lanes"}
                                       for r in rows if r["api"]=="vcpadd" and r.get("chain_input_lanes")!=[4,2]],
              "config_sha256":{name:analysis.digest(args.root/"configs"/name) for name in (
                  "instruction_catalog.json", "isa.json", "forwarding.json", "InitiationInterval.json")}}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"cases":len(rows), "passed":sum(r["passed"] for r in rows),
                      "forms":len(parameters["forms"]), "latency":len(parameters["isa"]),
                      "forwarding":len(parameters["forwarding"]), "ii":len(parameters["ii"]),
                      "new_fields":len(changes), "conflicts":len(conflicts)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
