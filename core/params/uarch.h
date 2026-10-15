#pragma once
#include "api/common/schema.h"
#include "api/json/value.h"
#include "core/params/types.h"
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
