#pragma once
#include "api/common/diagnostics.h"
namespace vfsim {
CanonicalValidationResult
validateCanonicalVfInfo(const CanonicalVfInfo &vfInfo);
void requireValidCanonicalVfInfo(const CanonicalVfInfo &vfInfo);
} // namespace vfsim
