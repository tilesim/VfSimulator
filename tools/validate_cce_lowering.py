"""Compare Python/C++ Canonical graphs and simulation for every CANN recipe."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.input_api import InputAPI
from api.json.serialization import canonical_vf_info_to_dict
from core.simulator import CoreVfCostModel


HELPER = r"""
#include "native/api/input_api.h"
#include "native/api/json/serialization.h"
#include "native/core/simulator.h"
#include <iostream>
int main(int argc, char **argv) {
  vfsim::CoreVfCostModel model(argv[1], std::filesystem::path{});
  std::string path;
  while (std::getline(std::cin, path)) {
    try {
      auto vf = vfsim::InputAPI::loadCce(path);
      auto result = model.runVfInfo(vf);
      vfsim::json::Value::Object item{
          {"canonical", vfsim::encodeCanonicalVfInfoJson(vf)},
          {"cycles", result.cyclesExecuted},
          {"vf_end", result.vfEndCycle},
          {"instructions", result.linearInstCount}};
      std::cout << vfsim::json::stringify(item) << '\n';
    } catch (const std::exception &error) {
      std::cout << vfsim::json::stringify(
          vfsim::json::Value::Object{{"error",error.what()}}) << '\n';
    }
  }
}
"""


def source(recipe):
    declarations, parameters, arguments = [], [], []
    for arg in recipe["arguments"]:
        position = arg["position"]
        name = f"a{position}"
        typ = arg["type"].replace("&", "").strip()
        if arg["config"]:
            arguments.append(recipe["options"][str(position)])
        else:
            arguments.append(name)
            if typ.startswith("vector_"):
                initializer = "=pset_b8(PAT_ALL)" if typ == "vector_bool" else ""
                declarations.append(f"{typ} {name}{initializer};")
            else:
                parameters.append(f"{typ} {name}")
    call = recipe["api"] + "(" + ",".join(arguments) + ");"
    return (
        "void vf("
        + ",".join(parameters)
        + "){__VEC_SCOPE__{"
        + "".join(declarations)
        + call
        + "}}\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--compiler", default="c++")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    fixtures = args.out_dir / "cce"
    fixtures.mkdir(exist_ok=True)
    recipes = json.loads((ROOT / "configs/cce_lowering.json").read_text())["recipes"]
    paths = []
    for recipe in recipes:
        path = fixtures / (recipe["name"] + ".cce")
        path.write_text(source(recipe))
        paths.append(path.resolve())
    cpp = args.out_dir / "canonical_check.cpp"
    cpp.write_text(HELPER)
    executable = (args.out_dir / "canonical_check").resolve()
    command = [
        args.compiler,
        "-std=c++17",
        "-O2",
        "-I",
        str(ROOT),
        str(cpp),
        str(args.build_dir.resolve() / "libvfsim_native_core.a"),
        "-o",
        str(executable),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    process = subprocess.run(
        [str(executable), str(ROOT)],
        input="\n".join(map(str, paths)) + "\n",
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    native = [json.loads(line) for line in process.stdout.splitlines()]
    if len(native) != len(recipes):
        raise RuntimeError("Native result count differs from the recipe inventory")
    model = CoreVfCostModel(base_dir=ROOT, out_dir=None)
    records = []
    for recipe, path, actual in zip(recipes, paths, native):
        vf = InputAPI.load_cce(path)
        graph = canonical_vf_info_to_dict(vf)
        result = model.run_vf_info(vf)
        expected = dict(
            canonical=graph,
            cycles=result["cycles_executed"],
            vf_end=result["vf_end_cycle"],
            instructions=result["linear_inst_count"],
        )
        passed = expected == actual
        record = dict(
            recipe=recipe["name"],
            api=recipe["api"],
            passed=passed,
            steps=len(recipe["steps"]),
            vf_end=result["vf_end_cycle"],
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            canonical_sha256=hashlib.sha256(
                json.dumps(graph, sort_keys=True).encode()
            ).hexdigest(),
        )
        if not passed:
            (args.out_dir / (recipe["name"] + ".difference.json")).write_text(
                json.dumps(dict(expected=expected, actual=actual), indent=2) + "\n"
            )
        records.append(record)
    report = dict(
        recipes=len(records),
        passed=sum(row["passed"] for row in records),
        command=command,
        records=records,
    )
    (args.out_dir / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(dict(recipes=report["recipes"], passed=report["passed"])))
    if report["passed"] != report["recipes"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
