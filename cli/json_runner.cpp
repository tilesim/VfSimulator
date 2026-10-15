// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/input_api.h"
#include "core/simulator.h"
#include <iostream>
#include <stdexcept>
namespace {
struct Args {
  std::filesystem::path tracePath, ccePath, outDir = "results";
  std::string kernelName;
  int64_t maxCycles = 0;
  bool vloopOnly = false, directIssue = false, help = false;
};
Args parseArgs(int argc, char **argv) {
  Args args;
  for (int i = 1; i < argc; ++i) {
    const std::string key = argv[i];
    if (key == "--help" || key == "-h") {
      args.help = true;
      continue;
    }
    if (key == "--theoretical-limit-vloop-only") {
      args.vloopOnly = true;
      continue;
    }
    if (key ==
        "--theoretical-limit-vloop-only-legacy-forwarding-direct-issue") {
      args.directIssue = true;
      continue;
    }
    if (i + 1 >= argc)
      throw std::runtime_error("Incomplete argument: " + key);
    const std::string value = argv[++i];
    if (key == "--trace")
      args.tracePath = value;
    else if (key == "--cce")
      args.ccePath = value;
    else if (key == "--cce-kernel")
      args.kernelName = value;
    else if (key == "--out-dir" || key == "--out_dir")
      args.outDir = value;
    else if (key == "--max-cycles") {
      size_t used = 0;
      args.maxCycles = std::stoll(value, &used);
      if (used != value.size() || args.maxCycles <= 0)
        throw std::runtime_error("--max-cycles must be positive");
    } else
      throw std::runtime_error("Unknown argument: " + key);
  }
  if (!args.tracePath.empty() && !args.ccePath.empty())
    throw std::runtime_error("Please provide only one input: --trace or --cce");
  return args;
}
} // namespace
int main(int argc, char **argv) {
  try {
    const auto args = parseArgs(argc, argv);
    if (args.help) {
      std::cout
          << "Usage: vfsim_native_json_runner [--trace INPUT.json | --cce "
             "INPUT.cce] [--cce-kernel NAME]\n"
             "  --out_dir DIR (alias: --out-dir), default: results\n"
             "  --max-cycles N\n"
             "  --theoretical-limit-vloop-only\n"
             "  "
             "--theoretical-limit-vloop-only-legacy-forwarding-direct-issue\n";
      return 0;
    }
    const auto root =
        std::filesystem::path(VFSIM_SOURCE_ROOT).lexically_normal();
    auto path = args.ccePath.empty() ? args.tracePath : args.ccePath;
    if (path.empty())
      path = "tests/fixtures/canonical_vf_info/v1_valid_loop.json";
    if (!path.is_absolute())
      path = root / path;
    const auto vf = args.ccePath.empty()
                        ? vfsim::InputAPI::loadJson(path)
                        : vfsim::InputAPI::loadCce(path, args.kernelName);
    vfsim::CoreVfCostModel model(root, args.outDir, args.vloopOnly,
                                 args.directIssue);
    const auto result = model.runVfInfo(vf, args.maxCycles);
    std::cout << "cyclesExecuted=" << result.cyclesExecuted << '\n'
              << "vfEndCycle=" << result.vfEndCycle << '\n'
              << "linearInstCount=" << result.linearInstCount << '\n';
    if (!result.tracePath.empty())
      std::cout << "tracePath=" << result.tracePath << '\n';
    if (!result.resultsDir.empty())
      std::cout << "resultsDir=" << result.resultsDir << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "vfsim_native_json_runner failed: " << error.what() << '\n';
    return 1;
  }
}
