// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER
// EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
// MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE in the root
// of the software repository for the full text of the License.

#include "ptoas_adapter/vmi/VmiCanonicalAdapter.h"

#include "api/common/instruction_catalog.h"
#include "api/common/schema.h"

#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Diagnostics.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Value.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/DenseMap.h"
#include "llvm/ADT/STLExtras.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <unordered_set>
#include <utility>
#include <vector>

namespace vfsim {
namespace {
struct ReductionCarry {
  size_t carriedIndex = 0;
  mlir::Operation *operation = nullptr;
  unsigned carriedOperandIndex = 0;
  unsigned laneOperandIndex = 0;
};

struct TypeInfo {
  CanonicalStorageKind storage = CanonicalStorageKind::Unknown;
  std::string dtype;
  std::vector<int64_t> shape;
  bool predicate = false;
  bool supported = false;
};

std::string printType(mlir::Type type) {
  std::string text;
  llvm::raw_string_ostream stream(text);
  type.print(stream);
  return text;
}

std::string normalizeDtype(llvm::StringRef dtype) {
  if (dtype == "f32") {
    return "fp32";
  }
  if (dtype == "f16") {
    return "fp16";
  }
  if (dtype == "bf16") {
    return "bf16";
  }
  if (dtype == "ui32" || dtype == "i32") {
    return "b32";
  }
  if (dtype == "ui16" || dtype == "i16") {
    return "b16";
  }
  if (dtype == "ui8" || dtype == "i8") {
    return "b8";
  }
  if (dtype == "si32") {
    return "s32";
  }
  if (dtype == "index") {
    return "s64";
  }
  return dtype.str();
}

std::optional<std::pair<int64_t, std::string>> parseVectorPayload(llvm::StringRef payload) {
  const size_t separator = payload.find('x');
  if (separator == llvm::StringRef::npos) {
    return std::nullopt;
  }
  int64_t width = 0;
  if (payload.take_front(separator).getAsInteger(10, width) || width <= 0) {
    return std::nullopt;
  }
  return std::make_pair(width, normalizeDtype(payload.drop_front(separator + 1)));
}

TypeInfo classifyType(mlir::Type type) {
  TypeInfo result;
  if (auto vector = mlir::dyn_cast<mlir::VectorType>(type)) {
    if (!vector.hasStaticShape() || vector.getRank() != 1) {
      return result;
    }
    result.storage = CanonicalStorageKind::Register;
    result.shape.assign(vector.getShape().begin(), vector.getShape().end());
    result.dtype = normalizeDtype(printType(vector.getElementType()));
    result.supported = true;
    return result;
  }
  if (auto floating = mlir::dyn_cast<mlir::FloatType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = normalizeDtype(printType(floating));
    result.supported = true;
    return result;
  }
  if (auto integer = mlir::dyn_cast<mlir::IntegerType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = integer.isSigned() ? "s" + std::to_string(integer.getWidth())
                                      : "b" + std::to_string(integer.getWidth());
    result.supported = true;
    return result;
  }
  if (mlir::isa<mlir::IndexType>(type)) {
    result.storage = CanonicalStorageKind::Scalar;
    result.dtype = "s64";
    result.supported = true;
    return result;
  }

  const std::string typeText = printType(type);
  llvm::StringRef text(typeText);
  if (text.consume_front("!pto.vreg<") && text.consume_back(">")) {
    auto parsed = parseVectorPayload(text);
    if (!parsed) {
      return result;
    }
    result.storage = CanonicalStorageKind::Register;
    result.shape = {parsed->first};
    result.dtype = parsed->second;
    result.supported = true;
    return result;
  }

  text = llvm::StringRef(typeText);
  if (text.consume_front("!pto.ptr<") && text.consume_back(">")) {
    const size_t comma = text.find(',');
    if (comma == llvm::StringRef::npos || text.drop_front(comma + 1).trim() != "ub") {
      return result;
    }
    result.storage = CanonicalStorageKind::UB;
    result.dtype = normalizeDtype(text.take_front(comma).trim());
    result.shape = {1};
    result.supported = true;
    return result;
  }

  text = llvm::StringRef(typeText);
  if (text.starts_with("!pto.mask<")) {
    result.predicate = true;
    result.supported = true;
  }
  return result;
}

std::optional<int64_t> constantInteger(mlir::Value value) {
  mlir::Operation *defining = value.getDefiningOp();
  if (defining == nullptr || defining->getName().getStringRef() != "arith.constant") {
    return std::nullopt;
  }
  auto attribute = defining->getAttrOfType<mlir::IntegerAttr>("value");
  if (!attribute) {
    return std::nullopt;
  }
  return attribute.getInt();
}

std::optional<int64_t> staticTripCount(mlir::Operation *loop) {
  if (loop->getNumOperands() < 3 || loop->getNumRegions() != 1) {
    return std::nullopt;
  }
  const auto lower = constantInteger(loop->getOperand(0));
  const auto upper = constantInteger(loop->getOperand(1));
  const auto step = constantInteger(loop->getOperand(2));
  if (!lower || !upper || !step || *step <= 0) {
    return std::nullopt;
  }
  if (*upper <= *lower) {
    return 0;
  }
  if (*lower < 0 && *upper > std::numeric_limits<int64_t>::max() + *lower) {
    return std::nullopt;
  }
  const int64_t distance = *upper - *lower;
  return distance / *step + (distance % *step != 0 ? 1 : 0);
}

CanonicalInstructionClass instructionClass(CatalogInstructionClass catalogClass) {
  switch (catalogClass) {
  case CatalogInstructionClass::Load:
    return CanonicalInstructionClass::Load;
  case CatalogInstructionClass::Store:
    return CanonicalInstructionClass::Store;
  case CatalogInstructionClass::Compute:
    return CanonicalInstructionClass::Compute;
  case CatalogInstructionClass::Control:
    return CanonicalInstructionClass::Control;
  }
  return CanonicalInstructionClass::Unknown;
}

class VmiCanonicalAdapter {
public:
  VmiCanonicalAdapter(mlir::Operation *targetLoop, VmiUnrollMode mode, int64_t factor)
      : targetLoop_(targetLoop), mode_(mode), factor_(factor) {}

