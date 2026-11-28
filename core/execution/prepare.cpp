// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/prepare.h"
#include "api/common/instruction_catalog.h"
#include "core/execution/expansion.h"
namespace vfsim {
namespace {
// Core checks execution support; Canonical semantic validation is performed by
// InputAPI.
int64_t checkExecutionSupport(const std::vector<CanonicalNode> &nodes) {
  int64_t count = 0;
  for (const auto &node : nodes) {
    ++count;
    if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
      if (inst->instructionClass == CanonicalInstructionClass::Control)
        throw std::runtime_error(
            "Canonical control instruction must use a Membar node: " +
            inst->instructionId);
      if (!defaultInstructionCatalog().lookup(inst->opcode) &&
          inst->instructionClass != CanonicalInstructionClass::Compute)
        throw std::runtime_error(
            "Unknown native canonical load/store/control opcode: " +
            inst->opcode);
    } else if (const auto *pointer =
                   std::get_if<std::shared_ptr<const CanonicalLoop>>(
                       &node.payload)) {
      const auto &loop = *pointer;
      if (!loop)
        throw std::runtime_error("Canonical loop payload is null");
      count += 1 + checkExecutionSupport(loop->body);
    }
  }
  return count;
}
} // namespace
PreparedProgram prepareProgram(const CanonicalVfInfo &input, const ParamDB &db,
                               const UarchConfig &cfg, bool retainDiagnostics) {
  CompilerOptimizationStats stats;
  const auto hoisted = hoistLoopInvariantContext(input, stats);
  // LICM rewrites only the root context. Borrow the immutable value, storage,
  // parameter and source tables from the caller instead of copying the whole
  // CanonicalVfInfo into a temporary.
  const std::vector<CanonicalNode> &context = hoisted ? *hoisted : input.context;
  const int64_t linearCount = checkExecutionSupport(context);
  std::unordered_map<std::string, ValueInfo> values;
  if (retainDiagnostics)
    for (const auto &[id, value] : input.values)
      values.emplace(id,
                     ValueInfo{id, value.storage, value.dtype, value.shape});
  auto program = expandCanonicalProgram(input, context, db, cfg,
                                        std::move(values), retainDiagnostics);
  program.linearInstCount = linearCount;
  program.compilerOptimizationStats = stats;
  return program;
}

PreparedStreamingLayout prepareProgramForStreamingLayout(
    const CanonicalVfInfo &input,
    std::optional<std::vector<CanonicalNode>> postLicmContext, const ParamDB &db,
    const UarchConfig &cfg, CompilerOptimizationStats stats) {
  // Shape gate and reported linear instruction count come from the same static
  // walk the eager preparation uses; then one layout build that the cursor reuses
  // instead of interning the static graph again.
  const std::vector<CanonicalNode> &context =
      postLicmContext ? *postLicmContext : input.context;
  const int64_t linearCount = checkExecutionSupport(context);
  auto prepared = expandCanonicalStreamingLayout(
      input, std::move(postLicmContext), db, cfg);
  prepared.program.linearInstCount = linearCount;
  prepared.program.compilerOptimizationStats = std::move(stats);
  return prepared;
}
} // namespace vfsim
