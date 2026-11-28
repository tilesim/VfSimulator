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
// Prepare-path helper shared with hoistLoopInvariants: fills 'stats' and returns
// the rewritten root context only when LICM is enabled and actually moved at least
// one instruction. std::nullopt means "reuse the input context", so the immutable
// value/storage/parameter/source tables never have to be copied into a temporary
// CanonicalVfInfo.
std::optional<std::vector<CanonicalNode>>
hoistLoopInvariantContext(const CanonicalVfInfo &, CompilerOptimizationStats &);
std::optional<std::vector<CanonicalNode>>
hoistLoopInvariantContext(const CanonicalVfInfo &,
                          const std::vector<CanonicalNode> &,
                          CompilerOptimizationStats &);
} // namespace vfsim
