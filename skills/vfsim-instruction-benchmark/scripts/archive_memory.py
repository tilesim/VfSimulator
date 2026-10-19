"""Preserve reproducible memory probes without archiving binaries or idle-core logs."""
import argparse
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    reports = []
    for path in args.paths:
        target = args.out / path.name
        target.mkdir()
        for name in ["kernel.cce", "host.cpp", "commands.json", "validation.json", "input.bin",
                     "golden.bin", "output.bin", "compile.log", "run.log", "host_build.log", "link.log"]:
            if (path / name).exists():
                shutil.copy2(path / name, target / name)
        for suffix in ["instr_log", "instr_popped_log", "rvec.IDU", "rvec.ISU", "rvec.EXU", "rvec.LSU"]:
            name = f"core0.veccore0.{suffix}.dump"
            if (path / name).exists():
                shutil.copy2(path / name, target / name)
        if (target / "validation.json").exists():
            report = json.loads((target / "validation.json").read_text())
            report["artifact_dir"] = target.name
        else:
            report = dict(artifact_dir=target.name, passed=False,
                          status="compile/runtime failure; inspect logs")
        reports.append(report)
    (args.out / "summary.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(f"Archived {len(reports)} probes: {args.out}")


if __name__ == "__main__":
    main()
