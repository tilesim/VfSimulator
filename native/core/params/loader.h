#pragma once
#include "native/core/params/types.h"
#include <filesystem>
namespace vfsim {
ParamBundle loadParameters(const std::filesystem::path &baseDir);
}
