#pragma once
#include "native/api/common/schema.h"
#include "native/core/trace/writer.h"
#include <memory>
namespace vfsim {
struct ParamBundle;
// Accepts CanonicalVfInfo validated by InputAPI.
class CoreVfCostModel {
public:
  explicit CoreVfCostModel(std::filesystem::path baseDir = {},
                           std::filesystem::path outDir = "results/cost_model",
                           bool vloopOnly = false, bool directIssue = false);
  SimulationResult runVfInfo(const CanonicalVfInfo &vf,
                             int64_t maxCycles = 0) const;
  int64_t predictVfCycles(const CanonicalVfInfo &vf,
                          int64_t maxCycles = 0) const {
    return runVfInfo(vf, maxCycles).vfEndCycle;
  }

private:
  std::filesystem::path baseDir_, outDir_;
  bool vloopOnly_, directIssue_;
  std::shared_ptr<const ParamBundle> parameters_;
};
} // namespace vfsim
