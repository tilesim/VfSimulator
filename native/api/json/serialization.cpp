// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/json/serialization.h"

#include <initializer_list>
#include <stdexcept>
#include <string_view>
#include <unordered_set>
#include <utility>

namespace vfsim {
namespace {

using Object = json::Value::Object;

void rejectUnknownFields(const Object &object,
                         std::initializer_list<std::string_view> allowedFields,
                         const std::string &context) {
  std::unordered_set<std::string> allowed;
  for (const std::string_view field : allowedFields)
    allowed.emplace(field);
  for (const auto &[field, value] : object) {
    (void)value;
    if (allowed.find(field) == allowed.end())
      throw std::runtime_error(context + " contains unknown field: " + field);
  }
}

const json::Value *find(const Object &object, const std::string &key) {
  auto it = object.find(key);
  return it == object.end() ? nullptr : &it->second;
}

const json::Value &required(const Object &object, const char *key) {
  const json::Value *value = find(object, key);
  if (!value)
    throw std::runtime_error(std::string("Canonical JSON missing field: ") +
                             key);
  return *value;
}

std::optional<std::string> optionalString(const Object &object,
                                          const std::string &key) {
  const json::Value *value = find(object, key);
  if (!value || value->isNull())
    return std::nullopt;
  if (!value->isString())
    throw std::runtime_error("Canonical JSON field must be string: " + key);
  return value->asString();
}

std::optional<int64_t> optionalInt(const Object &object,
                                   const std::string &key) {
  const json::Value *value = find(object, key);
  if (!value || value->isNull())
    return std::nullopt;
  if (!value->isInt())
    throw std::runtime_error("Canonical JSON field must be int: " + key);
  return value->asInt();
}

CanonicalScalar scalar(const json::Value &value) {
  if (value.isNull())
    return std::monostate{};
  if (value.isBool())
    return value.asBool();
  if (value.isInt())
    return value.asInt();
  if (value.isDouble())
    return value.asDouble();
  if (value.isString())
    return value.asString();
  throw std::runtime_error("Canonical JSON scalar cannot be object/array");
}

std::map<std::string, CanonicalScalar> scalarMap(const json::Value *value) {
  std::map<std::string, CanonicalScalar> result;
  if (!value)
    return result;
  for (const auto &[key, item] : value->asObject())
    result.emplace(key, scalar(item));
  return result;
}

std::vector<int64_t> shape(const json::Value *value) {
  std::vector<int64_t> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray()) {
    if (!item.isInt())
      throw std::runtime_error("Canonical JSON shape must contain ints");
    result.push_back(item.asInt());
  }
  return result;
}

std::optional<CanonicalSourceLocation>
sourceLocation(const json::Value *value) {
  if (!value || value->isNull())
    return std::nullopt;
  const auto &object = value->asObject();
  rejectUnknownFields(object, {"source", "line", "column", "path"},
                      "canonical source_location");
  CanonicalSourceLocation result;
  result.source = optionalString(object, "source");
  result.line = optionalInt(object, "line");
  result.column = optionalInt(object, "column");
  result.path = optionalString(object, "path");
  return result;
}

CanonicalStorageKind storageKind(const std::string &value) {
  if (value == "Register")
    return CanonicalStorageKind::Register;
  if (value == "PredicateRegister")
    return CanonicalStorageKind::PredicateRegister;
  if (value == "UB")
    return CanonicalStorageKind::UB;
  if (value == "Scalar")
    return CanonicalStorageKind::Scalar;
  throw std::runtime_error("unsupported canonical storage: " + value);
}

CanonicalInstructionClass instructionClass(const std::string &value) {
  if (value == "load")
    return CanonicalInstructionClass::Load;
  if (value == "store")
    return CanonicalInstructionClass::Store;
  if (value == "compute")
    return CanonicalInstructionClass::Compute;
  if (value == "control")
    return CanonicalInstructionClass::Control;
  throw std::runtime_error("unsupported canonical instruction class: " + value);
}

CanonicalOperandRole operandRole(const std::string &value) {
  if (value == "source")
    return CanonicalOperandRole::Source;
  if (value == "destination")
    return CanonicalOperandRole::Destination;
  if (value == "memory")
    return CanonicalOperandRole::Memory;
  if (value == "scalar")
    return CanonicalOperandRole::Scalar;
  if (value == "predicate")
    return CanonicalOperandRole::Predicate;
  if (value == "config")
    return CanonicalOperandRole::Config;
  throw std::runtime_error("unsupported canonical operand role: " + value);
}

CanonicalDependencyKind dependencyKind(const std::string &value) {
  if (value == "memory")
    return CanonicalDependencyKind::Memory;
  if (value == "control")
    return CanonicalDependencyKind::Control;
  throw std::runtime_error("unsupported canonical dependency kind: " + value);
}

CanonicalIntegerExpression integerExpression(const json::Value &value) {
  if (value.isInt())
    return value.asInt();
  if (value.isString())
    return value.asString();
  throw std::runtime_error("canonical integer expression must be int/string");
}

CanonicalDependencyRef dependency(const json::Value &value) {
  const auto &object = value.asObject();
  rejectUnknownFields(object, {"producer_node_id", "kind", "operand_index"},
                      "canonical dependency");
  CanonicalDependencyRef result;
  result.producerNodeId = required(object, "producer_node_id").asString();
  result.kind = dependencyKind(required(object, "kind").asString());
  result.operandIndex = optionalInt(object, "operand_index");
  return result;
}

std::vector<CanonicalDependencyRef> dependencies(const json::Value *value) {
  std::vector<CanonicalDependencyRef> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray())
    result.push_back(dependency(item));
  return result;
}

