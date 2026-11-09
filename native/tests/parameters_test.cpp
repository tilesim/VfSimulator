// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/common/uarch_validation.h"
#include "native/core/params/loader.h"
#include "native/tests/support.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
#include <tuple>
using namespace vfsim;
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
void write(const std::filesystem::path &path, const char *text) {
  std::ofstream(path) << text;
}
std::string read(const std::filesystem::path &path) {
  std::ifstream stream(path);
  require(stream.good(), "Expected output file");
  return {std::istreambuf_iterator<char>(stream),
          std::istreambuf_iterator<char>()};
}

void testParameterReuse(const std::filesystem::path &root) {
  std::filesystem::create_directories(root / "configs");
  write(root / "configs/uarch.json", "{}");
  write(
      root / "configs/isa.json",
      R"({"schema_version":2,"defaults":{"vf_startup_cost":23,"vf_drain_cost":12},"instructions":{"VLDS":{"op_class":"LOAD","forms":{"fp32":{"latency":9}}}}})");
  write(root / "configs/forwarding.json", "{}");
  write(root / "configs/InitiationInterval.json", "{}");
  auto parameters = std::make_shared<const ParamBundle>(loadParameters(root));
  ParamDB first(parameters), second(parameters);
  require(&first.bundle() == &second.bundle(),
          "Runs share the parameter bundle");
  first.forwardingCycles("VLDS", "fp32", "VLDS", "fp32");
  require(!first.warnings().empty() && second.warnings().empty(),
          "Shared parameters keep warnings local");
  second.forwardingCycles("VLDS", "fp32", "VLDS", "fp32");
  require(!second.warnings().empty(), "Each run records its own cache misses");

  const auto vf =
      InputAPI::loadJson(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                         "VFtest/canonical/fixtures/v1_valid_loop.json");
  const auto output = root / "output";
  CoreVfCostModel model(root, output);
  const auto baseline = model.runVfInfo(vf);
  const auto starts = read(output / "start_by_cycle.json");
  auto overridden = vf;
  overridden.uarch["issue_ports"] = int64_t{1};
  overridden.uarch["vreg_num"] = int64_t{1};
  model.runVfInfo(InputAPI::fromCanonical(std::move(overridden)));
  require(model.predictVfCycles(vf) == baseline.vfEndCycle &&
              read(output / "start_by_cycle.json") == starts,
          "Runs reset hardware state and isolate VF overrides");

  write(
      root / "configs/isa.json",
      R"({"schema_version":2,"defaults":{"vf_startup_cost":23,"vf_drain_cost":29},"instructions":{"VLDS":{"op_class":"LOAD","forms":{"fp32":{"latency":9}}}}})");
  CoreVfCostModel updated(root);
  require(model.predictVfCycles(vf) == baseline.vfEndCycle &&
              updated.predictVfCycles(vf) == baseline.vfEndCycle + 17,
          "New models observe configuration updates");
  std::filesystem::remove_all(root / "configs");
  require(model.predictVfCycles(vf) == baseline.vfEndCycle,
          "Repeated predictions require no parameter files");

  const auto warningVf = InputAPI::loadJson(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "VFtest/canonical/fixtures/timing/"
      "cli_writes_instruction_fallback_warnings_without_vreg_warning.json");
  const auto warningsPath = output / "model_warnings.json";
  model.runVfInfo(warningVf);
  const auto warnings = read(warningsPath);
  std::filesystem::remove(warningsPath);
  model.runVfInfo(warningVf);
  require(read(warningsPath) == warnings, "Runs retain independent warnings");
  std::filesystem::remove(warningsPath);
  model.runVfInfo(vf);
  require(!std::filesystem::exists(warningsPath),
          "Clean runs do not inherit earlier warnings");
}

