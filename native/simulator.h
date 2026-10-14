#pragma once
#include "api/native/common/schema.h"
#include "native/trace/writer.h"
namespace vfsim {
// Accepts CanonicalVfInfo validated by InputAPI.
class CoreVfCostModel {
public:
  explicit CoreVfCostModel(std::filesystem::path baseDir = {},
                           std::filesystem::path outDir = {},
                           bool vloopOnly = false, bool directIssue = false)
      : baseDir_(std::move(baseDir)), outDir_(std::move(outDir)),
        vloopOnly_(vloopOnly), directIssue_(directIssue) {}
  SimulationResult runVfInfo(const CanonicalVfInfo &vf,
                             int64_t maxCycles = 0) const;
  int64_t predictVfCycles(const CanonicalVfInfo &vf,
                          int64_t maxCycles = 0) const {
    return runVfInfo(vf, maxCycles).vfEndCycle;
  }

private:
  std::filesystem::path baseDir_, outDir_;
  bool vloopOnly_, directIssue_;
};
} // namespace vfsim
