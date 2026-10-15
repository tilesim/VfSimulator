#pragma once
#include "native/api/cce/ir.h"
namespace vfsim::cce {
CanonicalVfInfo
versionValues(const AdapterProgram &,
              const std::map<std::string, CanonicalScalar> &source = {});
} // namespace vfsim::cce
