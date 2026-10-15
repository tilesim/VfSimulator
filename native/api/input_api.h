#pragma once
#include "native/api/common/validator.h"
#include "native/api/programmatic/builder.h"
#include <filesystem>
namespace vfsim {
class InputAPI {
public:
  static VfInfoBuilder
  newBuilder(std::unordered_map<std::string, int64_t> params = {},
             std::map<std::string, CanonicalScalar> uarch = {},
             std::map<std::string, CanonicalScalar> source = {}) {
    return VfInfoBuilder(std::move(params), std::move(uarch),
                         std::move(source));
  }
  static CanonicalVfInfo loadJson(const std::filesystem::path &path);
  static CanonicalVfInfo
  loadCce(const std::filesystem::path &path, const std::string &kernelName = {},
          const std::unordered_map<std::string, int64_t> &loopParams = {});
  static CanonicalVfInfo fromCanonical(CanonicalVfInfo info);
};
} // namespace vfsim
