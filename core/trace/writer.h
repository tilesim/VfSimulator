#pragma once
#include "core/execution/optimization.h"
#include <cstdint>
#include <filesystem>
#include <string>
namespace vfsim {
class TraceRecorder;
class IDU;
class LoopControl;
class MembarController;
class ParamDB;
struct SimulationResult {
  int64_t cyclesExecuted = 0, vfEndCycle = 0;
  std::string resultsDir, tracePath;
  int64_t linearInstCount = 0;
  CompilerOptimizationStats compilerOptimizationStats;
  // Which preparation ran and, for the streamed scoring path, the exact static
  // event total the cursor produced (audit only; no per-event recording).
  std::string scoringMode = "eager";
  int64_t scoringEventCount = -1;
};
void dumpLogs(const std::filesystem::path &, const TraceRecorder &, const IDU &,
              const LoopControl &, const MembarController &, const ParamDB &,
              int64_t issuePorts);
} // namespace vfsim
