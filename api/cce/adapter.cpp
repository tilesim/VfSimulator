// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/cce/adapter.h"
#include "api/cce/value_versioning.h"
#include "api/common/validator.h"
namespace vfsim::cce {
CanonicalVfInfo parseCanonical(const std::filesystem::path &path,
                               const std::string &name,
                               const IntegerParams &params) {
  auto vf =
      versionValues(parseProgram(path, name, params),
                    {{"adapter", std::string("cce")}, {"path", path.string()}});
  requireValidCanonicalVfInfo(vf);
  return vf;
}
} // namespace vfsim::cce