CanonicalOperand operand(const json::Value &value) {
  const auto &object = value.asObject();
  rejectUnknownFields(object, {"value_id", "role", "dtype", "memory_access"},
                      "canonical operand");
  CanonicalOperand result;
  result.valueId = required(object, "value_id").asString();
  result.role = operandRole(required(object, "role").asString());
  result.dtype = optionalString(object, "dtype");
  if (const json::Value *memoryValue = find(object, "memory_access")) {
    if (!memoryValue->isNull()) {
      const auto &memoryObject = memoryValue->asObject();
      rejectUnknownFields(memoryObject,
                          {"base_object_id", "offset", "access_kind", "span",
                           "alias_group", "address_state_id", "update_mode",
                           "post_update_delta_bytes"},
                          "canonical memory_access");
      CanonicalMemoryAccess memory;
      memory.baseObjectId = required(memoryObject, "base_object_id").asString();
      const auto &offsetObject = required(memoryObject, "offset").asObject();
      rejectUnknownFields(offsetObject, {"constant", "terms"},
                          "canonical affine offset");
      memory.offset.constant = required(offsetObject, "constant").asInt();
      for (const auto &termValue : required(offsetObject, "terms").asArray()) {
        const auto &termObject = termValue.asObject();
        rejectUnknownFields(termObject, {"variable_id", "coefficient"},
                            "canonical affine term");
        memory.offset.terms.push_back(
            CanonicalAffineTerm{required(termObject, "variable_id").asString(),
                                required(termObject, "coefficient").asInt()});
      }
      const std::string access =
          required(memoryObject, "access_kind").asString();
      if (access == "read")
        memory.accessKind = CanonicalAccessKind::Read;
      else if (access == "write")
        memory.accessKind = CanonicalAccessKind::Write;
      else
        throw std::runtime_error("unsupported canonical access kind: " +
                                 access);
      memory.span = optionalInt(memoryObject, "span");
      memory.aliasGroup = optionalString(memoryObject, "alias_group");
      memory.addressStateId = optionalString(memoryObject, "address_state_id");
      if (const auto *mode = find(memoryObject, "update_mode"))
        memory.updateMode = mode->asString();
      if (const auto *delta = find(memoryObject, "post_update_delta_bytes")) {
        if (!delta->isNull()) {
          const auto &object = delta->asObject();
          rejectUnknownFields(object, {"constant", "terms"},
                              "canonical update delta");
          CanonicalAffineExpression expression;
          expression.constant = required(object, "constant").asInt();
          for (const auto &term : required(object, "terms").asArray()) {
            const auto &fields = term.asObject();
            rejectUnknownFields(fields, {"variable_id", "coefficient"},
                                "canonical affine term");
            expression.terms.push_back(
                {required(fields, "variable_id").asString(),
                 required(fields, "coefficient").asInt()});
          }
          memory.postUpdateDeltaBytes = std::move(expression);
        }
      }
      result.memoryAccess = std::move(memory);
    }
  }
  return result;
}

std::vector<CanonicalOperand> operands(const json::Value *value) {
  std::vector<CanonicalOperand> result;
  if (!value)
    return result;
  for (const auto &item : value->asArray())
    result.push_back(operand(item));
  return result;
}

