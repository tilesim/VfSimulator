"""Extract physical LSU profiles and indexed-memory service intervals.

Queue admission and LSU I1 are retained separately. Indexed-memory service
costs describe the recorded index distribution; they are not universal bank-
conflict latencies for arbitrary runtime indices.
"""

from __future__ import annotations
import argparse, json
from collections import defaultdict, Counter
from pathlib import Path
import vector_compute as compute
import analyze_vector_compute as analysis

GSU = {"vgather2", "vgather2_bc", "vgatherb", "vscatter"}


def derive(rows):
    groups = defaultdict(list)
    summary = []
    isa = {}
    forwarding = {}
    intervals = defaultdict(list)
    for r in rows:
        if not r["passed"] or not r["single_op_sample"]:
            continue
        if r["api"] in {"paired_gather", "paired_scatter"}:
            op = "VGATHER2_BYTES" if r["api"] == "paired_gather" else "VSCATTER_BYTES"
            form = "int32" if r["form"] == "int64" else "uint32"
            key = op + "." + form
            groups[key].extend((event, r, False) for event in r["events"])
            intervals[key].extend(
                b["done"] - a["done"] for a, b in zip(r["events"], r["events"][1:])
            )
            continue
        if r["api"].startswith("memory_wrapper_"):
            paired = r["form"].startswith("pair_")
            for e in r["memory_events"]:
                physical = e["op"]
                helper = None
                if paired:
                    helper = {
                        "RV_VSTI": "VSTS_BYTES",
                        "RV_VSTS": "VSTS_BYTES",
                        "RV_VLDAS": "VLDAS_BYTES",
                        "RV_VLDUI": "VLDUS_BYTES",
                        "RV_VLDUS": "VLDUS_BYTES",
                        "RV_VSTUS": "VSTUS_BYTES",
                        "RV_VSTUI": "VSTUS_BYTES",
                        "RV_VSTAS": "VSTAS_BYTES",
                        "RV_VSTAI": "VSTAS_BYTES",
                    }.get(physical)
                    if (
                        r["public_api"] == "vload"
                        and r["mode"] == "ADDRESS_ALIGNED"
                        and physical in {"RV_VLDS", "RV_VLDI"}
                    ):
                        helper = "VLDS_PAIR"
                elif r["public_api"] == "vload" and physical == "RV_VLDAS":
                    helper = "VLDAS"
                if helper:
                    # Tail and zero-count stores have a shorter service path.
                    # The profile records the full-vector reference case.
                    if (
                        helper in {"VSTUS_BYTES", "VSTAS_BYTES"}
                        and r.get("count") != 64
                    ):
                        continue
                    form = "b32" if paired else r["form"]
                    groups[helper + "." + form].append((e, r, True))
            continue
        if r["api"] in GSU:
            events = r["events"]
            op = (
                "VGATHERB_UNMASKED"
                if r["api"] == "vgatherb" and r.get("unmasked")
                else r["api"].upper()
            )
            key = op + "." + r["form"]
            groups[key].extend((e, r, False) for e in events)
            if not r.get("isolated"):
                intervals[key].extend(
                    b["done"] - a["done"] for a, b in zip(events, events[1:])
                )
            continue
        # Public constant-count APIs can select a different physical opcode.
        for e in r["events"]:
            op = {"RV_VLDI": "VLDS", "RV_VSTI": "VSTS", "RV_VSTAR": "VSTAR"}.get(
                e["op"], e["op"].removeprefix("RV_")
            )
            if r["api"] == "vsstb":
                op = "VSSTB"
            if r["api"] == "vsstb" and not r.get("isolated"):
                events = r["events"]
                if e is events[0]:
                    intervals["VSSTB." + r["form"]].extend(
                        b["done"] - a["done"] for a, b in zip(events, events[1:])
                    )
            groups[op + "." + r["form"]].append((e, r, True))
        # The typed append/load wrappers expose their align helper's pointer
        # type directly; these are not arbitrary cast-to-byte helper stores.
        for e in r["memory_events"]:
            if e["id"] in {v["id"] for v in r["events"]}:
                continue
            if r["api"] in {"vldus", "vldui"} and e["op"] == "RV_VLDAS":
                groups["VLDAS." + r["form"]].append((e, r, True))
            if r["api"] in {"vstus", "vstui"} and e["op"] in {"RV_VSTAS", "RV_VSTAI"}:
                groups[e["op"].removeprefix("RV_") + "." + r["form"]].append(
                    (e, r, True)
                )
    catalog = compute.ROOT / "configs/instruction_catalog.json"
    specs = json.loads(catalog.read_text())["instructions"]
    for key, values in sorted(groups.items()):
        op, form = key.split(".", 1)
        if op not in specs or form not in specs[op]["forms"]:
            continue
        gsu = op.removesuffix("_BYTES").removesuffix("_UNMASKED").lower() in GSU
        service = gsu or op == "VSSTB"
        latencies = Counter(
            e.get("pipeline_latency")
            if normal and e.get("pipeline_latency") is not None
            else e["latency"]
            for e, r, normal in values
        )
        latency = None
        if service:
            isolated = [e["latency"] for e, r, _ in values if r.get("isolated")]
            if isolated and len(set(isolated)) == 1:
                latency = isolated[0]
            elif len(values) >= 4:
                latency = min(latencies)
        elif len(latencies) == 1:
            latency = next(iter(latencies))
        elif any(e.get("pipeline_latency") is not None for e, r, _ in values):
            pipe = [
                e["pipeline_latency"]
                for e, r, _ in values
                if e.get("pipeline_latency") is not None
            ]
            if len(set(pipe)) == 1:
                latency = pipe[0]
        if latency is None:
            continue
        dtype = values[0][1].get("result_dtype", form)
        fields = {"latency": latency, "dtype": dtype if op == "VGATHER2" else form}
        interval = None
        if service:
            observations = intervals[key]
            if len(observations) >= 3 and len(set(observations)) == 1:
                interval = observations[0]
                fields.update(
                    lsu_resource="GSU" if gsu else "STU_BLOCK",
                    lsu_issue_interval=interval,
                )
        isa[key] = fields
        summary.append(
            dict(
                key=key,
                samples=len(values),
                runs=sorted({r["run"] for _, r, _ in values}),
                latency_counts=dict(latencies),
                pipeline_latency=latency,
                lsu_issue_interval=interval,
                index_pattern_dependent=gsu,
                block_stride_pattern_dependent=op == "VSSTB",
                qualification="isolated and burst numerical reference"
                if gsu
                else "same-ID LSU stage and completion events",
            )
        )
    return dict(forms=summary, isa=isa, forwarding=forwarding, ii={})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runs", nargs="+", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--apply", action="store_true")
    p.add_argument("--replace", action="append", default=[])
    a = p.parse_args()
    rows, runs = analysis.collect(a.runs)
    parameters = derive(rows)
    # apply_parameters defaults new op_class to COMPUTE; memory profiles carry
    # their physical class explicitly so the configuration remains self-contained.
    specs = json.loads((compute.ROOT / "configs/instruction_catalog.json").read_text())[
        "instructions"
    ]
    for key, fields in parameters["isa"].items():
        fields["op_class"] = specs[key.split(".")[0]]["class"].upper()
    changes, conflicts = analysis.apply_parameters(
        parameters, compute.ROOT, a.apply, a.replace
    )
    out = dict(
        target={
            "cann": "9.0.0-beta.1",
            "soc": "Ascend950PR_9599",
            "compiler_target": "dav-c310-vec",
            "__NPU_ARCH__": 3101,
        },
        runs=runs,
        parameters=parameters,
        changes=changes,
        conflicts=conflicts,
        records=rows,
    )
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(out, indent=2) + "\n")
    print(
        json.dumps(
            dict(
                cases=len(rows),
                passed=sum(r["passed"] for r in rows),
                profiles=len(parameters["isa"]),
                gsu_intervals=sum(
                    "lsu_issue_interval" in r for r in parameters["isa"].values()
                ),
                changes=len(changes),
                conflicts=conflicts,
            )
        )
    )


if __name__ == "__main__":
    main()
