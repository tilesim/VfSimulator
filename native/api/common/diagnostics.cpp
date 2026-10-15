// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/common/diagnostics.h"
#include <algorithm>
#include <sstream>
namespace vfsim {
bool CanonicalValidationResult::ok() const {
  return std::none_of(diagnostics.begin(), diagnostics.end(),
                      [](const CanonicalValidationDiagnostic &diagnostic) {
                        return diagnostic.severity == "error";
                      });
}

namespace {
std::string messageFor(const CanonicalValidationResult &result) {
  std::ostringstream text;
  text << "CanonicalVfInfo validation failed";
  for (const auto &d : result.diagnostics)
    if (d.severity == "error")
      text << "; " << d.code << " at " << d.path << ": " << d.message;
  return text.str();
}
} // namespace
VfInfoValidationError::VfInfoValidationError(CanonicalValidationResult result)
    : std::runtime_error(messageFor(result)), result_(std::move(result)) {}
} // namespace vfsim
