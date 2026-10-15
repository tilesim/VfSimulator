#pragma once
#include "native/core/execution/prepare.h"
namespace vfsim {
PreparedProgram
expandCanonicalProgram(const CanonicalVfInfo &, const ParamDB &,
                       const UarchConfig &,
                       std::unordered_map<std::string, ValueInfo> values);
}
