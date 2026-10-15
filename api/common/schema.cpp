// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/common/schema.h"
namespace vfsim {
CanonicalNode CanonicalNode::makeInstruction(CanonicalInstruction value) {
  return CanonicalNode{std::move(value)};
}

CanonicalNode CanonicalNode::makeLoop(CanonicalLoop value) {
  return CanonicalNode{std::shared_ptr<const CanonicalLoop>(
      std::make_shared<CanonicalLoop>(std::move(value)))};
}

CanonicalNode CanonicalNode::makeMembar(CanonicalMembar value) {
  return CanonicalNode{std::move(value)};
}

} // namespace vfsim

namespace vfsim {
int64_t emissionSchemaVersion(
    const std::unordered_map<std::string, CanonicalValue> &values,
    const std::vector<CanonicalNode> &nodes) {
  for (const auto &[id, value] : values)
    if (value.storage == CanonicalStorageKind::PredicateRegister)
      return kCurrentSchemaVersion;
  for (const auto &node : nodes) {
    const auto *loop =
        std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
    if (!loop || !*loop)
      continue;
    for (const auto &carried : (*loop)->carriedValues)
      if (!carried.entryValueId)
        return kCurrentSchemaVersion;
    if (emissionSchemaVersion({}, (*loop)->body) == kCurrentSchemaVersion)
      return kCurrentSchemaVersion;
  }
  return kLegacyEmissionSchemaVersion;
}
} // namespace vfsim
