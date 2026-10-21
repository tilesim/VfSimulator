"""Extract predicate timing evidence after full-bitmap golden validation."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re


EVENT = re.compile(r"\[info\] \[(\d+)\].*?\(ID:\s*(\d+)\)\s+(RV_\w+)(.*)")
EXU = re.compile(r"\[PERF\] \[(\d+)\].*instr_name (RV_\w+).*instr_id (\d+).*exu_id:(\d+)")
REG = re.compile(r"\b(Pd|Pn|Pm|Pg)\[(\d+)\]")
EXPECTED = {"RV_PNOT": 12, "RV_PXOR": 16, "RV_PAND": 12, "RV_PSEL": 16}


def read_events(path):
    return [dict(cycle=int(m[1]), id=int(m[2]), opcode=m[3], operands=m[4], line=i,
                 pc=int(re.search(r"\(PC:\s*0x([0-9a-fA-F]+)\)",text)[1],16))
            for i, text in enumerate(path.read_text().splitlines(), 1)
            if (m := EVENT.search(text))]


def analyze(work):
    validation = json.loads((work / "validation.json").read_text())
    if not validation["passed"] or (work / "golden.bin").read_bytes() != (work / "output.bin").read_bytes():
        raise ValueError("A passing bytewise golden check is required")
    popped = read_events(work / "core0.veccore0.instr_popped_log.dump")
    vf_regions = [(int(base,16), int(base,16)+4*int(count,16)) for base,count in
                  re.findall(r"VF\s+addr:\s*0x([0-9a-fA-F]+),instr_num:\s*0x([0-9a-fA-F]+)",
                             (work / "core0.veccore0.instr_popped_log.dump").read_text())]
    if len(vf_regions) != 4:
        raise ValueError("Expected four separate VF calls with fusion disabled")
    finished = {(x["id"], x["opcode"]): x for x in read_events(work / "core0.veccore0.instr_log.dump")}
    perf = {}
    port_streams = defaultdict(list)
    for line in (work / "core0.veccore0.rvec.EXU.dump").read_text().splitlines():
        if m := EXU.search(line):
            key = (int(m[3]), m[2])
            perf[key] = (int(m[1]), int(m[4]))
            port_streams[int(m[4])].append(key)
    latest, inputs = {}, {}
    for event in sorted(popped, key=lambda e: e["id"]):
        registers = dict(REG.findall(event["operands"]))
        event["registers"] = registers
        key = (event["id"], event["opcode"])
        inputs[key] = {role: latest.get(register) for role, register in registers.items() if role != "Pd"}
        # PSTI logs its predicate source as Pd; it does not define a register.
        if "Pd" in registers and not event["opcode"].startswith("RV_PST"):
            latest[registers["Pd"]] = key
    result = {"validation": "passed; all bitmap bytes and guards match", "artifacts": str(work), "instructions": {}}
    for opcode, count in EXPECTED.items():
        rows = sorted((e for e in popped if e["opcode"] == opcode), key=lambda e: e["id"])
        if len(rows) != count:
            raise ValueError(f"{opcode}: expected {count} dynamic instructions, got {len(rows)}")
        regions={region for e in rows for region in vf_regions if region[0] <= e['pc'] < region[1]}
        if len(regions) != 1:
            raise ValueError(f"Expected one isolated VF for {opcode}")
        evidence = []
        for e in rows:
            key = (e["id"], opcode)
            issue_perf, port = perf[key]
            if issue_perf != e["cycle"] + 1:
                raise ValueError("Unexpected PERF/popped cycle relationship")
            done = finished[key]
            evidence.append(dict(id=e["id"], issue=e["cycle"], done=done["cycle"],
                                 latency=done["cycle"]-e["cycle"], port=port,
                                 operands=e["operands"], popped_line=e["line"], done_line=done["line"]))
        first = min(e["cycle"] for e in rows[:4])
        first_ready = all(producer is not None and finished[producer]["cycle"] <= first
                          for e in rows[:4] for producer in inputs[(e["id"],opcode)].values())
        independent = defaultdict(list)
        for e in rows[:4]:
            independent[perf[(e["id"],opcode)][1]].append(e)
        ii_samples = []
        for port, stream in independent.items():
            for a,b in zip(sorted(stream,key=lambda x:x["cycle"]), sorted(stream,key=lambda x:x["cycle"])[1:]):
                ka,kb=(a["id"],opcode),(b["id"],opcode)
                if port_streams[port].index(kb) == port_streams[port].index(ka)+1:
                    ii_samples.append(dict(producer_id=a["id"],consumer_id=b["id"],port=port,
                                           gap=b["cycle"]-a["cycle"],all_sources_done_before_stream=first_ready))
        roles = [("source",rows[4:8])]
        if count == 16:
            roles.append(("rhs",rows[8:12]))
        roles.append(("mask",rows[-4:]))
        forwarding = {}
        for role, stream in roles:
            samples = []
            for a,b in zip(stream,stream[1:]):
                previous=(a["id"],opcode)
                consumed=inputs[(b["id"],opcode)]
                dependent=[r for r,p in consumed.items() if p==previous]
                if not dependent:
                    raise ValueError(f"Missing RAW in {opcode} {role} chain")
                ready=all(p is not None and finished[p]["cycle"] <= b["cycle"]
                          for p in consumed.values() if p!=previous)
                samples.append(dict(producer_id=a["id"],consumer_id=b["id"],roles=dependent,
                                    gap=b["cycle"]-a["cycle"],other_sources_done=ready))
            valid=[s["gap"] for s in samples if s["other_sources_done"]]
            forwarding[role]={"samples":samples,"minimum_ready_gap":min(valid) if valid else None}
        # A one-cycle same-port observation proves II=1 even if a different
        # source became ready during this stream. Extra waits cannot reduce a
        # hardware minimum below an actually observed one-cycle gap.
        ii_min=min((s["gap"] for s in ii_samples),default=None)
        role_minima=[r["minimum_ready_gap"] for r in forwarding.values()]
        result["instructions"][opcode.removeprefix("RV_")+".b8"]={
            "latencies":sorted({e["latency"] for e in evidence}),"events":evidence,
            "vf_pc_region":[hex(x) for x in next(iter(regions))],
            "ports":sorted({e["port"] for e in evidence}),
            "independent_ii_samples":ii_samples,"observed_same_port_gap":ii_min,
            "confirmed_self_ii":1 if ii_min==1 else None,
            "self_ii_note":"One-cycle same-port minimum observed" if ii_min==1 else
                            "Further saturation experiments required; larger observed gaps remain bounds",
            "self_forwarding_by_source":forwarding,
            "aggregate_self_forwarding":max(role_minima) if all(n is not None for n in role_minima) else None,
            "forwarding_note":"Pair config uses the maximum of the ready-source role minima; smaller per-role gaps are retained here",
        }
    result["sha256"]={name:hashlib.sha256((work/name).read_bytes()).hexdigest() for name in
                      ("kernel.cce","input.bin","golden.bin","output.bin","commands.json",
                       "core0.veccore0.instr_popped_log.dump","core0.veccore0.instr_log.dump","core0.veccore0.rvec.EXU.dump")}
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifacts",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    result=analyze(args.artifacts)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,indent=2)+"\n")
    for key,value in result["instructions"].items():
        print(key,'latency',value['latencies'],'self-II',value['confirmed_self_ii'],
              'fwd',value['aggregate_self_forwarding'])


if __name__ == "__main__":
    main()
