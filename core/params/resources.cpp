// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/params/resources.h"
#include "api/common/instruction_catalog.h"
#include <algorithm>
#include <stdexcept>

namespace vfsim {
std::vector<std::string> instructionBanks(const std::string &op) {
  std::vector<std::string> banks;
  const auto *spec = defaultInstructionCatalog().lookup(op);
  if (!spec)
    return banks;
  for (const auto &operand : spec->operands) {
    if (operand.direction != CatalogOperandDirection::Output &&
        operand.direction != CatalogOperandDirection::ReadWrite)
      continue;
    const std::string bank = operand.kind == CatalogArgumentKind::Register ? "vector"
        : operand.kind == CatalogArgumentKind::Predicate ? "predicate" : "";
    if (!bank.empty() && std::find(banks.begin(), banks.end(), bank) == banks.end())
      banks.push_back(bank);
  }
  return banks;
}

std::optional<int64_t> computeStoreForwarding(const ParamBundle &rules, const InstructionProfile &producer,
                                            const InstructionProfile &consumer) {
  if (producer.opClass == "COMPUTE" && consumer.opClass == "STORE")
    return std::max<int64_t>(0, producer.latency - rules.computeStoreForwardingOffset);
  return std::nullopt;
}

std::optional<int64_t> forwardingInterval(const ParamBundle &rules,
                                        const InstructionProfile &producer,
                                        const InstructionProfile &consumer) {
  if (producer.executionResource.empty() || consumer.executionResource.empty())
    return std::nullopt;
  auto offset = rules.resourceForwardingDefaultOffset;
  if (const auto row = rules.resourceForwardingOffsets.find(producer.executionResource);
      row != rules.resourceForwardingOffsets.end())
    if (const auto value = row->second.find(consumer.executionResource);
        value != row->second.end())
      offset = value->second;
  return std::max(rules.resourceForwardingMinimum, producer.latency - offset);
}

std::optional<int64_t> issueInterval(const ParamBundle &rules, const InstructionProfile &previous,
                                   const InstructionProfile &current) {
  if (previous.executionResource.empty() || current.executionResource.empty())
    return std::nullopt;
  const bool sharedResource = previous.executionResource == current.executionResource;
  if (sharedResource && !previous.selfIi)
    return std::nullopt;
  auto gap = sharedResource ? *previous.selfIi : rules.crossResourceIssueInterval;
  const bool sharedBank = std::any_of(previous.writebackBanks.begin(), previous.writebackBanks.end(),
      [&](const auto &bank) {
        return std::find(current.writebackBanks.begin(), current.writebackBanks.end(), bank) != current.writebackBanks.end();
      });
  if (sharedBank && previous.latency == gap + current.latency)
    gap += rules.writebackCollisionPenalty;
  return gap;
}

void validateResources(const ParamBundle &parameters) {
  for (const auto &[op, forms] : parameters.isa)
    for (const auto &[form, profile] : forms)
      if (!profile.executionResource.empty() &&
          (profile.opClass != "COMPUTE" ||
           std::find(parameters.executionResources.begin(), parameters.executionResources.end(),
                     profile.executionResource) == parameters.executionResources.end()))
        throw std::runtime_error("Unknown or non-compute execution resource: " + op + "." + form);
  auto exact = [&](const std::string &key) -> const InstructionProfile * {
    const auto dot = key.find('.');
    if (dot == std::string::npos)
      return nullptr;
    const auto instruction = parameters.isa.find(key.substr(0, dot));
    if (instruction == parameters.isa.end())
      return nullptr;
    const auto value = instruction->second.find(key.substr(dot + 1));
    return value == instruction->second.end() ? nullptr : &value->second;
  };
  for (bool initiation : {false, true})
    for (const auto &[a, row] : initiation ? parameters.iiOverrides : parameters.forwardingOverrides) {
      const auto *producer = exact(a);
      if (!producer)
        throw std::runtime_error("Unknown timing override producer: " + a);
      for (const auto &[b, value] : row) {
        const auto *consumer = exact(b);
        if (!consumer || (initiation && (a == b || producer->opClass != "COMPUTE" ||
                                                   consumer->opClass != "COMPUTE")))
          throw std::runtime_error("Invalid timing override: " + a + " -> " + b);
      }
    }
}
} // namespace vfsim
