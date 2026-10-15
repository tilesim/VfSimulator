// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/common/instruction_validation.h"
namespace vfsim {
namespace {
std::optional<CatalogInstructionClass> catalogClass(CanonicalInstructionClass value) {
  switch (value) {
  case CanonicalInstructionClass::Load:
    return CatalogInstructionClass::Load;
  case CanonicalInstructionClass::Store:
    return CatalogInstructionClass::Store;
  case CanonicalInstructionClass::Compute:
    return CatalogInstructionClass::Compute;
  case CanonicalInstructionClass::Control:
    return CatalogInstructionClass::Control;
  case CanonicalInstructionClass::Unknown:
    return std::nullopt;
  }
  return std::nullopt;
}

std::string operandRoleName(CanonicalOperandRole role) {
  switch (role) {
  case CanonicalOperandRole::Source:
    return "source";
  case CanonicalOperandRole::Destination:
    return "destination";
  case CanonicalOperandRole::Memory:
    return "memory";
  case CanonicalOperandRole::Scalar:
    return "scalar";
  case CanonicalOperandRole::Predicate:
    return "predicate";
  case CanonicalOperandRole::Config:
    return "config";
  case CanonicalOperandRole::Unknown:
    return "unknown";
  }
  return "unknown";
}

} // namespace

const NativeInstructionSpec *validateInstructionForm(
    const CanonicalVfInfo &vfInfo, const CanonicalInstruction &inst,
    const std::string &nodePath, const InstructionError &error) {
  const NativeInstructionSpec *catalogSpec =
      defaultInstructionCatalog().lookup(inst.opcode);
  if (auto mode = inst.attributes.find("catalog_mode");
      mode != inst.attributes.end()) {
    const auto *name = std::get_if<std::string>(&mode->second);
    const auto *variant =
        name ? defaultInstructionCatalog().lookupMemoryMode(inst.opcode,
                                                            *name)
             : nullptr;
    if (!variant)
      error("unsupported_catalog_mode", "Unknown Catalog memory mode",
            nodePath, inst.sourceLocation);
    else {
      catalogSpec = variant;
      for (const auto *operands : {&inst.inputs, &inst.outputs})
        for (const auto &operand : *operands)
          if (operand.memoryAccess &&
              operand.memoryAccess->span != variant->memorySpan)
            error("catalog_memory_span_mismatch",
                  "Memory span conflicts with Catalog mode", nodePath,
                  inst.sourceLocation);
    }
  }
  for (const auto *operands : {&inst.inputs, &inst.outputs})
    for (const auto &operand : *operands) {
      auto value = vfInfo.values.find(operand.valueId);
      if (!catalogSpec && value != vfInfo.values.end() &&
          value->second.storage ==
              CanonicalStorageKind::PredicateRegister)
        error(
            "unsupported_predicate_semantics",
            "Predicate instructions require a complete Catalog signature",
            nodePath, inst.sourceLocation);
    }
  for (const auto &[key, value] : inst.attributes)
    if (const auto *text = std::get_if<std::string>(&value);
        text && *text == "MODE_MERGING")
      error("unsupported_merging_mode",
            "MODE_MERGING requires an explicit old destination input",
            nodePath, inst.sourceLocation);
  if (catalogSpec) {
    for (const auto *operands : {&inst.inputs, &inst.outputs})
      for (const auto &operand : *operands) {
        if (!operand.memoryAccess)
          continue;
        const auto &memory = *operand.memoryAccess;
        auto delta =
            catalogSpec->implicitPostUpdateBytes.find(inst.form);
        if (delta != catalogSpec->implicitPostUpdateBytes.end() &&
            (memory.updateMode != "post_update" ||
             !memory.addressStateId || memory.addressStateId->empty() ||
             !memory.postUpdateDeltaBytes ||
             memory.postUpdateDeltaBytes->constant != delta->second ||
             !memory.postUpdateDeltaBytes->terms.empty()))
          error("catalog_implicit_update_mismatch",
                "Memory access must declare its Catalog implicit pointer "
                "update",
                nodePath, inst.sourceLocation);
        auto span = catalogSpec->memorySpanByForm.find(inst.form);
        if (span != catalogSpec->memorySpanByForm.end() &&
            memory.span != span->second)
          error("catalog_memory_span_mismatch",
                "Memory span conflicts with Catalog form", nodePath,
                inst.sourceLocation);
      }
    if (inst.opcode != catalogSpec->opcode)
      error("noncanonical_opcode",
            "Known opcode must use its canonical Catalog name", nodePath,
            inst.sourceLocation);
    if (const auto cls = catalogClass(inst.instructionClass);
        cls && *cls != catalogSpec->instructionClass)
      error("catalog_instruction_class_mismatch",
            "Instruction class conflicts with Catalog semantics",
            nodePath, inst.sourceLocation);
    const bool validForm = catalogSpec->virtualOpcode ||
                           catalogSpec->forms.count(inst.form) ||
                           catalogSpec->specializations.count(inst.form);
    if (!validForm)
      error("catalog_instruction_form_mismatch",
            "Instruction form conflicts with Catalog semantics", nodePath,
            inst.sourceLocation);
    auto specialized = catalogSpec->specializations.find(inst.form);
    if (specialized != catalogSpec->specializations.end() &&
        specialized->second != inst.opcode)
      error("catalog_specialization_required",
            "Virtual opcode/form must use specialized opcode", nodePath,
            inst.sourceLocation);
    if (!catalogSpec->alignStateOperation.empty()) {
      const auto operation =
          inst.attributes.find("align_state_operation");
      const auto state = inst.attributes.find("align_state_id");
      const auto *operationValue =
          operation == inst.attributes.end()
              ? nullptr
              : std::get_if<std::string>(&operation->second);
      const auto *stateValue =
          state == inst.attributes.end()
              ? nullptr
              : std::get_if<std::string>(&state->second);
      if (operationValue == nullptr ||
          *operationValue != catalogSpec->alignStateOperation ||
          stateValue == nullptr || stateValue->empty())
        error("catalog_align_state_mismatch",
              "Instruction must declare its Catalog align-state "
              "operation and state ID",
              nodePath, inst.sourceLocation);
    }
  }
  return catalogSpec;
}

void validateInstructionOperands(
    const CanonicalVfInfo &vfInfo, const CanonicalInstruction &inst,
    const NativeInstructionSpec *catalogSpec, const std::string &nodePath,
    const InstructionError &error) {
  if (catalogSpec) {
    std::vector<const NativeOperandSpec *> expectedInputs;
    std::vector<const NativeOperandSpec *> expectedOutputs;
    for (const auto &operand : catalogSpec->operands) {
      if ((operand.direction == CatalogOperandDirection::Input ||
           operand.direction == CatalogOperandDirection::ReadWrite) &&
          (vfInfo.schemaVersion == 2 ||
           operand.kind != CatalogArgumentKind::Predicate))
        expectedInputs.push_back(&operand);
      if (operand.direction == CatalogOperandDirection::Output ||
          operand.direction == CatalogOperandDirection::ReadWrite)
        expectedOutputs.push_back(&operand);
    }
    auto actualOperands =
        [](const std::vector<CanonicalOperand> &operands) {
          std::vector<const CanonicalOperand *> result;
          for (const auto &operand : operands)
            if (operand.role != CanonicalOperandRole::Config)
              result.push_back(&operand);
          return result;
        };
    const auto actualInputs = actualOperands(inst.inputs);
    const auto actualOutputs = actualOperands(inst.outputs);
    auto validateCatalogOperands = [&](const auto &actual,
                                       const auto &expected,
                                       const std::string &direction) {
      if (actual.size() != expected.size()) {
        error("catalog_operand_count_mismatch",
              "Operand count conflicts with Catalog signature", nodePath,
              inst.sourceLocation);
        return;
      }
      for (size_t operandIndex = 0; operandIndex < actual.size();
           ++operandIndex) {
        const auto &operand = *actual[operandIndex];
        const auto &operandSpec = *expected[operandIndex];
        const std::string operandPath = nodePath + "." + direction + "[" +
                                        std::to_string(operandIndex) +
                                        "]";
        const auto expectedRole =
            direction == "inputs" &&
                    operandSpec.direction ==
                        CatalogOperandDirection::ReadWrite
                ? "source"
                : operandSpec.role;
        if (operandRoleName(operand.role) != expectedRole)
          error("catalog_operand_role_mismatch",
                "Operand role conflicts with Catalog signature",
                operandPath, inst.sourceLocation);
        auto value = vfInfo.values.find(operand.valueId);
        if (value == vfInfo.values.end())
          continue;
        if (!operandSpec.allowedDtypes.empty() &&
            !operandSpec.allowedDtypes.count(value->second.dtype))
          error("catalog_operand_dtype_mismatch",
                "Operand dtype conflicts with Catalog signature",
                operandPath, inst.sourceLocation);
        if (catalogSpec->memorySpan &&
            (value->second.storage == CanonicalStorageKind::Register ||
             value->second.storage == CanonicalStorageKind::UB) &&
            !catalogSpec->forms.count(value->second.dtype))
          error("catalog_operand_dtype_mismatch",
                "Operand dtype conflicts with Catalog memory mode",
                operandPath, inst.sourceLocation);
        const CanonicalStorageKind storage = value->second.storage;
        bool storageMatches = true;
        switch (operandSpec.kind) {
        case CatalogArgumentKind::Register:
          storageMatches = storage == CanonicalStorageKind::Register;
          break;
        case CatalogArgumentKind::UB:
          storageMatches = storage == CanonicalStorageKind::UB;
          break;
        case CatalogArgumentKind::Scalar:
          storageMatches = storage == CanonicalStorageKind::Scalar;
          break;
        case CatalogArgumentKind::RegisterOrScalar:
          storageMatches = storage == CanonicalStorageKind::Register ||
                           storage == CanonicalStorageKind::Scalar;
          break;
        case CatalogArgumentKind::Predicate:
          storageMatches =
              storage == CanonicalStorageKind::PredicateRegister;
          break;
        case CatalogArgumentKind::Config:
        case CatalogArgumentKind::AlignState:
          break;
        }
        if (!storageMatches)
          error("catalog_operand_storage_mismatch",
                "Operand storage conflicts with Catalog signature",
                operandPath, inst.sourceLocation);
      }
    };
    validateCatalogOperands(actualInputs, expectedInputs, "inputs");
    validateCatalogOperands(actualOutputs, expectedOutputs, "outputs");
  }
}
} // namespace vfsim
