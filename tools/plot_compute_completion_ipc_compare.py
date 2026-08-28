#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt


def _compute_completion_counts(path: Path) -> dict[int, int]:
    counts: dict[int, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        op = str(event.get("op", "")).upper()
        if op.startswith("VLD") or op.startswith("VST"):
            continue
        cycle = int(event["cy"])
        counts[cycle] = counts.get(cycle, 0) + 1
    return counts


def _trailing_ipc(counts: dict[int, int], end_cycle: int, window: int) -> list[float]:
    raw = [float(counts.get(cycle, 0)) for cycle in range(end_cycle + 1)]
    prefix = [0.0]
    for value in raw:
        prefix.append(prefix[-1] + value)
    result: list[float] = []
    for cycle in range(end_cycle + 1):
        begin = max(0, cycle - window + 1)
        result.append((prefix[cycle + 1] - prefix[begin]) / float(window))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare compute-completion IPC from two VfSim runs"
    )
    parser.add_argument("--first-log", required=True)
    parser.add_argument("--second-log", required=True)
    parser.add_argument("--first-label", required=True)
    parser.add_argument("--second-label", required=True)
    parser.add_argument("--first-cycles", required=True, type=int)
    parser.add_argument("--second-cycles", required=True, type=int)
    parser.add_argument("--third-log")
    parser.add_argument("--third-label")
    parser.add_argument("--third-cycles", type=int)
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--y-max", type=float, default=2.0)
    parser.add_argument("--title", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--csv-out")
    args = parser.parse_args()

    third_enabled = any(
        value is not None
        for value in (args.third_log, args.third_label, args.third_cycles)
    )
    if third_enabled and not all(
        value is not None
        for value in (args.third_log, args.third_label, args.third_cycles)
    ):
        parser.error(
            "--third-log, --third-label, and --third-cycles must be provided together"
        )

    end_cycle = max(
        args.first_cycles,
        args.second_cycles,
        args.third_cycles if args.third_cycles is not None else 0,
    )
    first = _trailing_ipc(
        _compute_completion_counts(Path(args.first_log)), end_cycle, args.window
    )
    second = _trailing_ipc(
        _compute_completion_counts(Path(args.second_log)), end_cycle, args.window
    )
    third = None
    if third_enabled:
        third = _trailing_ipc(
            _compute_completion_counts(Path(args.third_log)), end_cycle, args.window
        )
    cycles = list(range(end_cycle + 1))

    fig, ax = plt.subplots(figsize=(16, 7.5), constrained_layout=True)
    ax.plot(
        cycles,
        first,
        color="#2563eb",
        linewidth=2.0,
        label=f"{args.first_label} ({args.first_cycles} cycles)",
    )
    ax.plot(
        cycles,
        second,
        color="#dc2626",
        linewidth=1.8,
        label=f"{args.second_label} ({args.second_cycles} cycles)",
    )
    if third is not None:
        ax.plot(
            cycles,
            third,
            color="#16a34a",
            linewidth=1.8,
            label=f"{args.third_label} ({args.third_cycles} cycles)",
        )
    ax.set_xlim(0, end_cycle)
    ax.set_ylim(0, args.y_max)
    ax.set_xlabel("Cycle", fontsize=17)
    ax.set_ylabel("Compute completion IPC", fontsize=17)
    ax.set_title(
        f"{args.title}\nTrailing window = {args.window} cycles",
        fontsize=20,
    )
    ax.tick_params(axis="both", labelsize=14)
    ax.grid(True, linestyle="--", alpha=0.35)
    ax.legend(loc="upper right", fontsize=14)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=220)

    if args.csv_out:
        csv_output = Path(args.csv_out)
        csv_output.parent.mkdir(parents=True, exist_ok=True)
        with csv_output.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            headers = ["cycle", "first_ipc", "second_ipc"]
            columns = [cycles, first, second]
            if third is not None:
                headers.append("third_ipc")
                columns.append(third)
            writer.writerow(headers)
            writer.writerows(zip(*columns))


if __name__ == "__main__":
    main()
