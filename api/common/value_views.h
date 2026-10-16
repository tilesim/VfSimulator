// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#pragma once
#include "api/common/schema.h"
namespace vfsim {
// Non-emitted register definitions may be bit views of a directly emitted value.
// Views share a logical ID, producer and total bit width, never a new register.
std::unordered_map<std::string, std::string> registerViewSources(const CanonicalVfInfo &vf);
} // namespace vfsim