void testResourceParameters(const std::filesystem::path &root) {
  const auto source = std::filesystem::path(VFSIM_SOURCE_ROOT);
  ParamDB db(source);
  const auto evidence = json::parseFile(source / "docs/data/sub_exu_timing_migration_20261109.json");
  for (const auto *section : {"original_pair_values", "additional_measured_ii"})
    for (const auto &value : evidence.asObject().at(section).asArray()) {
      const auto &row = value.asObject();
      const auto previous = row.at("previous").asString(), current = row.at("current").asString();
      const auto split = previous.find('.'), otherSplit = current.find('.');
      const auto p = previous.substr(0, split), pf = previous.substr(split+1);
      const auto c = current.substr(0, otherSplit), cf = current.substr(otherSplit+1);
      const auto kind = row.find("kind");
      const auto actual = kind != row.end() && kind->second.asString() == "forwarding"
          ? db.forwardingCycles(p, pf, c, cf) : db.initiationInterval(p, pf, c, cf);
      require(actual == row.at("value").asInt(), "Migrated timing and additional measurements retain their values");
    }
  require(db.inst("VCI", "int32").executionResource == "ADD_DUAL" &&
              db.inst("VCI", "int32").selfIi == 2 &&
              db.inst("VMUL", "fp32").executionResource == "MUL",
          "Instruction profiles carry resource and self-II metadata");
  require(db.forwardingCycles("VADD", "fp32", "VMUL", "fp32") == 3 &&
              db.forwardingCycles("VADD", "fp32", "VEXP", "fp32") == 4 &&
              db.forwardingCycles("VMULL", "int32", "VMULL", "int32") == 5,
          "Resource forwarding and sparse exceptions share lookup semantics");
  const auto stores = json::parseFile(source / "docs/data/compute_store_forwarding_20261109.json");
  for (const auto &value : stores.asObject().at("pairs").asArray()) {
    const auto &row = value.asObject();
    const auto p = row.at("producer").asString(), c = row.at("consumer").asString();
    require(db.forwardingCycles(p.substr(0, p.find('.')), p.substr(p.find('.')+1),
                                c.substr(0, c.find('.')), c.substr(c.find('.')+1)) == row.at("value").asInt(),
            "Compute-to-store migration preserves all configured intervals");
  }
  require(db.forwardingCycles("VCADD", "fp32", "VSSTB", "fp32") == 20 &&
              db.forwardingCycles("PLT", "b32", "PSTS", "b8") == 4 &&
              db.forwardingCycles("VCI", "int32", "VSCATTER", "int32") == 5,
          "Compute-to-store timing covers ungrouped, predicate and scatter inputs");
  auto bundle = std::make_shared<ParamBundle>(db.bundle());
  bundle->isa["VADD"]["fp32"].latency = 14;
  bundle->forwardingByForm["VADD.fp32"]["VSTS.fp32"] = 99;
  bundle->forwardingByForm["VADD.fp32"]["*"] = 98;
  bundle->forwardingOverrides["VADD.fp32"]["VSTS.fp32"] = 97;
  ParamDB changed(bundle);
  for (int i = 0; i < 3; ++i)
    require(changed.forwardingCycles("VADD", "fp32", "VSTS", "fp32") == 12,
            "Current producer latency determines compute-to-store timing before explicit values");
  auto shortBundle = std::make_shared<ParamBundle>(db.bundle());
  shortBundle->isa["VADD"]["fp32"].latency = 1;
  ParamDB shortLatency(shortBundle);
  require(shortLatency.forwardingCycles("VADD", "fp32", "VSTS", "fp32") == 0 &&
              shortLatency.forwardingCycles("VLDS", "fp32", "VSTS", "fp32") == 8,
          "Short compute latency is clamped while LOAD-to-STORE keeps its own timing");
  std::filesystem::create_directories(root / "configs");
  for (const auto *name : {"isa.json", "uarch.json", "forwarding.json", "InitiationInterval.json", "execution_resources.json"})
    std::filesystem::copy_file(source / "configs" / name, root / "configs" / name);
  for (const auto *invalid : {
       R"({"schema_version":1,"ii":{"VADD.fp32":{"VADD.fp32":1}},"forwarding":{}})",
       R"({"schema_version":1,"ii":{"VADD.fp32":{"VMUL.fp32":true}},"forwarding":{}})",
       R"({"schema_version":1,"ii":{},"forwarding":{"VADD.fp32":{"UNKNOWN.fp32":2}}})",
       R"({"schema_version":1,"ii":{},"forwarding":{"UNKNOWN.fp32":{}}})",
       R"({"schema_version":1,"ii":{"fp32":{"VADD":{"VMUL":2}}},"forwarding":{}})"}) {
    write(root / "configs/timing_overrides.json", invalid);
    bool rejected = false;
    try { loadParameters(root); } catch (const std::runtime_error &) { rejected = true; }
    require(rejected, "Invalid sparse timing exceptions must fail loading");
  }
}

