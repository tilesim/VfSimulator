// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/execution/prepare.h"
#include "native/api/common/instruction_catalog.h"
#include "native/core/execution/expansion.h"
namespace vfsim {
namespace {
// Core checks execution support; Canonical semantic validation is performed by
// InputAPI.
int64_t checkExecutionSupport(const std::vector<CanonicalNode> &nodes) {
  int64_t count = 0;
  for (const auto &node : nodes) {
    ++count;
    if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
      if (!inst->dependencies.empty())
        throw std::runtime_error("Canonical explicit dependencies are not "
                                 "supported by native Core: " +
                                 inst->instructionId);
      if (inst->instructionClass == CanonicalInstructionClass::Control)
        throw std::runtime_error(
            "Canonical control instruction must use a Membar node: " +
            inst->instructionId);
      if (!defaultInstructionCatalog().lookup(inst->opcode) &&
          inst->instructionClass != CanonicalInstructionClass::Compute)
        throw std::runtime_error(
            "Unknown native canonical load/store/control opcode: " +
            inst->opcode);
    } else if (const auto *membar =
                   std::get_if<CanonicalMembar>(&node.payload)) {
      if (!membar->dependencies.empty())
        throw std::runtime_error(
            "Canonical explicit Membar dependencies are not supported: " +
            membar->instructionId);
    } else {
      const auto &loop =
          std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
      if (!loop)
        throw std::runtime_error("Canonical loop payload is null");
      count += 1 + checkExecutionSupport(loop->body);
    }
  }
  return count;
}
} // namespace
PreparedProgram prepareProgram(const CanonicalVfInfo &vf, const ParamDB &db,
                               const UarchConfig &cfg) {
  const int64_t linearCount = checkExecutionSupport(vf.context);
  std::unordered_map<std::string, ValueInfo> values;
  for (const auto &[id, value] : vf.values)
    values.emplace(id, ValueInfo{id, value.storage, value.dtype, value.shape});
  auto program = expandCanonicalProgram(vf, db, cfg, std::move(values));
  program.linearInstCount = linearCount;
  return program;
}
} // namespace vfsim
