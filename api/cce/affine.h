#pragma once
#include "api/cce/ir.h"
#include <optional>
#include <set>
namespace vfsim::cce {
std::optional<int64_t> evaluateInteger(const std::string &,
                                       const IntegerParams &);
std::optional<uint64_t>
evaluateUnsigned(const std::string &, const IntegerParams &,
                 const std::map<std::string, std::string> &initializers = {});
std::string expandExpression(const std::string &,
                             const std::map<std::string, std::string> &);
CanonicalAffineExpression parseAffine(const std::string &,
                                      const IntegerParams &,
                                      const std::set<std::string> &);
bool isAffine(const std::string &, const IntegerParams &,
              const std::set<std::string> &,
              const std::map<std::string, std::string> &);
} // namespace vfsim::cce
