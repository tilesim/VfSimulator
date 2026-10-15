// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/input_api.h"
#include "api/json/serialization.h"
namespace vfsim {
CanonicalVfInfo InputAPI::fromCanonical(CanonicalVfInfo info) {
  requireValidCanonicalVfInfo(info);
  return info;
}
CanonicalVfInfo InputAPI::loadJson(const std::filesystem::path &path) {
  return fromCanonical(decodeCanonicalVfInfoJson(json::parseFile(path)));
}
} // namespace vfsim
