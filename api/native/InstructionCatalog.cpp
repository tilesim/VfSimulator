// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/InstructionCatalog.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace vfsim {
namespace {

struct GeneratedInstruction {
  const char *opcode;
  const char *instructionClass;
  const char *signature;
  const char *formRule;
  const char *fixedForm;
  bool virtualOpcode;
  const char *alignStateOperation;
  int alignStateArgumentIndex;
  int64_t ubTransferBytes;
  const char *forwardingOpcode;
};
struct GeneratedOperand {
  const char *opcode;
  const char *name;
  int argumentIndex;
  const char *direction;
  const char *role;
  const char *kind;
  bool optional;
  bool allowIntegerExpression;
};
struct GeneratedMemoryMode {
  const char *opcode;
  const char *mode;
  const char *key;
  int span;
};
struct GeneratedMemoryForm {
  const char *opcode;
  const char *form;
  int64_t implicitPostUpdateBytes;
  int64_t span;
};
struct GeneratedPostUpdateDelta {
  const char *opcode;
  int argumentIndex;
  const char *encoding;
  int bitOffset;
  int bitWidth;
  int unitBytes;
};
struct GeneratedAllowedValue {
  const char *opcode;
  int argumentIndex;
  const char *value;
};
struct GeneratedCallVariant {
  const char *opcode;
  int variantIndex;
  int argumentCount;
};
struct GeneratedFormAllowedValue {
  const char *opcode;
  int argumentIndex;
  const char *form;
  const char *value;
};
struct GeneratedCallVariantValue {
  const char *opcode;
  int variantIndex;
  int argumentIndex;
  const char *value;
};
struct GeneratedAlias { const char *alias; const char *opcode; };
struct GeneratedForm { const char *opcode; const char *form; };
struct GeneratedSpecialization {
  const char *opcode;
  const char *form;
  const char *target;
};

#include "api/native/generated/InstructionCatalogData.inc"

std::string lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

std::string upper(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return value;
}

CatalogInstructionClass parseClass(const std::string &value) {
  if (value == "load")
    return CatalogInstructionClass::Load;
  if (value == "store")
    return CatalogInstructionClass::Store;
  if (value == "compute")
    return CatalogInstructionClass::Compute;
  if (value == "control")
    return CatalogInstructionClass::Control;
  throw std::runtime_error("Invalid generated instruction class: " + value);
}

CatalogOperandDirection parseDirection(const std::string &value) {
  if (value == "input")
    return CatalogOperandDirection::Input;
  if (value == "output")
    return CatalogOperandDirection::Output;
  if (value == "read_write")
    return CatalogOperandDirection::ReadWrite;
  if (value == "ignore")
    return CatalogOperandDirection::Ignore;
  throw std::runtime_error("Invalid generated operand direction: " + value);
}

CatalogArgumentKind parseArgumentKind(const std::string &value) {
  if (value == "register")
    return CatalogArgumentKind::Register;
  if (value == "ub")
    return CatalogArgumentKind::UB;
  if (value == "scalar")
    return CatalogArgumentKind::Scalar;
  if (value == "predicate")
    return CatalogArgumentKind::Predicate;
  if (value == "config")
    return CatalogArgumentKind::Config;
  if (value == "register_or_scalar")
    return CatalogArgumentKind::RegisterOrScalar;
  if (value == "align_state")
    return CatalogArgumentKind::AlignState;
  throw std::runtime_error("Invalid generated argument kind: " + value);
}

} // namespace

