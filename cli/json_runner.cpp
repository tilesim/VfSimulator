// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/input_api.h"
#include "api/json/value.h"
#include "core/execution/scoring_stream.h"
#include "core/simulator.h"
#include <cstdlib>
#include <iostream>
#include <stdexcept>
namespace {
struct Args {
  std::filesystem::path tracePath, ccePath, outDir = "results";
  bool outDirExplicit = false;
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
    else if (key == "--out-dir" || key == "--out_dir") {
      args.outDir = value;
      // An explicit results directory is a trace/export request: it must win
      // over VFSIM_SCORING_STREAM instead of being silently dropped.
      args.outDirExplicit = true;
    }
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
      path = "VFtest/canonical/fixtures/v1_valid_loop.json";
    if (!path.is_absolute())
      path = root / path;
    const auto vf = args.ccePath.empty()
                        ? vfsim::InputAPI::loadJson(path)
                        : vfsim::InputAPI::loadCce(path, args.kernelName);
    // Opt-in streamed scoring path for benchmarks: explicit environment switch,
    // never enabled implicitly (default scoring stays the eager preparation).
    // Opt-in streamed scoring path. An explicit --out_dir is a trace/export
    // request and wins over the switch, so the results directory is never
    // silently dropped; only a headless (default) invocation may route the
    // normal scoring path through the streamed preparation.
    bool scoringStream = false;
    if (const char *scoringSwitch = std::getenv("VFSIM_SCORING_STREAM");
        scoringSwitch != nullptr && *scoringSwitch != '\0' &&
        std::string(scoringSwitch) != "0") {
      if (args.outDirExplicit) {
        std::cout << "scoringStream=refused:explicit-out-dir\n";
      } else {
        vfsim::setScoringStreamEnabled(true);
        scoringStream = true;
      }
    }
    vfsim::CoreVfCostModel model(root, scoringStream ? "" : args.outDir,
                                 args.vloopOnly, args.directIssue);
    const auto result = model.runVfInfo(vf, args.maxCycles);
    std::cout << "cyclesExecuted=" << result.cyclesExecuted << '\n'
              << "vfEndCycle=" << result.vfEndCycle << '\n'
              << "linearInstCount=" << result.linearInstCount << '\n'
              << "scoringMode=" << result.scoringMode << '\n'
              << "scoringEventCount=" << result.scoringEventCount << '\n';
    vfsim::json::Value::Array moves;
    for (const auto &move : result.compilerOptimizationStats.moves)
      moves.emplace_back(vfsim::json::Value::Object{{"instruction_id", move.instructionId},
        {"opcode", move.opcode}, {"from_loop", move.fromLoop},
        {"to_loop", move.toLoop ? vfsim::json::Value(*move.toLoop) : vfsim::json::Value(nullptr)}});
    std::cout << "compilerOptimizationStats=" << vfsim::json::stringify(vfsim::json::Value::Object{
      {"enabled", result.compilerOptimizationStats.enabled},
      {"hoisted_instruction_count", result.compilerOptimizationStats.hoistedInstructionCount},
      {"moves", std::move(moves)}}) << '\n';
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
