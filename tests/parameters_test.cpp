// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/common/uarch_validation.h"
#include "core/params/loader.h"
#include "tests/support.h"
#include <chrono>
#include <fstream>
#include <iostream>
#include <iterator>
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
  write(root / "configs/isa.json",
        R"({"schema_version":2,"defaults":{"vf_startup_cost":23,"vf_drain_cost":12},"instructions":{"VLDS":{"op_class":"LOAD","forms":{"fp32":{"latency":9}}}}})");
  write(root / "configs/forwarding.json", "{}");
  write(root / "configs/InitiationInterval.json", "{}");
  auto parameters = std::make_shared<const ParamBundle>(loadParameters(root));
  ParamDB first(parameters), second(parameters);
  require(&first.bundle() == &second.bundle(), "Runs share the parameter bundle");
  first.forwardingCycles("VLDS", "fp32", "VLDS", "fp32");
  require(!first.warnings().empty() && second.warnings().empty(),
          "Shared parameters keep warnings local");
  second.forwardingCycles("VLDS", "fp32", "VLDS", "fp32");
  require(!second.warnings().empty(), "Each run records its own cache misses");

  const auto vf = InputAPI::loadJson(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "tests/fixtures/canonical_vf_info/v1_valid_loop.json");
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

  write(root / "configs/isa.json",
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
      "tests/fixtures/canonical_vf_info/timing/"
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
  write(root / "configs/forwarding.json",
        R"({"default":5,"forwarding":{"CUSTOM.fp32":{"OTHER.fp32":8,"OTHER.fp16":2},"CUSTOM.fp16":{"OTHER.fp32":4},"OTHER.fp32":{"*":7},"VLDS.fp32":{"OTHER.fp32":9}}})");
  ParamDB otherDb(root);
  for (int i = 0; i < 2; ++i) {
    require(otherDb.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 8 &&
                db.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 3,
            "Forwarding caches must be isolated between parameter instances");
    require(otherDb.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp16") == 2 &&
                otherDb.forwardingCycles("CUSTOM", "fp16", "OTHER", "fp32") == 4,
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
  testParameterReuse(root / "snapshot");
  std::filesystem::remove_all(root);
  std::cout
      << "Parameter inheritance, pair lookup and format validation passed\n";
}
