#pragma once
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
};
void dumpLogs(const std::filesystem::path &, const TraceRecorder &, const IDU &,
              const LoopControl &, const MembarController &, const ParamDB &,
              int64_t issuePorts);
} // namespace vfsim
