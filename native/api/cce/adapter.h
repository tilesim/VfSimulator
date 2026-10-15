#pragma once
#include "native/api/cce/parser.h"
namespace vfsim::cce {
CanonicalVfInfo parseCanonical(const std::filesystem::path &,
                               const std::string &kernelName = {},
                               const IntegerParams &loopParams = {});
} // namespace vfsim::cce
