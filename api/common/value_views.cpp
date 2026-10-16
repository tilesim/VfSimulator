// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/common/value_views.h"
#include <algorithm>
#include <limits>
namespace vfsim {
namespace {
int64_t bitSize(const CanonicalValue &value) {
  static const std::unordered_map<std::string, int64_t> widths = {
      {"fp16", 16}, {"bf16", 16}, {"fp32", 32}, {"fp64", 64}, {"b8", 8},   {"b16", 16},
      {"b32", 32},  {"b64", 64},  {"s8", 8},    {"s16", 16},  {"s32", 32}, {"s64", 64}};
  auto width = widths.find(value.dtype);
  if (width == widths.end()) {
    return 0;
  }
  int64_t bits = width->second;
  for (int64_t extent : value.shape) {
    if (extent <= 0 || extent > std::numeric_limits<int64_t>::max() / bits) {
      return 0;
    }
    bits *= extent;
  }
  return bits;
}

using Definitions = std::unordered_map<std::string, std::vector<std::string>>;
void collectDefinitions(const std::vector<CanonicalNode> &nodes, Definitions &definitions) {
  for (const auto &node : nodes) {
    if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
      for (const auto &output : inst->outputs) {
        definitions[inst->instructionId].push_back(output.valueId);
      }
    } else if (const auto *loop =
                   std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload)) {
      if (!*loop) {
        continue;
      }
      for (const auto &carried : (*loop)->carriedValues) {
        definitions[(*loop)->loopId].push_back(carried.exitValueId);
      }
      collectDefinitions((*loop)->body, definitions);
    }
  }
}
} // namespace

std::unordered_map<std::string, std::string> registerViewSources(const CanonicalVfInfo &vf) {
  Definitions definitions;
  collectDefinitions(vf.context, definitions);
  std::unordered_map<std::string, std::string> views;
  for (const auto &[id, view] : vf.values) {
    if (view.storage != CanonicalStorageKind::Register) {
      continue;
    }
    if (!view.producerNodeId) {
      auto source = vf.values.find(view.logicalId);
      if (id != view.logicalId && source != vf.values.end() &&
          source->second.storage == CanonicalStorageKind::Register &&
          !source->second.producerNodeId && bitSize(view) > 0 &&
          bitSize(source->second) == bitSize(view)) {
        views.emplace(id, source->first);
      }
      continue;
    }
    auto outputs = definitions.find(*view.producerNodeId);
    if (outputs == definitions.end() ||
        std::find(outputs->second.begin(), outputs->second.end(), id) != outputs->second.end()) {
      continue;
    }
    const int64_t bits = bitSize(view);
    for (const auto &output : outputs->second) {
      auto source = vf.values.find(output);
      if (source != vf.values.end() && source->second.storage == CanonicalStorageKind::Register &&
          source->second.producerNodeId == view.producerNodeId &&
          source->second.logicalId == view.logicalId && !view.storageObjectId &&
          !source->second.storageObjectId && bits > 0 && bitSize(source->second) == bits) {
        views.emplace(id, output);
        break;
      }
    }
  }
  return views;
}
} // namespace vfsim
