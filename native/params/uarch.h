#pragma once
#include "api/native/common/schema.h"
#include "api/native/json/value.h"
#include "native/params/types.h"
#include <set>
namespace vfsim {
std::set<std::string> supportedUarchFields();
UarchConfig parseUarch(const json::Value::Object &values,
                       UarchConfig base = {});
UarchConfig
resolveUarch(UarchConfig base,
             const std::map<std::string, CanonicalScalar> &overrides = {},
             bool vloopOnly = false, bool directIssue = false);

} // namespace vfsim
