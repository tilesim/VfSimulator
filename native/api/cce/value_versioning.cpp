// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "native/api/cce/value_versioning.h"
#include "native/api/cce/affine.h"
#include "native/api/common/instruction_catalog.h"
#include <algorithm>
#include <functional>
#include <regex>
#include <stdexcept>

namespace vfsim::cce {
namespace {
using Environment = std::map<std::string, std::optional<std::string>>;
std::string safeId(const std::string &s) {
  auto text = std::regex_replace(s, std::regex(R"([^A-Za-z0-9_.-]+)"), "_");
  auto start = text.find_first_not_of('_');
  if (start == std::string::npos)
    return "value";
  return text.substr(start, text.find_last_not_of('_') - start + 1);
}
CanonicalOperandRole role(const std::string &s) {
  if (s == "source")
    return CanonicalOperandRole::Source;
  if (s == "destination")
    return CanonicalOperandRole::Destination;
  if (s == "memory")
    return CanonicalOperandRole::Memory;
  if (s == "predicate")
    return CanonicalOperandRole::Predicate;
  if (s == "scalar")
    return CanonicalOperandRole::Scalar;
  if (s == "config")
    return CanonicalOperandRole::Config;
  return CanonicalOperandRole::Unknown;
}
class Versioner {
  const AdapterProgram &program_;
  CanonicalVfInfo vf_;
  std::set<std::string> nodeIds_;
  int64_t instruction_ = 0, loop_ = 0, membar_ = 0, definition_ = 0;
  std::string nodeId(const std::string &kind,
                     const std::string &preferred = {}) {
    if (!preferred.empty()) {
      if (!nodeIds_.insert(preferred).second)
        throw std::runtime_error("Duplicate node ID during value versioning: " +
                                 preferred);
      return preferred;
    }
    auto &counter = kind == "instruction" ? instruction_
                    : kind == "loop"      ? loop_
                                          : membar_;
    while (true) {
      auto id = kind + "." + std::to_string(counter++);
      if (nodeIds_.insert(id).second)
        return id;
    }
  }
  std::string definition(const std::string &logical,
                         const std::optional<std::string> &producer) {
    const auto &value = program_.values.at(logical);
    auto id = safeId(logical) + ".def" + std::to_string(definition_++);
    CanonicalValue v;
    v.definitionId = id;
    v.logicalId = logical;
    v.storage = value.storage;
    v.dtype = value.dtype.empty() ? "fp32" : value.dtype;
    v.shape = value.shape;
    v.producerNodeId = producer;
    if (v.storage == CanonicalStorageKind::UB) {
      v.storageObjectId = "ub." + safeId(logical);
      CanonicalStorageObject object;
      object.objectId = *v.storageObjectId;
      object.storage = v.storage;
      object.shape = v.shape;
      vf_.storageObjects.emplace(object.objectId, std::move(object));
    }
    vf_.values.emplace(id, std::move(v));
    return id;
  }
  std::string entry(const std::string &logical, Environment &env) {
    auto it = env.find(logical);
    if (it != env.end() && !it->second)
      throw std::runtime_error("Value " + logical +
                               " is undefined after a possibly empty loop");
    if (it == env.end())
      return *(env[logical] = definition(logical, std::nullopt));
    return *it->second;
  }
  std::set<std::string> written(const std::vector<AdapterNode> &nodes) const {
    std::set<std::string> result;
    for (const auto &n : nodes) {
      if (n.kind == AdapterNode::Kind::Loop) {
        auto inner = written(n.body);
        result.insert(inner.begin(), inner.end());
      } else
        for (const auto &v : n.dst)
          if (renameable(program_.values.at(v.valueId).storage))
            result.insert(v.valueId);
    }
    return result;
  }
  std::set<std::string>
  firstReads(const std::vector<AdapterNode> &nodes) const {
    std::set<std::string> defined, reads;
    for (const auto &n : nodes) {
      if (n.kind == AdapterNode::Kind::Loop) {
        auto inner = firstReads(n.body);
        for (const auto &id : inner)
          if (!defined.count(id))
            reads.insert(id);
        if (n.count > 0) {
          auto ids = written(n.body);
          defined.insert(ids.begin(), ids.end());
        }
        continue;
      }
      for (const auto &v : n.src)
        if (!defined.count(v.valueId) &&
            renameable(program_.values.at(v.valueId).storage))
          reads.insert(v.valueId);
      for (const auto &v : n.dst)
        defined.insert(v.valueId);
    }
    return reads;
  }
  CanonicalOperand operand(const std::string &id, bool output,
                           const std::string &catalogRole,
                           const AdapterMemoryAccess *access,
                           const std::set<std::string> &induction) {
    const auto &value = vf_.values.at(id);
    CanonicalOperand o;
    o.valueId = id;
    o.dtype = value.dtype;
    o.role = role(catalogRole);
    if (value.storage == CanonicalStorageKind::UB) {
      if (o.role == CanonicalOperandRole::Unknown)
        o.role = CanonicalOperandRole::Memory;
      CanonicalMemoryAccess memory;
      memory.baseObjectId = *value.storageObjectId;
      memory.accessKind = access   ? access->accessKind
                          : output ? CanonicalAccessKind::Write
                                   : CanonicalAccessKind::Read;
      memory.offset = parseAffine(access ? access->offset : "0",
                                  program_.params, induction);
      if (access) {
        memory.span = access->span;
        memory.addressUnitBytes = access->addressUnitBytes;
        memory.addressStateId = access->addressStateId;
        memory.updateMode = access->updateMode;
        if (access->postUpdateDeltaBytes)
          memory.postUpdateDeltaBytes = parseAffine(
              *access->postUpdateDeltaBytes, program_.params, induction);
      }
      o.memoryAccess = std::move(memory);
    } else if (o.role == CanonicalOperandRole::Unknown)
      o.role = output ? CanonicalOperandRole::Destination
               : value.storage == CanonicalStorageKind::Scalar
                   ? CanonicalOperandRole::Scalar
                   : CanonicalOperandRole::Source;
    return o;
  }
  CanonicalInstruction instruction(const AdapterNode &n, Environment &env,
                                   const std::set<std::string> &induction) {
    CanonicalInstruction out;
    out.instructionId = nodeId("instruction");
    out.opcode = n.name;
    out.form = n.form.empty() ? "fp32" : n.form;
    out.instructionClass = n.instructionClass;
    out.attributes = n.attributes;
    out.sourceLocation = n.sourceLocation;
    const auto &catalog = defaultInstructionCatalog();
    auto spec = catalog.lookup(n.name);
    if (auto mode = n.attributes.find("catalog_mode");
        mode != n.attributes.end())
      spec =
          catalog.lookupMemoryMode(n.name, std::get<std::string>(mode->second));
    std::vector<std::string> inputRoles, outputRoles;
    if (spec)
      for (const auto &o : spec->operands) {
        if (o.direction == CatalogOperandDirection::Input ||
            o.direction == CatalogOperandDirection::ReadWrite)
          inputRoles.push_back(o.direction == CatalogOperandDirection::ReadWrite
                                   ? "source"
                                   : o.role);
        if (o.direction == CatalogOperandDirection::Output ||
            o.direction == CatalogOperandDirection::ReadWrite)
          outputRoles.push_back(o.role);
      }
    auto accessFor =
        [&](const std::string &logical) -> const AdapterMemoryAccess * {
      const AdapterMemoryAccess *result = nullptr;
      for (const auto &a : n.memoryAccesses)
        if (a.valueId == logical)
          result = &a;
      return result;
    };
    for (size_t i = 0; i < n.src.size(); ++i) {
      auto logical = n.src[i].valueId;
      auto id = entry(logical, env);
      out.inputs.push_back(operand(id, false,
                                   i < inputRoles.size() ? inputRoles[i] : "",
                                   accessFor(logical), induction));
    }
    for (size_t i = 0; i < n.dst.size(); ++i) {
      auto logical = n.dst[i].valueId;
      auto id = definition(logical, out.instructionId);
      out.outputs.push_back(
          operand(id, true, i < outputRoles.size() ? outputRoles[i] : "",
                  accessFor(logical), induction));
      if (program_.values.at(logical).storage != CanonicalStorageKind::UB)
        env[logical] = id;
    }
    return out;
  }
  CanonicalLoop loop(const AdapterNode &n, Environment &env,
                     const std::set<std::string> &induction) {
    CanonicalLoop out;
    out.loopId = nodeId("loop", n.loopId);
    out.induction.variableId = n.inductionVariable.empty()
                                   ? "iter_" + safeId(out.loopId)
                                   : n.inductionVariable;
    out.induction.start = n.inductionStart;
    out.induction.step = n.inductionStep;
    out.count = n.count;
    out.unroll = n.unroll;
    out.sourceLocation = n.sourceLocation;
    auto ids = written(n.body), reads = firstReads(n.body);
    auto innerEnv = env;
    Environment entries;
    for (const auto &id : ids)
      entries[id] = ((env.count(id) && env.at(id)) || reads.count(id))
                        ? std::optional<std::string>{entry(id, innerEnv)}
                        : std::nullopt;
    auto innerInduction = induction;
    innerInduction.insert(out.induction.variableId);
    out.body = nodes(n.body, innerEnv, innerInduction);
    std::map<std::string, std::string> exitByBack;
    for (const auto &id : ids) {
      auto back = innerEnv.at(id);
      if (!back || (!entries[id] && n.count <= 0)) {
        env[id] = std::nullopt;
        continue;
      }
      auto exit = definition(id, out.loopId);
      exitByBack[*back] = exit;
      out.carriedValues.push_back({id, entries[id], *back, exit});
      env[id] = exit;
    }
    if (n.count > 0)
      for (const auto &[id, def] : innerEnv)
        if (!ids.count(id)) {
          if (def && exitByBack.count(*def))
            env[id] = exitByBack[*def];
          else
            env[id] = def;
        }
    return out;
  }
  std::vector<CanonicalNode> nodes(const std::vector<AdapterNode> &input,
                                   Environment &env,
                                   const std::set<std::string> &induction) {
    std::vector<CanonicalNode> out;
    for (const auto &n : input) {
      if (n.kind == AdapterNode::Kind::Instruction)
        out.push_back(
            CanonicalNode::makeInstruction(instruction(n, env, induction)));
      else if (n.kind == AdapterNode::Kind::Loop)
        out.push_back(CanonicalNode::makeLoop(loop(n, env, induction)));
      else if (n.kind == AdapterNode::Kind::Membar) {
        CanonicalMembar m;
        m.instructionId = nodeId("membar");
        m.barrier = n.name;
        m.sourceLocation = n.sourceLocation;
        out.push_back(CanonicalNode::makeMembar(std::move(m)));
      } else {
        const auto &s = program_.values.at(n.src.at(0).valueId),
                   &d = program_.values.at(n.dst.at(0).valueId);
        auto signedness = [](std::string dtype) {
          if (dtype.compare(0, 4, "uint") == 0)
            dtype.erase(0, 1);
          return dtype;
        };
        const auto flag = n.attributes.find("register_bitcast");
        const bool bitcast = flag != n.attributes.end() &&
                             std::get_if<bool>(&flag->second) &&
                             std::get<bool>(flag->second) &&
                             s.storage == CanonicalStorageKind::Register &&
                             signedness(s.dtype) == signedness(d.dtype);
        if (!renameable(s.storage) || s.storage != d.storage ||
            (s.dtype != d.dtype && !bitcast) || s.shape != d.shape)
          throw std::runtime_error(
              "Register alias requires matching storage, dtype and shape");
        env[d.valueId] = entry(s.valueId, env);
      }
    }
    return out;
  }

public:
  Versioner(const AdapterProgram &p,
            const std::map<std::string, CanonicalScalar> &source)
      : program_(p) {
    vf_.params = p.params;
    vf_.uarch = p.uarch;
    vf_.source = source;
  }
  CanonicalVfInfo run() {
    Environment env;
    for (const auto &id : program_.liveInValues)
      entry(id, env);
    vf_.context = nodes(program_.context, env, {});
    vf_.schemaVersion = emissionSchemaVersion(vf_.values, vf_.context);
    return std::move(vf_);
  }
};
} // namespace
CanonicalVfInfo
versionValues(const AdapterProgram &p,
              const std::map<std::string, CanonicalScalar> &source) {
  return Versioner(p, source).run();
}
} // namespace vfsim::cce
