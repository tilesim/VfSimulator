// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under the terms and conditions of
// CANN Open Software License Agreement Version 2.0 (the "License").
// Please refer to the License for details. You may not use this file except in compliance with the License.
// THIS SOFTWARE IS PROVIDED ON AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE.
// See LICENSE in the root of the software repository for the full text of the License.

#include "native/ProgramAnalysis.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>
#include <utility>

namespace vfsim {
namespace {

bool isDigits(const std::string &text) {
  if (text.empty())
    return false;
  return std::all_of(text.begin(), text.end(), [](unsigned char c) {
    return std::isdigit(c) != 0;
  });
}

} // namespace

ProgramNode ProgramNode::makeInst(ProgramInstNode value) {
  ProgramNode node;
  node.kind = Kind::Inst;
  node.inst = std::move(value);
  node.loop.reset();
  return node;
}

ProgramNode ProgramNode::makeLoop(ProgramLoopNode value) {
  ProgramNode node;
  node.kind = Kind::Loop;
  node.loop = std::make_shared<ProgramLoopNode>(std::move(value));
  return node;
}

ProgramNode ProgramNode::makeMembar(ProgramMembarNode value) {
  ProgramNode node;
  node.kind = Kind::Membar;
  node.membar = std::move(value);
  node.loop.reset();
  return node;
}

ProgramAnalysis::ProgramAnalysis(ParamMap params,
                                 std::unordered_map<std::string, ValueInfo> values)
    : params_(std::move(params)), values_(std::move(values)) {}

bool ProgramAnalysis::isVregName(const std::string &name) const {
  auto it = values_.find(name);
  if (it != values_.end())
    return it->second.storage == ValueStorageKind::Register;
  const std::string suffix = "_lane";
  const auto pos = name.rfind(suffix);
  if (pos != std::string::npos && pos + suffix.size() < name.size()) {
    bool digits = true;
    for (size_t i = pos + suffix.size(); i < name.size(); ++i) {
      if (!std::isdigit(static_cast<unsigned char>(name[i]))) {
        digits = false;
        break;
      }
    }
    if (digits) {
      it = values_.find(name.substr(0, pos));
      if (it != values_.end())
        return it->second.storage == ValueStorageKind::Register;
    }
  }
  return inferValueStorage(name) == ValueStorageKind::Register;
}

int64_t ProgramAnalysis::resolveBound(const std::string &bound) const {
  if (isDigits(bound))
    return std::stoll(bound);
  auto it = params_.find(bound);
  if (it != params_.end())
    return it->second;
  throw std::invalid_argument("Unsupported loop bound: " + bound);
}

int64_t ProgramAnalysis::resolveUnrollValue(const std::string &unroll) const {
  if (isDigits(unroll))
    return std::max<int64_t>(1, std::stoll(unroll));
  auto it = params_.find(unroll);
  if (it != params_.end())
    return std::max<int64_t>(1, it->second);
  return 1;
}


std::vector<int64_t> ProgramAnalysis::inferNestedBoundsFromLoop(const ProgramLoopNode &loop) const {
  std::vector<int64_t> bounds;
  const ProgramLoopNode *cur = &loop;

  while (cur != nullptr && bounds.size() < 3) {
    bounds.push_back(resolveBound(cur->iters));
    const ProgramLoopNode *nextLoop = nullptr;
    for (const auto &node : cur->body) {
      if (node.kind == ProgramNode::Kind::Loop && node.loop) {
        nextLoop = node.loop.get();
        break;
      }
    }
    cur = nextLoop;
  }

  return bounds;
}

std::unordered_map<int, std::vector<int64_t>>
ProgramAnalysis::inferTopBlockLoopBounds(const std::vector<ProgramNode> &program) const {
  std::unordered_map<int, std::vector<int64_t>> result;
  int tbid = 0;

  for (const auto &node : program) {
    if (node.kind == ProgramNode::Kind::Loop && node.loop) {
      result.emplace(tbid++, inferNestedBoundsFromLoop(*node.loop));
    }
  }

  if (result.empty())
    result.emplace(0, std::vector<int64_t>{});

  return result;
}

} // namespace vfsim
