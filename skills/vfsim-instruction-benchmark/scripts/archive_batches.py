"""Archive CCE batches and the active vector core logs with SHA256 provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

ARTIFACTS = (
    "kernel.cce",
    "probe.cce",
    "host.cpp",
    "host",
    "kernel.o",
    "kernel_aiv.o",
    "cases.json",
    "cases.original.json",
    "cases.corrected.json",
    "commands.json",
    "validation.json",
    "validation.corrected.json",
    "validation.strict.json",
    "failure.json",
    "failure_cases.json",
    "input.bin",
    "golden.bin",
    "output.bin",
    "compile.log",
    "run.log",
    "host_build.log",
    "link.log",
    "core0.veccore0.instr_popped_log.dump",
    "core0.veccore0.instr_log.dump",
    "core0.veccore0.rvec.EXU.dump",
    "core0.veccore0.rvec.ISU.dump",
    "core0.veccore0.rvec.IDU.dump",
    "core0.veccore0.rvec.LSU.dump",
    "core0.veccore0.rvec.GSU.dump",
    "core0.veccore0.rvec.simd.IDU.DISP.dump",
)


def archive(roots, output):
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for root in sorted(roots):
        for work in [root, *sorted(root.glob("batch_*"))]:
            if not work.is_dir():
                continue
            relative = Path(root.name) / work.relative_to(root)
            files = {}
            for name in ARTIFACTS:
                path = work / name
                if not path.is_file():
                    continue
                destination = output / relative / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
                files[name] = dict(
                    bytes=path.stat().st_size,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                )
            if files:
                records.append(
                    dict(run=str(relative), source_directory=str(work), files=files)
                )
    report = dict(
        runs=records,
        files=sum(len(r["files"]) for r in records),
        bytes=sum(f["bytes"] for r in records for f in r["files"].values()),
    )
    (output / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = archive(args.roots, args.out)
    print(
        json.dumps(
            dict(runs=len(report["runs"]), files=report["files"], bytes=report["bytes"])
        )
    )


if __name__ == "__main__":
    main()
