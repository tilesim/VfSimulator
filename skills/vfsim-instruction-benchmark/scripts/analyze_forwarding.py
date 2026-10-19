"""Archive CAModel probes and extract issue-to-issue predicate timing evidence."""
import argparse
import json
from pathlib import Path
import re
import shutil


def cycle(line):
    return int(re.search(r"\[(\d+)\]", line)[1])


def analyze(path):
    validation = json.loads((path / "validation.json").read_text())
    popped = (path / "core0.veccore0.instr_popped_log.dump").read_text().splitlines()
    done = (path / "core0.veccore0.instr_log.dump").read_text().splitlines()
    isu = (path / "core0.veccore0.rvec.ISU.dump").read_text().splitlines()
    base = cycle(next(line for line in popped if " VF " in line))
    consumer = next(line for line in popped if " RV_VADD " in line or " RV_VDUPS " in line)
    predicate = re.search(r"Pg\[(\d+)\]", consumer)[1]
    producer = next(line for line in reversed(popped[:popped.index(consumer)])
                    if " RV_PSET " in line and f"Pd[{predicate}]" in line)
    producer_id = int(re.search(r"ID: (\d+)", producer)[1])
    consumer_id = int(re.search(r"ID: (\d+)", consumer)[1])
    retire = next(line for line in done if " RV_PSET " in line
                  and int(re.search(r"ID: (\d+)", line)[1]) == producer_id)
    vf_done = next(line for line in done if "vf_execute_time:" in line)
    return dict(probe=validation["probe"], gap=validation["gap"],
                passed=validation["passed"],
                target_pattern={0: "PAT_ALL", 7: "PAT_VL32"}[int(re.search(r"#pattern=(\d+)", producer)[1])],
                vf_cycles=int(re.search(r"vf_execute_time: (\d+)", vf_done)[1]),
                pset_issue=cycle(producer)-base, pset_retire=cycle(retire)-base,
                consumer_issue=cycle(consumer)-base, issue_gap=cycle(consumer)-cycle(producer),
                load_retire=[cycle(line)-base for line in done if " RV_VLD" in line],
                evidence=[producer, retire, consumer] +
                [line for line in isu if f"instr_id {consumer_id} " in line and
                 ("WAKEUP" in line or "ISU_ISSUE]" in line)])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    reports = []
    for path in args.paths:
        report = analyze(path)
        target = args.out / path.name
        target.mkdir(exist_ok=False)
        for name in ["kernel.cce", "host.cpp", "validation.json", "input.bin", "golden.bin",
                     "output.bin", "compile.log", "run.log", "host_build.log", "link.log"]:
            shutil.copy2(path / name, target / name)
        for suffix in ["instr_log", "instr_popped_log", "rvec.IDU", "rvec.ISU", "rvec.EXU"]:
            name = f"core0.veccore0.{suffix}.dump"
            shutil.copy2(path / name, target / name)
        report["artifact_dir"] = target.name
        reports.append(report)
    (args.out / "summary.json").write_text(json.dumps(reports, indent=2) + "\n")
    for report in reports:
        print(json.dumps({k: v for k, v in report.items() if k != "evidence"}))


if __name__ == "__main__":
    main()