CanonicalNode node(const json::Value &value) {
  const auto &object = value.asObject();
  const std::string kind = required(object, "kind").asString();
  if (kind == "instruction") {
    rejectUnknownFields(object,
                        {"kind", "instruction_id", "opcode",
                         "instruction_class", "form", "inputs", "outputs",
                         "dependencies", "attributes", "source_location"},
                        "canonical instruction");
    CanonicalInstruction result;
    result.instructionId = required(object, "instruction_id").asString();
    result.opcode = required(object, "opcode").asString();
    result.instructionClass =
        instructionClass(required(object, "instruction_class").asString());
    result.form = required(object, "form").asString();
    result.inputs = operands(find(object, "inputs"));
    result.outputs = operands(find(object, "outputs"));
    result.dependencies = dependencies(find(object, "dependencies"));
    result.attributes = scalarMap(find(object, "attributes"));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeInstruction(std::move(result));
  }
  if (kind == "loop") {
    rejectUnknownFields(object,
                        {"kind", "loop_id", "induction", "count", "unroll",
                         "carried_values", "body", "source_location"},
                        "canonical loop");
    CanonicalLoop result;
    result.loopId = required(object, "loop_id").asString();
    const auto &inductionObject = required(object, "induction").asObject();
    rejectUnknownFields(inductionObject, {"variable_id", "start", "step"},
                        "canonical induction");
    result.induction.variableId =
        required(inductionObject, "variable_id").asString();
    result.induction.start =
        integerExpression(required(inductionObject, "start"));
    result.induction.step =
        integerExpression(required(inductionObject, "step"));
    result.count = integerExpression(required(object, "count"));
    result.unroll = integerExpression(required(object, "unroll"));
    if (const json::Value *carriedValues = find(object, "carried_values")) {
      for (const auto &item : carriedValues->asArray()) {
        const auto &carried = item.asObject();
        rejectUnknownFields(carried,
                            {"logical_id", "entry_value_id",
                             "back_edge_value_id", "exit_value_id"},
                            "canonical loop carried value");
        result.carriedValues.push_back(CanonicalLoopCarriedValue{
            required(carried, "logical_id").asString(),
            required(carried, "entry_value_id").isNull()
                ? std::nullopt
                : std::optional<std::string>{required(carried, "entry_value_id")
                                                 .asString()},
            required(carried, "back_edge_value_id").asString(),
            required(carried, "exit_value_id").asString()});
      }
    }
    for (const auto &item : required(object, "body").asArray())
      result.body.push_back(node(item));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeLoop(std::move(result));
  }
  if (kind == "membar") {
    rejectUnknownFields(object,
                        {"kind", "instruction_id", "barrier", "dependencies",
                         "source_location"},
                        "canonical membar");
    CanonicalMembar result;
    result.instructionId = required(object, "instruction_id").asString();
    result.barrier = required(object, "barrier").asString();
    result.dependencies = dependencies(find(object, "dependencies"));
    result.sourceLocation = sourceLocation(find(object, "source_location"));
    return CanonicalNode::makeMembar(std::move(result));
  }
  throw std::runtime_error("unsupported canonical node kind: " + kind);
}

} // namespace

CanonicalVfInfo decodeCanonicalVfInfoJson(const json::Value &rootValue) {
  const auto &root = rootValue.asObject();
  rejectUnknownFields(root,
                      {"schema_version", "context", "values", "storage_objects",
                       "params", "uarch", "source"},
                      "CanonicalVfInfo root");
  CanonicalVfInfo result;
  result.schemaVersion = required(root, "schema_version").asInt();
  for (const auto &item : required(root, "context").asArray())
    result.context.push_back(node(item));
  for (const auto &[definitionId, value] :
       required(root, "values").asObject()) {
    const auto &object = value.asObject();
    rejectUnknownFields(object,
                        {"definition_id", "logical_id", "storage", "dtype",
                         "shape", "producer_node_id", "storage_object_id",
                         "source_location"},
                        "canonical value");
    CanonicalValue decoded;
    decoded.definitionId = required(object, "definition_id").asString();
    decoded.logicalId = required(object, "logical_id").asString();
    decoded.storage = storageKind(required(object, "storage").asString());
    decoded.dtype = required(object, "dtype").asString();
    decoded.shape = shape(find(object, "shape"));
    decoded.producerNodeId = optionalString(object, "producer_node_id");
    decoded.storageObjectId = optionalString(object, "storage_object_id");
    decoded.sourceLocation = sourceLocation(find(object, "source_location"));
    result.values.emplace(definitionId, std::move(decoded));
  }
  if (const json::Value *storageObjects = find(root, "storage_objects")) {
    for (const auto &[objectId, value] : storageObjects->asObject()) {
      const auto &object = value.asObject();
      rejectUnknownFields(object,
                          {"object_id", "storage", "shape", "source_location"},
                          "canonical storage object");
      CanonicalStorageObject decoded;
      decoded.objectId = required(object, "object_id").asString();
      decoded.storage = storageKind(required(object, "storage").asString());
      decoded.shape = shape(find(object, "shape"));
      decoded.sourceLocation = sourceLocation(find(object, "source_location"));
      result.storageObjects.emplace(objectId, std::move(decoded));
    }
  }
  if (const json::Value *params = find(root, "params"))
    for (const auto &[key, value] : params->asObject())
      result.params.emplace(key, value.asInt());
  result.uarch = scalarMap(find(root, "uarch"));
  result.source = scalarMap(find(root, "source"));
  return result;
}

} // namespace vfsim