void testTimingPolicy(const std::filesystem::path &root) {
  const auto source = std::filesystem::path(VFSIM_SOURCE_ROOT);
  std::filesystem::create_directories(root / "configs");
  for (const auto *name : {"isa.json", "uarch.json", "forwarding.json", "InitiationInterval.json",
                           "execution_resources.json", "timing_overrides.json"})
    std::filesystem::copy_file(source / "configs" / name, root / "configs" / name);
  auto policy = json::parseFile(root / "configs/execution_resources.json").asObject();
  auto forwarding = policy.at("forwarding").asObject();
  forwarding["compute_store_offset"] = int64_t{5};
  forwarding["default_offset"] = int64_t{6};
  forwarding["minimum"] = int64_t{1};
  forwarding["fallback_offset"] = int64_t{5};
  forwarding["load_store_interval"] = int64_t{10};
  auto offsets = forwarding.at("pair_offsets").asObject();
  auto mathOffsets = offsets.at("LNEXP").asObject();
  mathOffsets["ADD_DUAL"] = int64_t{5};
  offsets["LNEXP"] = mathOffsets;
  forwarding["pair_offsets"] = offsets;
  policy["forwarding"] = forwarding;
  auto ii = policy.at("ii").asObject();
  ii["different_resource_interval"] = int64_t{3};
  ii["writeback_collision_penalty"] = int64_t{2};
  policy["ii"] = ii;
  std::ofstream(root / "configs/execution_resources.json") << json::stringify(policy);
  auto intervals = json::parseFile(root / "configs/InitiationInterval.json").asObject();
  intervals["defaults"] = int64_t{5};
  std::ofstream(root / "configs/InitiationInterval.json") << json::stringify(intervals);
  ParamDB db(root);
  require(db.forwardingCycles("VADD", "fp32", "VSTS", "fp32") == 2 &&
              db.forwardingCycles("VADD", "fp32", "VMUL", "fp32") == 1 &&
              db.forwardingCycles("VEXP", "fp32", "VADD", "fp32") == 11,
          "Parameter files determine store, grouped and math forwarding offsets");
  require(db.initiationInterval("VMOV", "fp32", "VADD", "fp32") == 3 &&
              db.initiationInterval("VCI", "int32", "VADD", "fp32") == 2,
          "Cross-resource policy is independent of measured self-II");
  auto bundle = std::make_shared<ParamBundle>(db.bundle());
  bundle->isa["VMUL"]["fp32"].latency = 10;
  for (const auto &[op, latency] : std::vector<std::pair<std::string, int64_t>>{{"CUSTOM_A", 12}, {"CUSTOM_B", 7}}) {
    InstructionProfile profile;
    profile.opClass = "COMPUTE";
    profile.latency = latency;
    profile.resolvedForm = "fp32";
    bundle->isa[op]["fp32"] = profile;
  }
  ParamDB modified(bundle);
  require(modified.initiationInterval("VMUL", "fp32", "VADD", "fp32") == 5 &&
              modified.initiationInterval("CUSTOM_A", "fp32", "CUSTOM_B", "fp32") == 7 &&
              modified.forwardingCycles("CUSTOM_A", "fp32", "CUSTOM_B", "fp32") == 7 &&
              modified.forwardingCycles("VLD_FUTURE", "fp32", "VST_FUTURE", "fp32") == 10,
          "Collision and missing-pair policies use configured coefficients");
  const std::vector<std::tuple<std::string, std::string, json::Value>> invalid = {
      {"forwarding", "compute_store_offset", int64_t{-1}},
      {"forwarding", "fallback_offset", true},
      {"forwarding", "load_store_interval", "6"},
      {"ii", "different_resource_interval", int64_t{0}},
      {"ii", "writeback_collision_penalty", true}};
  for (const auto &[section, field, value] : invalid) {
    auto broken = policy;
    auto fields = broken.at(section).asObject();
    fields[field] = value;
    broken[section] = fields;
    std::ofstream(root / "configs/execution_resources.json") << json::stringify(broken);
    bool rejected = false;
    try { loadParameters(root); } catch (const std::runtime_error &) { rejected = true; }
    require(rejected, "Invalid timing policy coefficients must fail loading");
  }
}
} // namespace
int main() {
  require(supportedUarchFields() ==
              uarchOverrideFieldsForTarget(UarchOverrideTarget::Cpp),
          "Native field bindings must cover the shared contract");
  const auto root =
      std::filesystem::temp_directory_path() /
      std::filesystem::path(
          "vfsim_params_" +
          std::to_string(
              std::chrono::steady_clock::now().time_since_epoch().count()));
  std::filesystem::create_directories(root / "configs");
  write(root / "configs/uarch.json", R"({"issue_ports":1})");
  write(
      root / "configs/isa.json",
      R"({"schema_version":2,"defaults":{"vf_startup_cost":5,"vf_drain_cost":8,"latency":11,"EXU":"ALU","dispatch_exu":"EXU01","op_class":"COMPUTE"},"instructions":{"CUSTOM":{"forms":{"fp32":{}}},"OTHER":{"forms":{"fp32":{"latency":10}}}}})");
  write(root / "configs/forwarding.json",
        R"({"default":5,"forwarding":{"CUSTOM.fp32":{"OTHER.fp32":3}}})");
  write(
      root / "configs/InitiationInterval.json",
      R"({"defaults":1,"InitiationInterval":{"CUSTOM.fp32":{"OTHER.fp32":4}}})");
  ParamDB db(root);
  require(db.isaDefaults().vfStartupCost == 5 &&
              db.isaDefaults().vfDrainCost == 8,
          "VF timing loads ISA defaults");
  require(db.inst("CUSTOM", "fp32").latency == 11 &&
              db.inst("CUSTOM", "fp32").fuType == "ALU",
          "Forms inherit instruction and ISA defaults");
  require(db.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 3,
          "Qualified forwarding pair is loaded");
  require(db.forwardingCycles("CUSTOM", "fp32", "CUSTOM", "fp32") == 6,
          "Forwarding fallback uses configured offset");
  require(db.initiationInterval("CUSTOM", "fp32", "OTHER", "fp32") == 4,
          "Qualified II pair is loaded");
  require(db.initiationInterval("CUSTOM", "fp32", "CUSTOM", "fp32") == 1,
          "Missing II follows latency policy");
  require(db.forwardingCycles("CUSTOM", "b32", "OTHER", "b32") == 3,
          "Compatible forwarding forms are resolved");
  const auto warnings = db.warnings();
  for (int i = 0; i < 100; ++i) {
    require(db.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 3 &&
                db.forwardingCycles("CUSTOM", "fp32", "CUSTOM", "fp32") == 6 &&
                db.forwardingCycles("CUSTOM", "b32", "OTHER", "b32") == 3,
            "Cached exact, missing and compatible pairs retain their values");
  }
  const auto cachedWarnings = db.warnings();
  require(warnings.size() == cachedWarnings.size(),
          "Cache hits must not add warnings");
  for (size_t i = 0; i < warnings.size(); ++i)
    require(warnings[i].kind == cachedWarnings[i].kind &&
                warnings[i].fields == cachedWarnings[i].fields &&
                warnings[i].count == cachedWarnings[i].count,
            "Cache hits preserve warning fields and counts");
  write(
      root / "configs/forwarding.json",
      R"({"default":5,"forwarding":{"CUSTOM.fp32":{"OTHER.fp32":8,"OTHER.fp16":2},"CUSTOM.fp16":{"OTHER.fp32":4},"OTHER.fp32":{"*":7},"VLDS.fp32":{"OTHER.fp32":9}}})");
  ParamDB otherDb(root);
  for (int i = 0; i < 2; ++i) {
    require(otherDb.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 8 &&
                db.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 3,
            "Forwarding caches must be isolated between parameter instances");
    require(otherDb.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp16") == 2 &&
                otherDb.forwardingCycles("CUSTOM", "fp16", "OTHER", "fp32") ==
                    4,
            "Both producer and consumer forms are part of the cache key");
    require(otherDb.forwardingCycles("OTHER", "fp32", "CUSTOM", "fp32") == 7,
            "Wildcard forwarding is cached");
    require(otherDb.forwardingCycles("VLDUS", "fp32", "OTHER", "fp32") == 9,
            "Forwarding opcode aliases are preserved");
  }
  write(root / "configs/InitiationInterval.json",
        R"({"InitiationInterval":{"CUSTOM.fp32":{"OTHER.fp32":true}}})");
  bool invalid = false;
  try {
    ParamDB broken(root);
  } catch (const std::runtime_error &) {
    invalid = true;
  }
  require(invalid, "Boolean pair timing must fail");
  write(root / "configs/InitiationInterval.json",
        R"({"InitiationInterval":{}})");
  write(root / "configs/uarch.json", R"({"three_ports_mode":false})");
  invalid = false;
  try {
    ParamDB broken(root);
  } catch (const std::runtime_error &) {
    invalid = true;
  }
  require(invalid, "Unknown configuration field must fail");
  require(db.inst("PSTU", "uint32").opClass == "STORE" &&
              db.inst("VLDAS", "fp32").opClass == "LOAD" &&
              db.inst("VLD_FUTURE", "fp32").opClass == "LOAD",
          "Missing ISA entries use Catalog instruction classes");
  for (const auto &warning : db.warnings())
    if (warning.kind == "unsupported_isa_op" &&
        warning.fields.at("op").asString() == "PSTU")
      require(warning.fields.at("used_defaults")
                      .asObject()
                      .at("op_class")
                      .asString() == "STORE",
              "PSTU fallback warning preserves its store classification");
  testParameterReuse(root / "snapshot");
  testResourceParameters(root / "resources");
  testTimingPolicy(root / "policy");
  std::filesystem::remove_all(root);
  std::cout
      << "Parameter inheritance, pair lookup and format validation passed\n";
}
