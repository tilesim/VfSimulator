#pragma once
#include "core/execution/prepare.h"
namespace vfsim {
PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &, const ParamDB &, const UarchConfig &,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics);
// LICM-aware entry point: expands a borrowed root context while the immutable
// value/storage/parameter tables stay owned by the given CanonicalVfInfo.
PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &, const std::vector<CanonicalNode> &context,
    const ParamDB &, const UarchConfig &,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics);
}
