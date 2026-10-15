#pragma once
#include "api/common/diagnostics.h"
#include "api/common/instruction_catalog.h"
#include <functional>
namespace vfsim {
using InstructionError = std::function<void(
    std::string, std::string, std::string,
    std::optional<CanonicalSourceLocation>)>;

const NativeInstructionSpec *validateInstructionForm(
    const CanonicalVfInfo &, const CanonicalInstruction &,
    const std::string &path, const InstructionError &);
void validateInstructionOperands(
    const CanonicalVfInfo &, const CanonicalInstruction &,
    const NativeInstructionSpec *, const std::string &path,
    const InstructionError &);
} // namespace vfsim
