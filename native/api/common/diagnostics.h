#pragma once
#include "native/api/common/schema.h"
#include <stdexcept>
namespace vfsim {
struct CanonicalValidationDiagnostic {
  std::string code;
  std::string severity = "error";
  std::string message;
  std::string path;
  std::optional<CanonicalSourceLocation> sourceLocation;
  std::map<std::string, CanonicalScalar> context;
};

struct CanonicalValidationResult {
  std::vector<CanonicalValidationDiagnostic> diagnostics;
  bool ok() const;
};

class VfInfoValidationError : public std::runtime_error {
public:
  explicit VfInfoValidationError(CanonicalValidationResult result);
  const CanonicalValidationResult &result() const noexcept { return result_; }

private:
  CanonicalValidationResult result_;
};

} // namespace vfsim