namespace vfsim {
namespace {
using Array = json::Value::Array;
using JsonObject = json::Value::Object;
template <class T> json::Value encodeOptional(const std::optional<T> &value) {
  return value ? json::Value(*value) : json::Value{};
}
json::Value encodeScalar(const CanonicalScalar &value) {
  return std::visit(
      [](const auto &item) -> json::Value {
        using T = std::decay_t<decltype(item)>;
        if constexpr (std::is_same_v<T, std::monostate>)
          return {};
        else
          return item;
      },
      value);
}
json::Value encodeInteger(const CanonicalIntegerExpression &value) {
  return std::visit([](const auto &item) -> json::Value { return item; },
                    value);
}
JsonObject encodeScalars(const std::map<std::string, CanonicalScalar> &items) {
  JsonObject result;
  for (const auto &[key, value] : items)
    result[key] = encodeScalar(value);
  return result;
}
Array encodeShape(const std::vector<int64_t> &shape) {
  Array result;
  for (auto dimension : shape)
    result.emplace_back(dimension);
  return result;
}
json::Value
encodeLocation(const std::optional<CanonicalSourceLocation> &value) {
  if (!value)
    return {};
  return JsonObject{{"source", encodeOptional(value->source)},
                    {"line", encodeOptional(value->line)},
                    {"column", encodeOptional(value->column)},
                    {"path", encodeOptional(value->path)}};
}
const char *storageName(CanonicalStorageKind kind) {
  switch (kind) {
  case CanonicalStorageKind::Register:
    return "Register";
  case CanonicalStorageKind::PredicateRegister:
    return "PredicateRegister";
  case CanonicalStorageKind::UB:
    return "UB";
  case CanonicalStorageKind::Scalar:
    return "Scalar";
  default:
    throw std::runtime_error("Cannot encode unknown Canonical storage kind");
  }
}
const char *roleName(CanonicalOperandRole role) {
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
  default:
    throw std::runtime_error("Cannot encode unknown Canonical operand role");
  }
}
json::Value encodeAffine(const CanonicalAffineExpression &value) {
  Array terms;
  for (const auto &term : value.terms)
    terms.emplace_back(JsonObject{{"variable_id", term.variableId},
                                  {"coefficient", term.coefficient}});
  return JsonObject{{"constant", value.constant}, {"terms", terms}};
}
Array encodeOperands(const std::vector<CanonicalOperand> &operands) {
  Array result;
  for (const auto &operand : operands) {
    json::Value memory;
    if (operand.memoryAccess) {
      const auto &m = *operand.memoryAccess;
      if (m.accessKind == CanonicalAccessKind::Unknown)
        throw std::runtime_error("Cannot encode unknown access kind");
      memory = JsonObject{
          {"base_object_id", m.baseObjectId},
          {"offset", encodeAffine(m.offset)},
          {"access_kind",
           m.accessKind == CanonicalAccessKind::Read ? "read" : "write"},
          {"span", encodeOptional(m.span)},
          {"alias_group", encodeOptional(m.aliasGroup)},
          {"address_state_id", encodeOptional(m.addressStateId)},
          {"update_mode", m.updateMode},
          {"post_update_delta_bytes",
           m.postUpdateDeltaBytes ? encodeAffine(*m.postUpdateDeltaBytes)
                                  : json::Value{}}};
    }
    result.emplace_back(JsonObject{{"value_id", operand.valueId},
                                   {"role", roleName(operand.role)},
                                   {"dtype", encodeOptional(operand.dtype)},
                                   {"memory_access", memory}});
  }
  return result;
}
Array encodeDependencies(
    const std::vector<CanonicalDependencyRef> &dependencies) {
  Array result;
  for (const auto &dependency : dependencies) {
    if (dependency.kind == CanonicalDependencyKind::Unknown)
      throw std::runtime_error("Cannot encode unknown dependency kind");
    result.emplace_back(
        JsonObject{{"producer_node_id", dependency.producerNodeId},
                   {"kind", dependency.kind == CanonicalDependencyKind::Memory
                                ? "memory"
                                : "control"},
                   {"operand_index", encodeOptional(dependency.operandIndex)}});
  }
  return result;
}
Array encodeNodes(const std::vector<CanonicalNode> &nodes) {
  Array result;
  for (const auto &node : nodes) {
    if (const auto *i = std::get_if<CanonicalInstruction>(&node.payload)) {
      const char *cls =
          i->instructionClass == CanonicalInstructionClass::Load    ? "load"
          : i->instructionClass == CanonicalInstructionClass::Store ? "store"
          : i->instructionClass == CanonicalInstructionClass::Compute
              ? "compute"
              : "control";
      if (i->instructionClass == CanonicalInstructionClass::Unknown)
        throw std::runtime_error("Cannot encode unknown instruction class");
      result.emplace_back(
          JsonObject{{"kind", "instruction"},
                     {"instruction_id", i->instructionId},
                     {"opcode", i->opcode},
                     {"instruction_class", cls},
                     {"form", i->form},
                     {"inputs", encodeOperands(i->inputs)},
                     {"outputs", encodeOperands(i->outputs)},
                     {"dependencies", encodeDependencies(i->dependencies)},
                     {"attributes", encodeScalars(i->attributes)},
                     {"source_location", encodeLocation(i->sourceLocation)}});
    } else if (const auto *m = std::get_if<CanonicalMembar>(&node.payload)) {
      result.emplace_back(
          JsonObject{{"kind", "membar"},
                     {"instruction_id", m->instructionId},
                     {"barrier", m->barrier},
                     {"dependencies", encodeDependencies(m->dependencies)},
                     {"source_location", encodeLocation(m->sourceLocation)}});
    } else {
      const auto &pointer =
          std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
      if (!pointer)
        throw std::runtime_error("Cannot encode null Canonical loop");
      const auto &loop = *pointer;
      Array carried;
      for (const auto &item : loop.carriedValues)
        carried.emplace_back(
            JsonObject{{"logical_id", item.logicalId},
                       {"entry_value_id", encodeOptional(item.entryValueId)},
                       {"back_edge_value_id", item.backEdgeValueId},
                       {"exit_value_id", item.exitValueId}});
      result.emplace_back(
          JsonObject{{"kind", "loop"},
                     {"loop_id", loop.loopId},
                     {"induction",
                      JsonObject{{"variable_id", loop.induction.variableId},
                                 {"start", encodeInteger(loop.induction.start)},
                                 {"step", encodeInteger(loop.induction.step)}}},
                     {"count", encodeInteger(loop.count)},
                     {"unroll", encodeInteger(loop.unroll)},
                     {"carried_values", carried},
                     {"body", encodeNodes(loop.body)},
                     {"source_location", encodeLocation(loop.sourceLocation)}});
    }
  }
  return result;
}
} // namespace
json::Value encodeCanonicalVfInfoJson(const CanonicalVfInfo &vf) {
  JsonObject values, objects, params;
  for (const auto &[id, value] : vf.values)
    values[id] =
        JsonObject{{"definition_id", value.definitionId},
                   {"logical_id", value.logicalId},
                   {"storage", storageName(value.storage)},
                   {"dtype", value.dtype},
                   {"shape", encodeShape(value.shape)},
                   {"producer_node_id", encodeOptional(value.producerNodeId)},
                   {"storage_object_id", encodeOptional(value.storageObjectId)},
                   {"source_location", encodeLocation(value.sourceLocation)}};
  for (const auto &[id, object] : vf.storageObjects)
    objects[id] =
        JsonObject{{"object_id", object.objectId},
                   {"storage", storageName(object.storage)},
                   {"shape", encodeShape(object.shape)},
                   {"source_location", encodeLocation(object.sourceLocation)}};
  for (const auto &[key, value] : vf.params)
    params[key] = value;
  return JsonObject{{"schema_version", vf.schemaVersion},
                    {"context", encodeNodes(vf.context)},
                    {"values", values},
                    {"storage_objects", objects},
                    {"params", params},
                    {"uarch", encodeScalars(vf.uarch)},
                    {"source", encodeScalars(vf.source)}};
}
} // namespace vfsim
