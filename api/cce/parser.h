#pragma once
#include "api/cce/ir.h"
#include "api/cce/source.h"
namespace vfsim::cce {
AdapterProgram parseProgram(const std::filesystem::path &,
                            const std::string &kernelName = {},
                            const IntegerParams &loopParams = {});
} // namespace vfsim::cce
