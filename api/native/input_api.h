#pragma once
#include "api/native/common/validator.h"
#include <filesystem>
namespace vfsim {
class InputAPI {
public:
  static CanonicalVfInfo loadJson(const std::filesystem::path &path);
  static CanonicalVfInfo fromCanonical(CanonicalVfInfo info);
};
} // namespace vfsim
