// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/params/loader.h"
#include "native/api/json/value.h"
#include "native/core/params/resources.h"
#include "native/core/params/fallback.h"
#include "native/core/params/uarch.h"
#include <algorithm>
#include <cstdlib>
#include <cctype>
#include <stdexcept>
namespace vfsim {
namespace {

using JsonValue = json::Value;

const JsonValue *findKey(const JsonValue::Object &object,
                         const std::string &key) {
  auto it = object.find(key);
  if (it == object.end())
    return nullptr;
  return &it->second;
}

std::string readStringField(const JsonValue::Object &object, const char *key,
                            const std::string &defaultValue = {}) {
  const JsonValue *value = findKey(object, key);
  if (!value)
    return defaultValue;
  if (!value->isString())
    throw std::runtime_error(std::string(key) + " must be a string");
  return value->asString();
}

int64_t readIntField(const JsonValue::Object &object, const char *key,
                     int64_t defaultValue = 0) {
  const JsonValue *value = findKey(object, key);
  if (!value)
    return defaultValue;
  if (!value->isInt() || value->asInt() < 0)
    throw std::runtime_error(std::string(key) +
                             " must be a nonnegative integer");
  return value->asInt();
}

std::pair<std::string, std::string> splitQualifiedOp(const std::string &name) {
  const std::size_t dot = name.rfind('.');
  if (dot == std::string::npos)
    return {name, ""};
  return {name.substr(0, dot), name.substr(dot + 1)};
}

std::string qualifyOp(const std::string &op, const std::string &form) {
  return form.empty() ? op : op + "." + form;
}

InstructionProfile readInstructionProfile(const JsonValue::Object &object) {
  InstructionProfile cfg;
  cfg.latency = readIntField(object, "latency");
  if (object.count("align_state_latency")) {
    cfg.alignStateLatency = readIntField(object, "align_state_latency");
    if (cfg.alignStateLatency < 0)
      throw std::runtime_error("align_state_latency must be nonnegative");
  }
  cfg.lsuResource = readStringField(object, "lsu_resource");
  if (object.count("lsu_issue_interval")) {
    cfg.lsuIssueInterval = readIntField(object, "lsu_issue_interval");
    if (cfg.lsuIssueInterval < 1)
      throw std::runtime_error("lsu_issue_interval must be positive");
  }
  cfg.fuType = readStringField(object, "EXU");
  cfg.dispatchExu = readStringField(object, "dispatch_exu");
  cfg.opClass = readStringField(object, "op_class");
  cfg.executionResource = readStringField(object, "execution_resource");
  return cfg;
}

std::unordered_map<std::string, JsonValue>
loadObjectFile(const std::filesystem::path &path) {
  JsonValue value = json::parseFile(path);
  if (!value.isObject())
    throw std::runtime_error("JSON root must be an object: " + path.string());
  return value.asObject();
}

JsonValue::Object mergeObjects(JsonValue::Object base,
                               const JsonValue::Object &overlay) {
  for (const auto &[key, value] : overlay)
    base[key] = value;
  return base;
}

std::optional<std::filesystem::path>
resolvePath(const std::filesystem::path &baseDir, const char *envKey,
            std::initializer_list<std::filesystem::path> candidates,
            bool required = true) {
  if (const char *envPath = std::getenv(envKey); envPath && *envPath) {
    const std::filesystem::path path(envPath);
    if (!std::filesystem::is_regular_file(path))
      throw std::runtime_error(std::string(envKey) +
                               ": explicit path not found: " + path.string());
    return std::filesystem::absolute(path);
  }
  for (const auto &candidate : candidates)
    if (std::filesystem::is_regular_file(baseDir / candidate))
      return std::filesystem::absolute(baseDir / candidate);
  if (!required)
    return std::nullopt;
  throw std::runtime_error(std::string("Could not locate ") + envKey +
                           " under " + baseDir.string());
}

} // namespace
ParamBundle loadParameters(const std::filesystem::path &baseDir) {
  ParamBundle bundle;

  const std::filesystem::path isaPath =
      *resolvePath(baseDir, "ISA_JSON_PATH", {"configs/isa.json", "isa.json"});
  const std::filesystem::path uarchPath = *resolvePath(
      baseDir, "UARCH_JSON_PATH", {"configs/uarch.json", "uarch.json"});
  const std::optional<std::filesystem::path> forwardingPath =
      resolvePath(baseDir, "FORWARDING_JSON_PATH",
                  {"configs/forwarding.json", "forwarding.json"}, false);
  const std::optional<std::filesystem::path> iiPath = resolvePath(
      baseDir, "II_JSON_PATH",
      {"configs/InitiationInterval.json", "Initiation_Interval.json"}, false);
  const auto resourcePath = resolvePath(isaPath.parent_path(), "EXECUTION_RESOURCES_JSON_PATH",
      {"execution_resources.json"}, false);
  const auto overridePath = resolvePath(isaPath.parent_path(), "TIMING_OVERRIDES_JSON_PATH",
      {"timing_overrides.json"}, false);

  const auto isaRoot = loadObjectFile(isaPath);
  const auto uarchRoot = loadObjectFile(uarchPath);
  const auto version = readIntField(isaRoot, "schema_version", 1);
  if (version != 1 && version != 2)
    throw std::runtime_error("Unsupported ISA schema_version");
  const auto fwdRoot = forwardingPath
                           ? loadObjectFile(*forwardingPath)
                           : std::unordered_map<std::string, JsonValue>{};
  const auto iiRoot = iiPath ? loadObjectFile(*iiPath)
                             : std::unordered_map<std::string, JsonValue>{};
  const auto resourceRoot = loadObjectFile(resourcePath.value_or(
      std::filesystem::path(VFSIM_SOURCE_ROOT) / "configs/execution_resources.json"));
  const auto overrideRoot = overridePath ? loadObjectFile(*overridePath) : JsonValue::Object{};
  {
    if (readIntField(resourceRoot, "schema_version") != 1)
      throw std::runtime_error("execution_resources requires schema_version=1");
    const auto *groups = findKey(resourceRoot, "groups");
    if (!groups || !groups->isArray())
      throw std::runtime_error("execution_resources.groups must be an array");
    for (const auto &value : groups->asArray()) {
      if (!value.isString() || value.asString().empty())
        throw std::runtime_error("Resource names must be nonempty strings");
      const auto &name = value.asString();
      if (std::any_of(name.begin(), name.end(), [](unsigned char c) { return std::islower(c); }) ||
          std::find(bundle.executionResources.begin(), bundle.executionResources.end(), name) != bundle.executionResources.end())
        throw std::runtime_error("Resource names must be unique uppercase strings");
      bundle.executionResources.push_back(name);
    }
    const auto *forwarding = findKey(resourceRoot, "forwarding");
    if (!forwarding || !forwarding->isObject())
      throw std::runtime_error("execution_resources.forwarding must be an object");
    const auto &policy = forwarding->asObject();
    for (const auto *field : {"minimum", "default_offset", "pair_offsets", "compute_store_offset",
                             "fallback_offset", "load_store_interval"})
      if (!findKey(policy, field))
        throw std::runtime_error("Missing resource forwarding field");
    bundle.resourceForwardingMinimum = readIntField(policy, "minimum");
    bundle.resourceForwardingDefaultOffset = readIntField(policy, "default_offset");
    bundle.computeStoreForwardingOffset = readIntField(policy, "compute_store_offset");
    bundle.forwardingDefaultOffset = readIntField(policy, "fallback_offset");
    bundle.loadStoreForwardingInterval = readIntField(policy, "load_store_interval");
    const auto *iiPolicy = findKey(resourceRoot, "ii");
    if (!iiPolicy || !iiPolicy->isObject())
      throw std::runtime_error("execution_resources.ii must be an object");
    for (const auto *field : {"different_resource_interval", "writeback_collision_penalty"})
      if (!findKey(iiPolicy->asObject(), field))
        throw std::runtime_error("Missing resource II field");
    bundle.crossResourceIssueInterval = readIntField(iiPolicy->asObject(), "different_resource_interval");
    bundle.writebackCollisionPenalty = readIntField(iiPolicy->asObject(), "writeback_collision_penalty");
    if (bundle.crossResourceIssueInterval < 1)
      throw std::runtime_error("different_resource_interval must be positive");
    const auto &offsets = policy.at("pair_offsets");
    if (!offsets.isObject())
      throw std::runtime_error("Resource forwarding offsets must be an object");
    auto known = [&](const std::string &name) {
      return std::find(bundle.executionResources.begin(), bundle.executionResources.end(), name) != bundle.executionResources.end();
    };
    for (const auto &[a, row] : offsets.asObject()) {
      if (!known(a) || !row.isObject())
        throw std::runtime_error("Unknown forwarding resource: " + a);
      for (const auto &[b, value] : row.asObject()) {
        if (!known(b) || !value.isInt() || value.asInt() < 0)
          throw std::runtime_error("Invalid resource forwarding offset");
        bundle.resourceForwardingOffsets[a][b] = value.asInt();
      }
    }
  }

  const auto *defaultNode = findKey(isaRoot, "defaults");
  if (const JsonValue *defaults = defaultNode) {
    if (!defaults->isObject())
      throw std::runtime_error("isa.json.defaults must be an object");
    const auto &obj = defaults->asObject();
    bundle.isaDefaults.vfStartupCost = readIntField(obj, "vf_startup_cost");
    bundle.isaDefaults.vfDrainCost = readIntField(obj, "vf_drain_cost");
  }

  const JsonValue::Object defaults =
      defaultNode ? defaultNode->asObject() : JsonValue::Object{};
  if (const JsonValue *instructions = findKey(isaRoot, "instructions")) {
    if (!instructions->isObject())
      throw std::runtime_error("isa.json.instructions must be an object");
    for (const auto &[opName, opValue] : instructions->asObject()) {
      if (!opValue.isObject())
        throw std::runtime_error("isa.json.instructions." + opName +
                                 " must be an object");
      auto &dtypeMap = bundle.isa[opName];
      const auto &opObject = opValue.asObject();
      if (const auto *modes = findKey(opObject, "memory_mode_latency")) {
        if (!modes->isObject())
          throw std::runtime_error("memory_mode_latency must be an object");
        for (const auto &[mode, latency] : modes->asObject()) {
          if (!latency.isInt() || latency.asInt() < 0)
            throw std::runtime_error(
                "memory_mode_latency must contain nonnegative integers");
          bundle.memoryModeLatency[opName][mode] = latency.asInt();
        }
      }
      const std::string inheritedOpClass =
          readStringField(opObject, "op_class");
      if (const JsonValue *forms = findKey(opObject, "forms")) {
        if (!forms->isObject())
          throw std::runtime_error("isa.json.instructions." + opName +
                                   ".forms must be an object");
        const auto &formObjects = forms->asObject();
        for (const auto &[formName, formValue] : formObjects) {
          if (!formValue.isObject())
            throw std::runtime_error("isa.json.instructions." + opName +
                                     ".forms." + formName +
                                     " must be an object");
          JsonValue::Object opDefaults = opObject;
          opDefaults.erase("forms");
          JsonValue::Object effectiveForm = mergeObjects(
              mergeObjects(defaults, opDefaults), formValue.asObject());
          const std::string compatible =
              param_fallback::compatibleForm(formName);
          if (!compatible.empty()) {
            const auto compatibleIt = formObjects.find(compatible);
            if (compatibleIt != formObjects.end()) {
              if (!compatibleIt->second.isObject())
                throw std::runtime_error("isa.json.instructions." + opName +
                                         ".forms." + compatible +
                                         " must be an object");
              effectiveForm = mergeObjects(
                  mergeObjects(effectiveForm, compatibleIt->second.asObject()),
                  formValue.asObject());
            }
          }
          InstructionProfile config = readInstructionProfile(effectiveForm);
          config.resolvedForm = formName;
          if (config.opClass.empty())
            config.opClass = inheritedOpClass;
          dtypeMap.emplace(formName, std::move(config));
        }
        continue;
      }

      for (const auto &[dtypeName, dtypeValue] : opObject) {
        if (!dtypeValue.isObject())
          continue;
        InstructionProfile config = readInstructionProfile(
            mergeObjects(defaults, dtypeValue.asObject()));
        config.resolvedForm = dtypeName;
        if (config.opClass.empty())
          config.opClass = inheritedOpClass;
        dtypeMap.emplace(dtypeName, std::move(config));
      }
    }
  }

  bundle.uarch = parseUarch(uarchRoot);
  bundle.forwardingDefaultOffset =
      readIntField(fwdRoot, "default", readIntField(fwdRoot, "defaults", bundle.forwardingDefaultOffset));
  bundle.initiationIntervalDefault = readIntField(iiRoot, "defaults", bundle.crossResourceIssueInterval);
  auto readPairs = [&](const JsonValue::Object &root, const char *key,
                       int64_t minimum, auto &table) {
    const auto *node = findKey(root, key);
    if (!node)
      return;
    if (!node->isObject())
      throw std::runtime_error(std::string(key) + " must be an object");
    auto add = [&](const std::string &producer, const std::string &consumer,
                   const JsonValue &value) {
      if (!value.isInt() || value.asInt() < minimum)
        throw std::runtime_error(std::string(key) + "." + producer + "." +
                                 consumer + " has invalid timing");
      table[producer][consumer] = value.asInt();
    };
    for (const auto &[outer, value] : node->asObject()) {
      if (!value.isObject())
        throw std::runtime_error(std::string(key) + "." + outer +
                                 " must be an object");
      auto [op, form] = splitQualifiedOp(outer);
      if (!form.empty())
        for (const auto &[consumer, timing] : value.asObject())
          add(outer, consumer, timing);
      else
        for (const auto &[producer, consumers] : value.asObject()) {
          if (!consumers.isObject())
            throw std::runtime_error(std::string(key) +
                                     " nested pair table must be an object");
          for (const auto &[consumer, timing] : consumers.asObject())
            add(qualifyOp(producer, outer), qualifyOp(consumer, outer), timing);
        }
    }
  };
  if (auto value = findKey(iiRoot, "defaults");
      value && (!value->isInt() || value->asInt() < 1))
    throw std::runtime_error("II.defaults must be a positive integer");
  readPairs(fwdRoot, "forwarding", 0, bundle.forwardingByForm);
  readPairs(iiRoot, "InitiationInterval", 1, bundle.initiationIntervalByForm);
  if (overridePath) {
    if (readIntField(overrideRoot, "schema_version") != 1 || !findKey(overrideRoot, "ii") ||
        !findKey(overrideRoot, "forwarding"))
      throw std::runtime_error("timing_overrides requires schema_version=1 and ii/forwarding tables");
    for (bool initiation : {false, true}) {
      const auto *key = initiation ? "ii" : "forwarding";
      const auto &values = overrideRoot.at(key);
      if (!values.isObject())
        throw std::runtime_error(std::string("timing_overrides.") + key + " must be an object");
      auto &table = initiation ? bundle.iiOverrides : bundle.forwardingOverrides;
      for (const auto &[producer, consumers] : values.asObject()) {
        if (producer.find('.') == std::string::npos || !consumers.isObject())
          throw std::runtime_error("Timing overrides require qualified producer rows");
        auto &row = table[producer];
        for (const auto &[consumer, value] : consumers.asObject()) {
          if (!value.isInt() || value.asInt() < (initiation ? 1 : 0))
            throw std::runtime_error("Invalid timing override: " + producer + " -> " + consumer);
          row[consumer] = value.asInt();
        }
      }
    }
  }
  for (auto &[op, forms] : bundle.isa) {
    for (auto &[form, profile] : forms) {
      for (const auto &candidate : param_fallback::pairKeyCandidates(op, form, op, form)) {
        const auto row = bundle.initiationIntervalByForm.find(candidate.producer);
        if (row != bundle.initiationIntervalByForm.end()) {
          const auto value = row->second.find(candidate.consumer);
          if (value != row->second.end()) { profile.selfIi = value->second; break; }
        }
      }
      profile.writebackBanks = instructionBanks(op);
    }
  }
  validateResources(bundle);
  return bundle;
}
} // namespace vfsim
