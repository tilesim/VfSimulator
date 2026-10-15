#pragma once
#include "native/api/cce/ir.h"
#include "native/api/cce/source.h"
namespace vfsim::cce {
AdapterProgram parseProgram(const std::filesystem::path &,
                            const std::string &kernelName = {},
                            const IntegerParams &loopParams = {});
} // namespace vfsim::cce