  std::optional<CanonicalVfInfo> build(mlir::Operation *vectorScope, std::string &reason) {
    if (vectorScope->getNumRegions() != 1 || vectorScope->getRegion(0).empty()) {
      reason = "vector scope has no body region";
      return std::nullopt;
    }
    for (mlir::Operation &operation : vectorScope->getRegion(0).front()) {
      if (!convertOperation(&operation, info_.context, reason)) {
        return std::nullopt;
      }
    }
    return std::move(info_);
  }

private:
  mlir::Operation *targetLoop_ = nullptr;
  VmiUnrollMode mode_ = VmiUnrollMode::NoUnroll;
  int64_t factor_ = 1;
  CanonicalVfInfo info_;
  llvm::DenseMap<mlir::Value, std::string> values_;
  int64_t nextValueId_ = 0;
  int64_t nextObjectId_ = 0;
  int64_t nextInstructionId_ = 0;
  int64_t nextLoopId_ = 0;

  std::string newValueId() { return "value." + std::to_string(nextValueId_++); }

  std::string getOrCreateValue(mlir::Value value, std::string &reason) {
    auto found = values_.find(value);
    if (found != values_.end()) {
      return found->second;
    }
    const TypeInfo type = classifyType(value.getType());
    if (!type.supported || type.predicate) {
      reason = "unsupported value type " + printType(value.getType());
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue canonical;
    canonical.definitionId = id;
    canonical.logicalId = id;
    canonical.storage = type.storage;
    canonical.dtype = type.dtype;
    canonical.shape = type.shape;
    if (type.storage == CanonicalStorageKind::UB) {
      const std::string objectId = "ub." + std::to_string(nextObjectId_++);
      CanonicalStorageObject object;
      object.objectId = objectId;
      object.storage = CanonicalStorageKind::UB;
      object.shape = {1};
      info_.storageObjects.emplace(objectId, std::move(object));
      canonical.storageObjectId = objectId;
    }
    info_.values.emplace(id, std::move(canonical));
    values_[value] = id;
    return id;
  }

  std::string defineValue(mlir::Value value, const std::string &producer, std::string &reason,
                          std::optional<std::string> logicalId = std::nullopt) {
    const TypeInfo type = classifyType(value.getType());
    if (!type.supported || type.predicate) {
      reason = "unsupported result type " + printType(value.getType());
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue canonical;
    canonical.definitionId = id;
    canonical.logicalId = logicalId.value_or(id);
    canonical.storage = type.storage;
    canonical.dtype = type.dtype;
    canonical.shape = type.shape;
    canonical.producerNodeId = producer;
    if (type.storage == CanonicalStorageKind::UB) {
      const std::string objectId = "ub." + std::to_string(nextObjectId_++);
      CanonicalStorageObject object;
      object.objectId = objectId;
      object.storage = CanonicalStorageKind::UB;
      object.shape = {1};
      info_.storageObjects.emplace(objectId, std::move(object));
      canonical.storageObjectId = objectId;
    }
    info_.values.emplace(id, std::move(canonical));
    values_[value] = id;
    return id;
  }

  std::string defineMemoryVersion(const std::string &baseId, const std::string &producer,
                                  std::string &reason) {
    auto base = info_.values.find(baseId);
    if (base == info_.values.end() || base->second.storage != CanonicalStorageKind::UB ||
        !base->second.storageObjectId) {
      reason = "memory instruction has no stable UB base";
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue value = base->second;
    value.definitionId = id;
    value.producerNodeId = producer;
    info_.values.emplace(id, std::move(value));
    return id;
  }

  std::string duplicateValue(const std::string &baseId, std::optional<std::string> producer,
                             std::string &reason,
                             std::optional<std::string> logicalId = std::nullopt) {
    auto base = info_.values.find(baseId);
    if (base == info_.values.end()) {
      reason = "cannot duplicate unknown canonical value " + baseId;
      return {};
    }
    const std::string id = newValueId();
    CanonicalValue value = base->second;
    value.definitionId = id;
    value.logicalId = logicalId.value_or(id);
    value.producerNodeId = std::move(producer);
    info_.values.emplace(id, std::move(value));
    return id;
  }

  std::string createLoopEntry(const std::string &baseId, bool forceDistinct,
                              std::unordered_set<std::string> &carriedLogicalIds,
                              std::string &reason) {
    const CanonicalValue &base = info_.values.at(baseId);
    if (base.storage == CanonicalStorageKind::UB) {
      return baseId;
    }
    if (!forceDistinct && carriedLogicalIds.insert(base.logicalId).second) {
      return baseId;
    }
    const std::string duplicate = duplicateValue(baseId, std::nullopt, reason);
    if (!duplicate.empty()) {
      carriedLogicalIds.insert(info_.values.at(duplicate).logicalId);
    }
    return duplicate;
  }

  CanonicalOperand registerOperand(const std::string &id, CanonicalOperandRole role) const {
    CanonicalOperand operand;
    operand.valueId = id;
    operand.role = role;
    operand.dtype = info_.values.at(id).dtype;
    return operand;
  }

  CanonicalOperand memoryOperand(const std::string &id, CanonicalAccessKind access) const {
    CanonicalOperand operand;
    operand.valueId = id;
    operand.role = CanonicalOperandRole::Memory;
    operand.dtype = info_.values.at(id).dtype;
    CanonicalMemoryAccess memory;
    memory.baseObjectId = *info_.values.at(id).storageObjectId;
    memory.accessKind = access;
    memory.span = 1;
    operand.memoryAccess = std::move(memory);
    return operand;
  }

  bool aliasResult(mlir::Operation *operation, std::string &reason) {
    if (operation->getNumOperands() == 0 || operation->getNumResults() == 0) {
      reason = operation->getName().getStringRef().str() + " alias has no source/result";
      return false;
    }
    const std::string source = getOrCreateValue(operation->getOperand(0), reason);
    if (source.empty()) {
      return false;
    }
    for (mlir::Value result : operation->getResults()) {
      values_[result] = source;
    }
    return true;
  }

  bool materializeCastPointerResult(mlir::Operation *operation, std::string &reason) {
    if (operation->getNumOperands() == 0 || operation->getNumResults() == 0) {
      reason = "pto.castptr has no source/result";
      return false;
    }
    for (mlir::Value result : operation->getResults()) {
      const TypeInfo type = classifyType(result.getType());
      if (!type.supported || type.storage != CanonicalStorageKind::UB) {
        reason = "pto.castptr has unsupported result type " + printType(result.getType());
        return false;
      }
      if (getOrCreateValue(result, reason).empty()) {
        return false;
      }
    }
    return true;
  }

  bool materializeBitcastView(mlir::Operation *operation, std::string &reason) {
    if (operation->getNumOperands() != 1 || operation->getNumResults() != 1) {
      reason = "pto.vbitcast requires one source and one result";
      return false;
    }
    const std::string source = getOrCreateValue(operation->getOperand(0), reason);
    if (source.empty()) {
      return false;
    }
    const CanonicalValue sourceValue = info_.values.at(source);
    const TypeInfo resultType = classifyType(operation->getResult(0).getType());
    if (sourceValue.storage != CanonicalStorageKind::Register || !resultType.supported ||
        resultType.storage != CanonicalStorageKind::Register) {
      reason = "pto.vbitcast requires register source/result types";
      return false;
    }

    const std::string view =
        duplicateValue(source, sourceValue.producerNodeId, reason, sourceValue.logicalId);
    if (view.empty()) {
      return false;
    }
    CanonicalValue &viewValue = info_.values.at(view);
    viewValue.storage = resultType.storage;
    viewValue.dtype = resultType.dtype;
    viewValue.shape = resultType.shape;
    viewValue.storageObjectId.reset();
    values_[operation->getResult(0)] = view;
    return true;
  }

  bool isKnownZeroIdentity(mlir::Value value) const {
    mlir::Operation *splat = value.getDefiningOp();
    if (splat == nullptr || splat->getName().getStringRef() != "pto.vdup" ||
        splat->getNumOperands() == 0) {
      return false;
    }
    mlir::Operation *constant = splat->getOperand(0).getDefiningOp();
    if (constant == nullptr || constant->getName().getStringRef() != "arith.constant") {
      return false;
    }
    mlir::Attribute attribute = constant->getAttr("value");
    if (auto integer = mlir::dyn_cast_or_null<mlir::IntegerAttr>(attribute)) {
      return integer.getValue().isZero();
    }
    if (auto floating = mlir::dyn_cast_or_null<mlir::FloatAttr>(attribute)) {
      return floating.getValue().isZero();
    }
    return false;
  }

  std::vector<std::optional<ReductionCarry>> findLaneLocalReductions(mlir::Operation *loop,
                                                                     mlir::Block &body) const {
    const size_t carriedCount = loop->getNumOperands() - 3;
    std::vector<std::optional<ReductionCarry>> reductions(carriedCount);
    mlir::Operation *terminator = body.getTerminator();
    if (terminator->getName().getStringRef() != "scf.yield" ||
        terminator->getNumOperands() != carriedCount) {
      return reductions;
    }

    for (size_t index = 0; index < carriedCount; ++index) {
      mlir::BlockArgument iterArg = body.getArgument(index + 1);
      mlir::Value yielded = terminator->getOperand(index);
      mlir::Operation *reduction = yielded.getDefiningOp();
      if (reduction == nullptr || reduction->getBlock() != &body ||
          reduction->getName().getStringRef() != "pto.vadd" || reduction->getNumResults() != 1 ||
          reduction->getResult(0) != yielded || !iterArg.hasOneUse() ||
          iterArg.use_begin()->getOwner() != reduction || !yielded.hasOneUse() ||
          yielded.use_begin()->getOwner() != terminator ||
          !isKnownZeroIdentity(loop->getOperand(index + 3))) {
        continue;
      }

      std::optional<unsigned> carriedOperand;
      std::optional<unsigned> laneOperand;
      for (auto [operandIndex, operand] : llvm::enumerate(reduction->getOperands())) {
        if (operand == iterArg) {
          if (carriedOperand) {
            carriedOperand.reset();
          } else {
            carriedOperand = operandIndex;
          }
          continue;
        }
        if (operand.getType() == iterArg.getType()) {
          if (laneOperand) {
            laneOperand.reset();
          } else {
            laneOperand = operandIndex;
          }
        }
      }
      if (!carriedOperand || !laneOperand) {
        continue;
      }
      reductions[index] = ReductionCarry{index, reduction, *carriedOperand, *laneOperand};
    }
    return reductions;
  }

  std::string appendReductionMerge(const std::string &lhs, const std::string &rhs,
                                   std::vector<CanonicalNode> &nodes, std::string &reason) {
    const std::string form = info_.values.at(lhs).dtype;
    const std::string opcode = defaultInstructionCatalog().specializeOpcode("VADD", form);
    const NativeInstructionSpec *spec = defaultInstructionCatalog().lookup(opcode);
    if (spec == nullptr) {
      reason = "VfSim catalog has no reduction merge opcode " + opcode;
      return {};
    }

    CanonicalInstruction instruction;
    instruction.instructionId = "inst." + std::to_string(nextInstructionId_++);
    instruction.opcode = opcode;
    instruction.instructionClass = instructionClass(spec->instructionClass);
    instruction.form = form;
    instruction.inputs.push_back(registerOperand(lhs, CanonicalOperandRole::Source));
    instruction.inputs.push_back(registerOperand(rhs, CanonicalOperandRole::Source));
    const std::string result = duplicateValue(lhs, instruction.instructionId, reason);
    if (result.empty()) {
      return {};
    }
    instruction.outputs.push_back(registerOperand(result, CanonicalOperandRole::Destination));
    nodes.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
    return result;
  }

  bool convertPhysicalInstruction(mlir::Operation *operation, std::vector<CanonicalNode> &nodes,
                                  llvm::StringRef opcode, std::string form, std::string &reason) {
    const NativeInstructionSpec *spec = defaultInstructionCatalog().lookup(opcode.str());
    if (spec == nullptr) {
      reason = "VfSim catalog has no opcode " + opcode.str();
      return false;
    }
    const std::string specialized =
        defaultInstructionCatalog().specializeOpcode(opcode.str(), form);
    spec = defaultInstructionCatalog().lookup(specialized);
    if (spec == nullptr) {
      reason = "VfSim catalog has no specialized opcode " + specialized;
      return false;
    }

    CanonicalInstruction instruction;
    instruction.instructionId = "inst." + std::to_string(nextInstructionId_++);
    instruction.opcode = specialized;
    instruction.instructionClass = instructionClass(spec->instructionClass);
    instruction.form = std::move(form);

    const llvm::StringRef name = operation->getName().getStringRef();
    if (spec->instructionClass == CatalogInstructionClass::Load) {
      mlir::Value memoryValue;
      for (mlir::Value operand : operation->getOperands()) {
        if (classifyType(operand.getType()).storage == CanonicalStorageKind::UB) {
          memoryValue = operand;
          break;
        }
      }
      if (!memoryValue || operation->getNumResults() == 0) {
        reason = name.str() + " requires UB input and register result";
        return false;
      }
      const std::string memoryId = getOrCreateValue(memoryValue, reason);
      if (memoryId.empty()) {
        return false;
      }
      const std::string resultId =
          defineValue(operation->getResult(0), instruction.instructionId, reason);
      if (resultId.empty()) {
        return false;
      }
      instruction.inputs.push_back(memoryOperand(memoryId, CanonicalAccessKind::Read));
      instruction.outputs.push_back(registerOperand(resultId, CanonicalOperandRole::Destination));
    } else if (spec->instructionClass == CatalogInstructionClass::Store) {
      mlir::Value sourceValue;
      mlir::Value memoryValue;
      for (mlir::Value operand : operation->getOperands()) {
        const TypeInfo type = classifyType(operand.getType());
        if (!sourceValue && type.storage == CanonicalStorageKind::Register) {
          sourceValue = operand;
        }
        if (!memoryValue && type.storage == CanonicalStorageKind::UB) {
          memoryValue = operand;
        }
      }
      if (!sourceValue || !memoryValue) {
        reason = name.str() + " requires register source and UB output";
        return false;
      }
      const std::string sourceId = getOrCreateValue(sourceValue, reason);
      const std::string memoryId = getOrCreateValue(memoryValue, reason);
      if (sourceId.empty() || memoryId.empty()) {
        return false;
      }
      const std::string outputId = defineMemoryVersion(memoryId, instruction.instructionId, reason);
      if (outputId.empty()) {
        return false;
      }
      instruction.inputs.push_back(registerOperand(sourceId, CanonicalOperandRole::Source));
      instruction.outputs.push_back(memoryOperand(outputId, CanonicalAccessKind::Write));
      for (mlir::Value result : operation->getResults()) {
        values_[result] = outputId;
      }
    } else {
      llvm::SmallVector<mlir::Value> modeledOperands;
      for (mlir::Value operand : operation->getOperands()) {
        const TypeInfo type = classifyType(operand.getType());
        if (!type.supported || type.predicate) {
          continue;
        }
        if (type.storage != CanonicalStorageKind::Register &&
            type.storage != CanonicalStorageKind::Scalar) {
          continue;
        }
        modeledOperands.push_back(operand);
      }
      llvm::SmallVector<const NativeOperandSpec *> expectedInputs;
      llvm::SmallVector<const NativeOperandSpec *> expectedOutputs;
      for (const NativeOperandSpec &operand : spec->operands) {
        if (operand.kind == CatalogArgumentKind::Predicate) {
          continue;
        }
        if (operand.direction == CatalogOperandDirection::Input) {
          expectedInputs.push_back(&operand);
        } else if (operand.direction == CatalogOperandDirection::Output) {
          expectedOutputs.push_back(&operand);
        }
      }
      if (modeledOperands.size() != expectedInputs.size()) {
        reason = name.str() + " operand count does not match VfSim Catalog";
        return false;
      }
      for (auto [operand, expected] : llvm::zip_equal(modeledOperands, expectedInputs)) {
        const std::string id = getOrCreateValue(operand, reason);
        if (id.empty()) {
          return false;
        }
        instruction.inputs.push_back(registerOperand(id, expected->role == "scalar"
                                                             ? CanonicalOperandRole::Scalar
                                                             : CanonicalOperandRole::Source));
      }
      size_t outputIndex = 0;
      for (mlir::Value result : operation->getResults()) {
        const TypeInfo type = classifyType(result.getType());
        if (type.storage != CanonicalStorageKind::Register) {
          continue;
        }
        const std::string id = defineValue(result, instruction.instructionId, reason);
        if (id.empty()) {
          return false;
        }
        instruction.outputs.push_back(registerOperand(id, CanonicalOperandRole::Destination));
        ++outputIndex;
      }
      if (outputIndex != expectedOutputs.size()) {
        reason = name.str() + " result count does not match VfSim Catalog";
        return false;
      }
    }

    nodes.push_back(CanonicalNode::makeInstruction(std::move(instruction)));
    return true;
  }

  bool createInitialLoopEntries(mlir::Operation *operation, size_t carriedCount,
                                std::vector<std::string> &entries, std::string &reason) {
    entries.clear();
    entries.reserve(carriedCount);
    std::unordered_set<std::string> carriedLogicalIds;
    for (size_t index = 0; index < carriedCount; ++index) {
      const std::string base = getOrCreateValue(operation->getOperand(index + 3), reason);
      if (base.empty()) {
        return false;
      }
      const std::string entry =
          createLoopEntry(base, /*forceDistinct=*/false, carriedLogicalIds, reason);
      if (entry.empty()) {
        return false;
      }
      entries.push_back(entry);
    }
    return true;
  }

  bool finishLoop(CanonicalLoop loop, const std::vector<std::string> &entries,
                  const std::vector<std::string> &backEdges, std::vector<CanonicalNode> &nodes,
                  std::vector<std::string> &exits, std::string &reason) {
    exits.clear();
    exits.reserve(entries.size());
    for (size_t index = 0; index < entries.size(); ++index) {
      const CanonicalValue &entry = info_.values.at(entries[index]);
      if (entry.storage == CanonicalStorageKind::UB) {
        exits.push_back(entries[index]);
        continue;
      }
      const std::string exit = duplicateValue(entries[index], loop.loopId, reason, entry.logicalId);
      if (exit.empty()) {
        return false;
      }
      CanonicalLoopCarriedValue carried;
      carried.logicalId = entry.logicalId;
      carried.entryValueId = entries[index];
      carried.backEdgeValueId = backEdges[index];
      carried.exitValueId = exit;
      loop.carriedValues.push_back(std::move(carried));
      exits.push_back(exit);
    }
    nodes.push_back(CanonicalNode::makeLoop(std::move(loop)));
    return true;
  }

  bool convertLoopSegment(mlir::Block &body, int64_t count, int64_t start, int64_t step,
                          const std::vector<std::string> &entries, int64_t bodyCopies,
                          int64_t unroll, std::vector<CanonicalNode> &nodes,
                          std::vector<std::string> &exits, std::string &reason) {
    CanonicalLoop loop;
    loop.loopId = "loop." + std::to_string(nextLoopId_++);
    loop.induction.variableId = loop.loopId + ".iv";
    loop.induction.start = start;
    loop.induction.step = step;
    loop.count = count;
    loop.unroll = unroll;

    std::vector<std::string> current = entries;
    for (int64_t copy = 0; copy < bodyCopies; ++copy) {
      for (size_t index = 0; index < entries.size(); ++index) {
        values_[body.getArgument(index + 1)] = current[index];
      }
      for (mlir::Operation &bodyOperation : body.without_terminator()) {
        if (!convertOperation(&bodyOperation, loop.body, reason)) {
          return false;
        }
      }
      mlir::Operation *terminator = body.getTerminator();
      if (terminator->getName().getStringRef() != "scf.yield" ||
          terminator->getNumOperands() != entries.size()) {
        reason = "scf.for terminator must be scf.yield with all carried values";
        return false;
      }
      for (size_t index = 0; index < entries.size(); ++index) {
        current[index] = getOrCreateValue(terminator->getOperand(index), reason);
        if (current[index].empty()) {
          return false;
        }
      }
    }

    return finishLoop(std::move(loop), entries, current, nodes, exits, reason);
  }

  bool convertLaneLocalTargetLoop(mlir::Operation *operation, mlir::Block &body, int64_t tripCount,
                                  const std::vector<std::optional<ReductionCarry>> &reductions,
                                  std::vector<CanonicalNode> &nodes, std::string &reason) {
    const size_t carriedCount = operation->getNumOperands() - 3;
    const int64_t mainCount = tripCount / factor_;
    const int64_t remainder = tripCount % factor_;
    CanonicalLoop loop;
    loop.loopId = "loop." + std::to_string(nextLoopId_++);
    loop.induction.variableId = loop.loopId + ".iv";
    loop.induction.start = constantInteger(operation->getOperand(0)).value_or(0);
    loop.induction.step = constantInteger(operation->getOperand(2)).value_or(1) * factor_;
    loop.count = mainCount;
    loop.unroll = 1;

    std::vector<std::vector<std::string>> entries(carriedCount);
    std::vector<std::vector<std::string>> current(carriedCount);
    std::unordered_set<std::string> carriedLogicalIds;
    for (size_t index = 0; index < carriedCount; ++index) {
      const std::string base = getOrCreateValue(operation->getOperand(index + 3), reason);
      if (base.empty()) {
        return false;
      }
      const size_t lanes = reductions[index] ? factor_ : 1;
      for (size_t lane = 0; lane < lanes; ++lane) {
        const std::string entry =
            createLoopEntry(base, /*forceDistinct=*/lane != 0, carriedLogicalIds, reason);
        if (entry.empty()) {
          return false;
        }
        entries[index].push_back(entry);
      }
      current[index] = entries[index];
    }

    std::vector<std::vector<CanonicalNode>> laneBodies(factor_);
    for (int64_t lane = 0; lane < factor_; ++lane) {
      for (size_t index = 0; index < carriedCount; ++index) {
        const size_t stateIndex = reductions[index] ? lane : 0;
        const CanonicalValue &entry = info_.values.at(entries[index][stateIndex]);
        values_[body.getArgument(index + 1)] = entry.storage == CanonicalStorageKind::UB
                                                   ? entries[index][stateIndex]
                                                   : current[index][stateIndex];
      }
      for (mlir::Operation &bodyOperation : body.without_terminator()) {
        if (!convertOperation(&bodyOperation, laneBodies[lane], reason)) {
          return false;
        }
      }

      mlir::Operation *terminator = body.getTerminator();
      for (size_t index = 0; index < carriedCount; ++index) {
        const std::string yielded = getOrCreateValue(terminator->getOperand(index), reason);
        if (yielded.empty()) {
          return false;
        }
        const size_t stateIndex = reductions[index] ? lane : 0;
        current[index][stateIndex] = yielded;
      }
    }

    if (mode_ == VmiUnrollMode::Abcabc) {
      for (auto &laneBody : laneBodies) {
        for (CanonicalNode &node : laneBody) {
          loop.body.push_back(std::move(node));
        }
      }
    } else {
      const size_t width = laneBodies.front().size();
      if (llvm::any_of(laneBodies,
                       [width](const auto &laneBody) { return laneBody.size() != width; })) {
        reason = "AABBCC lanes produced inconsistent physical instruction "
                 "counts";
        return false;
      }
      for (size_t position = 0; position < width; ++position) {
        for (auto &laneBody : laneBodies) {
          loop.body.push_back(std::move(laneBody[position]));
        }
      }
    }

    std::vector<std::vector<std::string>> exits(carriedCount);
    for (size_t index = 0; index < carriedCount; ++index) {
      const CanonicalValue &entryValue = info_.values.at(entries[index][0]);
      if (entryValue.storage == CanonicalStorageKind::UB) {
        exits[index].push_back(entries[index][0]);
        continue;
      }
      exits[index].reserve(entries[index].size());
      for (size_t state = 0; state < entries[index].size(); ++state) {
        const std::string entryLogicalId = info_.values.at(entries[index][state]).logicalId;
        const std::string exit =
            duplicateValue(entries[index][state], loop.loopId, reason, entryLogicalId);
        if (exit.empty()) {
          return false;
        }
        exits[index].push_back(exit);
        CanonicalLoopCarriedValue carried;
        carried.logicalId = info_.values.at(entries[index][state]).logicalId;
        carried.entryValueId = entries[index][state];
        carried.backEdgeValueId = current[index][state];
        carried.exitValueId = exit;
        loop.carriedValues.push_back(std::move(carried));
      }
    }

    nodes.push_back(CanonicalNode::makeLoop(std::move(loop)));
    std::vector<std::string> results;
    results.reserve(carriedCount);
    for (size_t index = 0; index < carriedCount; ++index) {
      std::string result = exits[index].front();
      if (reductions[index]) {
        for (size_t lane = 1; lane < exits[index].size(); ++lane) {
          result = appendReductionMerge(result, exits[index][lane], nodes, reason);
          if (result.empty()) {
            return false;
          }
        }
      }
      results.push_back(result);
    }

    if (remainder != 0) {
      const int64_t step = constantInteger(operation->getOperand(2)).value_or(1);
      const int64_t tailStart =
          constantInteger(operation->getOperand(0)).value_or(0) + mainCount * factor_ * step;
      std::vector<std::string> tailExits;
      if (!convertLoopSegment(body, remainder, tailStart, step, results,
                              /*bodyCopies=*/1, /*unroll=*/1, nodes, tailExits, reason)) {
        return false;
      }
      results = std::move(tailExits);
    }

    for (size_t index = 0; index < carriedCount; ++index) {
      values_[operation->getResult(index)] = results[index];
    }
    return true;
  }

  bool convertLoop(mlir::Operation *operation, std::vector<CanonicalNode> &nodes,
                   std::string &reason) {
    const auto tripCount = staticTripCount(operation);
    if (!tripCount || *tripCount < 0 || operation->getNumRegions() != 1 ||
        operation->getRegion(0).empty()) {
      reason = "scf.for requires a static positive-step trip count";
      return false;
    }
    mlir::Block &body = operation->getRegion(0).front();
    const size_t carriedCount = operation->getNumOperands() - 3;
    if (body.getNumArguments() != carriedCount + 1 || operation->getNumResults() != carriedCount) {
      reason = "scf.for operand, block argument, and result counts disagree";
      return false;
    }

    const bool isTarget = operation == targetLoop_;
    if (isTarget && mode_ != VmiUnrollMode::NoUnroll) {
      auto reductions = findLaneLocalReductions(operation, body);
      if (*tripCount / factor_ != 0 &&
          llvm::any_of(reductions, [](const auto &reduction) { return reduction.has_value(); })) {
        return convertLaneLocalTargetLoop(operation, body, *tripCount, reductions, nodes, reason);
      }
    }

    std::vector<std::string> entries;
    if (!createInitialLoopEntries(operation, carriedCount, entries, reason)) {
      return false;
    }

    const int64_t lower = constantInteger(operation->getOperand(0)).value_or(0);
    const int64_t step = constantInteger(operation->getOperand(2)).value_or(1);
    std::vector<std::string> current = entries;

    if (!isTarget || mode_ == VmiUnrollMode::NoUnroll) {
      if (!convertLoopSegment(body, *tripCount, lower, step, entries,
                              /*bodyCopies=*/1, /*unroll=*/1, nodes, current, reason)) {
        return false;
      }
    } else {
      const int64_t mainCount = *tripCount / factor_;
      const int64_t remainder = *tripCount % factor_;
      if (mainCount != 0) {
        const int64_t count = mode_ == VmiUnrollMode::Abcabc ? mainCount : mainCount * factor_;
        const int64_t mainStep = mode_ == VmiUnrollMode::Abcabc ? step * factor_ : step;
        const int64_t bodyCopies = mode_ == VmiUnrollMode::Abcabc ? factor_ : 1;
        const int64_t unroll = mode_ == VmiUnrollMode::Aabbcc ? factor_ : 1;
        if (!convertLoopSegment(body, count, lower, mainStep, entries, bodyCopies, unroll, nodes,
                                current, reason)) {
          return false;
        }
      }
      if (remainder != 0) {
        const int64_t tailStart = lower + mainCount * factor_ * step;
        std::vector<std::string> tailExits;
        if (!convertLoopSegment(body, remainder, tailStart, step, current,
                                /*bodyCopies=*/1, /*unroll=*/1, nodes, tailExits, reason)) {
          return false;
        }
        current = std::move(tailExits);
      }
    }

    for (size_t index = 0; index < carriedCount; ++index) {
      values_[operation->getResult(index)] = current[index];
    }
    return true;
  }

  bool convertOperation(mlir::Operation *operation, std::vector<CanonicalNode> &nodes,
                        std::string &reason) {
    const llvm::StringRef name = operation->getName().getStringRef();
    if (name == "scf.for") {
      return convertLoop(operation, nodes, reason);
    }
    if (name == "scf.yield" || name == "pto.yield" || name == "pto.vecscope_yield") {
      return true;
    }
    if (name == "pto.vbitcast") {
      return materializeBitcastView(operation, reason);
    }
    if (name == "pto.castptr") {
      return materializeCastPointerResult(operation, reason);
    }
    if (name == "pto.addptr") {
      return aliasResult(operation, reason);
    }
    if (name.starts_with("arith.") || name == "pto.pset_b16" || name == "pto.pset_b32" ||
        name == "pto.pge_b16" || name == "pto.pge_b32") {
      return true;
    }

    llvm::StringRef opcode;
    if (name == "pto.vlds") {
      opcode = "VLDS";
    } else if (name == "pto.vsts") {
      opcode = "VSTS";
    } else if (name == "pto.vsstb") {
      opcode = "VSSTB";
    } else if (name == "pto.vadd") {
      opcode = "VADD";
    } else if (name == "pto.vsub") {
      opcode = "VSUB";
    } else if (name == "pto.vadds") {
      opcode = "VADDS";
    } else if (name == "pto.vmuls") {
      opcode = "VMULS";
    } else if (name == "pto.vmaxs") {
      opcode = "VMAXS";
    } else if (name == "pto.vmins") {
      opcode = "VMINS";
    } else if (name == "pto.vmul") {
      opcode = "VMUL";
    } else if (name == "pto.vmax") {
      opcode = "VMAX";
    } else if (name == "pto.vmin") {
      opcode = "VMIN";
    } else if (name == "pto.vdiv") {
      opcode = "VDIV";
    } else if (name == "pto.vand") {
      opcode = "VAND";
    } else if (name == "pto.vexp") {
      opcode = "VEXP";
    } else if (name == "pto.vabs") {
      opcode = "VABS";
    } else if (name == "pto.vdup") {
      opcode = "VDUP";
    } else if (name == "pto.vcmax") {
      opcode = "VCMAX";
    } else if (name == "pto.vcmin") {
      opcode = "VCMIN";
    } else if (name == "pto.vcadd") {
      opcode = "VCADD";
    } else if (name == "pto.vcgmax") {
      opcode = "VCGMAX";
    } else if (name == "pto.vsel") {
      opcode = "VSEL";
    } else if (name == "pto.vshls") {
      opcode = "VSHLS";
    } else if (name == "pto.vshrs") {
      opcode = "VSHRS";
    } else if (name == "pto.vbr") {
      opcode = "VBR";
    } else if (name == "pto.vexpdif") {
      opcode = "VEXPDIF";
    } else if (name == "pto.vcvt") {
      opcode = "VCVT";
    } else if (name == "pto.vpack") {
      opcode = "VPACK";
    } else {
      reason = "unsupported operation " + name.str();
      return false;
    }

    std::string form;
    if (opcode == "VSSTB") {
      form = "b16";
    } else if (opcode == "VPACK") {
      form = "b32";
    } else if (opcode == "VCVT") {
      if (operation->getNumOperands() == 0 || operation->getNumResults() == 0) {
        reason = "pto.vcvt requires source and result";
        return false;
      }
      const TypeInfo source = classifyType(operation->getOperand(0).getType());
      const TypeInfo result = classifyType(operation->getResult(0).getType());
      form = source.dtype + "_to_" + result.dtype;
      if (form == "fp32_to_bf16") {
        form = "f32_to_bf16";
      } else if (form == "fp32_to_fp16") {
        form = "f32_to_f16";
      } else if (form == "fp16_to_fp32") {
        form = "f16_to_f32";
      } else if (form == "fp32_to_s32") {
        form = "f32_to_s32";
      } else if (form == "s32_to_fp32") {
        form = "s32_to_f32";
      }
    } else {
      mlir::Type formType;
      if (opcode == "VLDS" && operation->getNumResults() != 0) {
        formType = operation->getResult(0).getType();
      } else if (opcode == "VSTS" && operation->getNumOperands() != 0) {
        formType = operation->getOperand(0).getType();
      } else if (operation->getNumResults() != 0) {
        formType = operation->getResult(0).getType();
      }
      if (!formType) {
        reason = name.str() + " has no form-bearing type";
        return false;
      }
      form = classifyType(formType).dtype;
    }
    return convertPhysicalInstruction(operation, nodes, opcode, std::move(form), reason);
  }
};

} // namespace

std::optional<int64_t> getStaticLoopTripCount(mlir::Operation *loop) {
  if (loop == nullptr) {
    return std::nullopt;
  }
  return staticTripCount(loop);
}

std::optional<CanonicalVfInfo> lowerVmiScope(mlir::Operation *vectorScope,
                                             mlir::Operation *targetLoop, VmiUnrollMode mode,
                                             int64_t factor, std::string &reason) {
  const auto tripCount = getStaticLoopTripCount(targetLoop);
  if (vectorScope == nullptr || !tripCount || factor < 1 || factor > 8 ||
      (mode == VmiUnrollMode::NoUnroll ? factor != 1 : factor > *tripCount)) {
    reason = "invalid VMI scope, target loop or unroll factor";
    return std::nullopt;
  }
  VmiCanonicalAdapter adapter(targetLoop, mode, factor);
  return adapter.build(vectorScope, reason);
}
} // namespace vfsim
