"""Archive completed pressure runs and extract independent register evidence."""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import shutil


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("runs", type=Path, nargs="+")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    results = []
    for root in args.runs:
        summaries = json.loads((root / "summary.json").read_text())
        for record in summaries:
            n = record["masks"]
            case = root / f"n{n}"
            dest = args.out / f"n{n}"
            dest.mkdir()
            for path in case.iterdir():
                if path.name.startswith("core0.veccore0.") or path.name in (
                    "predicate_pressure.cce", "compile.log", "link.log", "run.log",
                    "input.bin", "golden.bin", "output.bin", "kernel_aiv.o", "kernel.o"
                ):
                    shutil.copy2(path, dest / path.name)
            shutil.copy2(root / "commands.json", dest / "commands.json")
            instructions = (case / "core0.veccore0.instr_log.dump").read_text()
            counts = Counter(re.findall(r"\) (RV_\w+)", instructions))
            isu = (case / "core0.veccore0.rvec.ISU.dump").read_text()
            mappings = []
            for line in isu.splitlines():
                if "[ISU_RECV]" in line and ("[SHQ]" in line or "[LDQ]" in line):
                    match = re.search(r"DST_PREGS:\[v_idx=(\d+),p_idx=(\d+)\]", line)
                    if match:
                        mappings.append(dict(logical=int(match[1]), physical=int(match[2]), line=line))
            record.update(
                instruction_counts=dict(counts),
                architectural_predicate_indices=sorted(set(map(int, re.findall(r"P[dgnm]\[(\d+)\]", instructions)))),
                physical_predicate_indices=sorted({x["physical"] for x in mappings}),
                predicate_destinations=mappings,
                preg_block_records=len(record["preg_block_lines"]),
            )
            (dest / "validation_and_analysis.json").write_text(json.dumps(record, indent=2) + "\n")
            compact = {key: record[key] for key in (
                "masks", "iterations", "numerical_pass", "min_free_preg", "preg_block_records",
                "architectural_predicate_indices", "physical_predicate_indices", "instruction_counts")}
            results.append(compact)
    results.sort(key=lambda x: x["masks"])
    (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
    for r in results:
        ops = r["instruction_counts"]
        print(r["masks"], ops.get("RV_PSTI", 0), ops.get("RV_PLDI", 0),
              ops.get("RV_SMEM_BAR", 0), r["min_free_preg"], r["preg_block_records"],
              r["numerical_pass"], r["physical_predicate_indices"])


if __name__ == "__main__":
    main()
