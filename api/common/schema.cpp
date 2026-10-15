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