InstructionCatalog::InstructionCatalog() {
  for (const auto &entry : kGeneratedInstructions) {
    NativeInstructionSpec spec;
    spec.opcode = entry.opcode;
    spec.instructionClass = parseClass(entry.instructionClass);
    spec.signature = entry.signature;
    spec.formRule = entry.formRule;
    spec.fixedForm = entry.fixedForm;
    spec.virtualOpcode = entry.virtualOpcode;
    spec.alignStateOperation = entry.alignStateOperation;
    spec.alignStateArgumentIndex = entry.alignStateArgumentIndex;
    spec.ubTransferBytes = entry.ubTransferBytes;
    spec.forwardingOpcode = entry.forwardingOpcode;
    if (!specs_.emplace(spec.opcode, std::move(spec)).second)
      throw std::runtime_error("Duplicate generated opcode: " +
                               std::string(entry.opcode));
    aliases_.emplace(lower(entry.opcode), entry.opcode);
  }
  for (const auto &entry : kGeneratedOperands) {
    NativeOperandSpec operand;
    operand.name = entry.name;
    operand.argumentIndex = entry.argumentIndex;
    operand.direction = parseDirection(entry.direction);
    operand.role = entry.role;
    operand.kind = parseArgumentKind(entry.kind);
    operand.optional = entry.optional;
    operand.allowIntegerExpression = entry.allowIntegerExpression;
    specs_.at(entry.opcode).operands.push_back(std::move(operand));
  }
  for (const auto &entry : kGeneratedPostUpdateDeltas) {
    auto &operands = specs_.at(entry.opcode).operands;
    auto operand = std::find_if(
        operands.begin(), operands.end(), [&](const NativeOperandSpec &candidate) {
          return candidate.argumentIndex == entry.argumentIndex;
        });
    if (operand == operands.end())
      throw std::runtime_error("Generated POST_UPDATE delta references missing operand: " +
                               std::string(entry.opcode));
    operand->postUpdateDeltaEncoding = entry.encoding;
    operand->postUpdateDeltaBitOffset = entry.bitOffset;
    operand->postUpdateDeltaBitWidth = entry.bitWidth;
    operand->postUpdateDeltaUnitBytes = entry.unitBytes;
  }
  for (const auto &entry : kGeneratedAllowedValues) {
    auto &operands = specs_.at(entry.opcode).operands;
    auto operand = std::find_if(
        operands.begin(), operands.end(), [&](const NativeOperandSpec &candidate) {
          return candidate.argumentIndex == entry.argumentIndex;
        });
    if (operand == operands.end())
      throw std::runtime_error("Generated allowed value references missing operand: " +
                               std::string(entry.opcode));
    operand->allowedValues.emplace(entry.value);
  }
  for (const auto &entry : kGeneratedFormAllowedValues) {
    auto &operands = specs_.at(entry.opcode).operands;
    auto operand = std::find_if(operands.begin(), operands.end(), [&](const NativeOperandSpec &candidate) {
      return candidate.argumentIndex == entry.argumentIndex;
    });
    if (operand == operands.end())
      throw std::runtime_error("Generated form-specific value references missing operand");
    operand->allowedValuesByForm[entry.form].emplace(entry.value);
  }
  for (const auto &entry : kGeneratedCallVariants) {
    auto &variants = specs_.at(entry.opcode).callVariants;
    if (entry.variantIndex != static_cast<int>(variants.size()))
      throw std::runtime_error("Generated call variants are not contiguous: " +
                               std::string(entry.opcode));
    NativeCallVariant variant;
    variant.argumentCount = entry.argumentCount;
    variants.push_back(std::move(variant));
  }
  for (const auto &entry : kGeneratedCallVariantValues) {
    auto &variants = specs_.at(entry.opcode).callVariants;
    if (entry.variantIndex < 0 ||
        entry.variantIndex >= static_cast<int>(variants.size()))
      throw std::runtime_error("Generated call variant value is invalid: " +
                               std::string(entry.opcode));
    variants[entry.variantIndex].argumentValues[entry.argumentIndex].emplace(
        entry.value);
  }
  for (const auto &entry : kGeneratedAliases) {
    auto [it, inserted] = aliases_.emplace(lower(entry.alias), entry.opcode);
    if (!inserted && it->second != entry.opcode)
      throw std::runtime_error("Conflicting generated opcode alias: " +
                               std::string(entry.alias));
  }
  for (const auto &entry : kGeneratedForms)
    specs_.at(entry.opcode).forms.emplace(entry.form);
  for (const auto &entry : kGeneratedSpecializations) {
    if (!specs_.count(entry.target))
      throw std::runtime_error("Generated specialization target is missing: " +
                               std::string(entry.target));
    specs_.at(entry.opcode).specializations.emplace(entry.form, entry.target);
  }
  for (const auto &entry : kGeneratedMemoryForms) {
    auto &spec = specs_.at(entry.opcode);
    if (entry.implicitPostUpdateBytes)
      spec.implicitPostUpdateBytes[entry.form] = entry.implicitPostUpdateBytes;
    if (entry.span) spec.memorySpanByForm[entry.form] = entry.span;
  }
  for (const auto &entry : kGeneratedMemoryModes) {
    auto spec = std::move(specs_.at(entry.key));
    spec.opcode = entry.opcode;
    spec.memorySpan = entry.span;
    memoryModes_.emplace(entry.key, std::move(spec));
    specs_.erase(entry.key);
    aliases_.erase(lower(entry.key));
  }
}

std::string InstructionCatalog::canonicalOpcode(const std::string &opcode) const {
  auto alias = aliases_.find(lower(opcode));
  return alias == aliases_.end() ? upper(opcode) : alias->second;
}

std::string InstructionCatalog::specializeOpcode(
    const std::string &opcode, const std::string &form) const {
  const std::string canonical = canonicalOpcode(opcode);
  auto spec = specs_.find(canonical);
  if (spec == specs_.end())
    return canonical;
  auto specialized = spec->second.specializations.find(form);
  return specialized == spec->second.specializations.end()
             ? canonical
             : specialized->second;
}

const NativeInstructionSpec *
InstructionCatalog::lookup(const std::string &opcode) const {
  auto spec = specs_.find(canonicalOpcode(opcode));
  return spec == specs_.end() ? nullptr : &spec->second;
}

const InstructionCatalog &defaultInstructionCatalog() {
  static const InstructionCatalog catalog;
  return catalog;
}

const NativeInstructionSpec *InstructionCatalog::lookupMemoryMode(
    const std::string &opcode, const std::string &mode) const {
  auto spec = memoryModes_.find(canonicalOpcode(opcode) + "@" + mode);
  return spec == memoryModes_.end() ? nullptr : &spec->second;
}

} // namespace vfsim
