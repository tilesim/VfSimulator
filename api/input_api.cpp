// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/input_api.h"
#include "api/cce/adapter.h"
#include "api/json/serialization.h"
namespace vfsim {
CanonicalVfInfo InputAPI::fromCanonical(CanonicalVfInfo info) {
  requireValidCanonicalVfInfo(info);
  return info;
}
CanonicalVfInfo InputAPI::loadJson(const std::filesystem::path &path) {
  return fromCanonical(decodeCanonicalVfInfoJson(json::parseFile(path)));
}
CanonicalVfInfo
InputAPI::loadCce(const std::filesystem::path &path,
                  const std::string &kernelName,
                  const std::unordered_map<std::string, int64_t> &loopParams) {
  return cce::parseCanonical(path, kernelName, loopParams);
}
} // namespace vfsim
