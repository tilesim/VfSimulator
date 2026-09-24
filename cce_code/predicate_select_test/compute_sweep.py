"""Numerically validate predicate compute probes and retain RV timing evidence."""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--operations", nargs="+")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    operations = [f"{family}_{condition}" for family in ("vcmp", "vcmps")
                  for condition in ("eq", "ne", "gt", "ge", "lt", "le")] + ["pand", "por", "movvp_ones", "movvp_zeros"]
    if args.operations:
        operations = args.operations
    reports = []
    for op in operations:
        case = args.out / op
        case.mkdir()
        run = subprocess.run([sys.executable, str(Path(__file__).with_name("run.py")),
                              "--probe", "predicate_compute", "--operation", op],
                             capture_output=True, text=True, timeout=240)
        (case / "driver.log").write_text(run.stdout + run.stderr)
        match = re.search(r"Artifacts: (.+)", run.stdout)
        report = dict(operation=op, returncode=run.returncode)
        if match:
            work = Path(match[1])
            for name in ["kernel.cce", "validation.json", "input.bin", "golden.bin", "output.bin",
                         "compile.log", "run.log", "host.cpp", "host_build.log", "link.log"]:
                if (work / name).exists():
                    shutil.copy2(work / name, case / name)
            for suffix in ["instr_log", "instr_popped_log", "rvec.EXU", "rvec.ISU", "rvec.IDU"]:
                name = f"core0.veccore0.{suffix}.dump"
                if (work / name).exists():
                    shutil.copy2(work / name, case / name)
            if (case / "validation.json").exists():
                report["numerical_pass"] = json.loads((case / "validation.json").read_text())["passed"]
                popped = (case / "core0.veccore0.instr_popped_log.dump").read_text()
                done = (case / "core0.veccore0.instr_log.dump").read_text()
                events = []
                pattern = r"\[info\] \[(\d+)\].*?\(ID: (\d+)\) (RV_\w+)(.*)"
                finishes = {int(m[2]): int(m[1]) for m in re.finditer(pattern, done)}
                for m in re.finditer(pattern, popped):
                    if m[3].startswith(("RV_VCMP", "RV_P", "RV_VSEL", "RV_MOVVP")):
                        events.append(dict(op=m[3], id=int(m[2]), issue=int(m[1]),
                                           done=finishes.get(int(m[2])), operands=m[4]))
                report["events"] = events
                report["vf_cycles"] = int(re.search(r"vf_execute_time: (\d+)", done)[1])
        reports.append(report)
        (args.out / "summary.json").write_text(json.dumps(reports, indent=2) + "\n")
        print(op, run.returncode, report.get("numerical_pass"), flush=True)
    if any(r["returncode"] or not r.get("numerical_pass") for r in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
