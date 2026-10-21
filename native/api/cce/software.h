#pragma once
#include "native/api/cce/ir.h"
#include "native/api/json/value.h"
#include <functional>

namespace vfsim::cce {
const json::Value *
selectSoftwareRecipe(const std::string &api,
                     const std::vector<std::string> &arguments,
                     const std::vector<std::string> &dtypes);
std::vector<AdapterNode> emitSoftwareRecipe(
    const json::Value &recipe,
    const std::map<int, std::vector<AdapterValue>> &arguments,
    const std::function<AdapterValue(const std::string &)> &temporary,
    const std::function<AdapterValue(const std::string &)> &scalar,
    const std::optional<CanonicalSourceLocation> &location);
} // namespace vfsim::cce
