#pragma once
#include "api/common/schema.h"

namespace vfsim {
struct LoopInvariantMove {
  std::string instructionId, opcode, fromLoop;
  std::optional<std::string> toLoop;
};
struct CompilerOptimizationStats {
  bool enabled = true;
  int64_t hoistedInstructionCount = 0;
  std::vector<LoopInvariantMove> moves;
};
struct LoopInvariantResult {
  CanonicalVfInfo vfInfo;
  CompilerOptimizationStats stats;
};
LoopInvariantResult hoistLoopInvariants(const CanonicalVfInfo &);
} // namespace vfsim
