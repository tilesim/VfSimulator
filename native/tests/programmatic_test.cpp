// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "native/api/input_api.h"
#include "native/api/json/serialization.h"
#include "native/core/simulator.h"
#include <chrono>
#include <filesystem>
#include <iostream>
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
void testBuilder() {
  const auto vf = vfsim::InputAPI::loadJson(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "VFtest/canonical/fixtures/v1_valid_loop.json");
  auto builder = vfsim::InputAPI::newBuilder(vf.params, vf.uarch, vf.source);
  for (const auto &[id, value] : vf.storageObjects)
    builder.registerStorageObject(value);
  for (const auto &[id, value] : vf.values)
    builder.registerValue(value);
  builder.addLoop(*std::get<std::shared_ptr<const vfsim::CanonicalLoop>>(
      vf.context.front().payload));
  require(vfsim::encodeCanonicalVfInfoJson(builder.build()) ==
              vfsim::encodeCanonicalVfInfoJson(vf),
          "Builder must preserve the Canonical loop contract");
  bool duplicate = false;
  try {
    builder.addLoop(*std::get<std::shared_ptr<const vfsim::CanonicalLoop>>(
        vf.context.front().payload));
  } catch (const std::invalid_argument &) {
    duplicate = true;
  }
  require(duplicate, "Builder must reject duplicate nested node identities");
  require(vfsim::encodeCanonicalVfInfoJson(builder.build()) ==
              vfsim::encodeCanonicalVfInfoJson(vf),
          "Failed additions must preserve builder state");
  vfsim::VfInfoBuilder scopes;
  vfsim::CanonicalLoop definition;
  definition.loopId = "loop.scope";
  definition.induction.variableId = "i";
  try {
    auto guard = scopes.loop(definition);
    auto value = vf.values.begin()->second;
    scopes.registerValue(value);
    bool openRejected = false;
    try {
      (void)scopes.build();
    } catch (const std::logic_error &) {
      openRejected = true;
    }
    require(openRejected, "Builder must reject build while a scope is open");
    throw std::runtime_error("abort scope");
  } catch (const std::runtime_error &) {
  }
  require(scopes.build().values.empty() && scopes.build().context.empty(),
          "Unwinding a loop must roll back values and body");
  {
    auto guard = scopes.loop(definition);
    vfsim::CanonicalMembar barrier;
    barrier.instructionId = "tail";
    barrier.barrier = "VST_VLD";
    scopes.addMembar(barrier);
  }
  require(scopes.build().context.size() == 1,
          "Successful loop scopes must commit their body");
  auto predicateBuilder = vfsim::InputAPI::newBuilder();
  vfsim::CanonicalInstruction instruction;
  instruction.instructionId = "mask";
  instruction.opcode = "PSET_B32";
  instruction.instructionClass = vfsim::CanonicalInstructionClass::Compute;
  instruction.form = "b32";
  instruction.outputs.push_back({"mask.0",
                                 vfsim::CanonicalOperandRole::Destination,
                                 std::string("bool"),
                                 {}});
  predicateBuilder.registerValue(
      {"mask.0",
       "mask",
       vfsim::CanonicalStorageKind::PredicateRegister,
       "bool",
       {},
       std::string("mask"),
       {},
       {}});
  predicateBuilder.addInstruction(instruction);
  require(predicateBuilder.build().schemaVersion == 2,
          "Builder must emit v2 for predicate definitions");
}
void testRoundTripAndFailure() {
  const auto fixtures = std::filesystem::path(VFSIM_SOURCE_ROOT) /
                        "VFtest/canonical/fixtures";
  for (const auto &entry :
       std::filesystem::recursive_directory_iterator(fixtures)) {
    if (entry.path().extension() != ".json" ||
        entry.path().filename().string().find("invalid") != std::string::npos)
      continue;
    const auto vf = vfsim::InputAPI::loadJson(entry.path());
    const auto encoded = vfsim::encodeCanonicalVfInfoJson(vf);
    const auto decoded =
        vfsim::InputAPI::fromCanonical(vfsim::decodeCanonicalVfInfoJson(
            vfsim::json::parseString(vfsim::json::stringify(encoded))));
    require(vfsim::encodeCanonicalVfInfoJson(decoded) == encoded,
            "Canonical JSON must round trip without losing metadata");
  }
  const auto root =
      std::filesystem::temp_directory_path() /
      ("vfsim_public_" +
       std::to_string(
           std::chrono::steady_clock::now().time_since_epoch().count()));
  std::filesystem::create_directories(root / "configs");
  for (const auto *file :
       {"isa.json", "uarch.json", "forwarding.json", "InitiationInterval.json"})
    std::filesystem::copy_file(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                                   "configs" / file,
                               root / "configs" / file);
  auto vf = vfsim::InputAPI::loadJson(fixtures / "v1_valid_loop.json");
  vfsim::CoreVfCostModel model(root);
  (void)model.runVfInfo(vf);
  require(
      std::filesystem::is_regular_file(root / "results/cost_model/trace.json"),
      "Public model must use the documented default output directory");
  auto shortVf = vf;
  shortVf.params["max_cycles"] = 1;
  vfsim::CoreVfCostModel timeout(root, root / "timeout");
  bool failed = false;
  try {
    (void)timeout.runVfInfo(shortVf);
  } catch (const std::runtime_error &) {
    failed = true;
  }
  require(failed && !std::filesystem::exists(root / "timeout"),
          "Timed out runs must not write partial result files");
  auto loopVf = vfsim::InputAPI::loadJson(
      fixtures / "timing/membar_disables_innermost_unroll_with_warning.json");
  auto loop = *std::get<std::shared_ptr<const vfsim::CanonicalLoop>>(
      loopVf.context.front().payload);
  loop.count = int64_t{3};
  loopVf.context.front() = vfsim::CanonicalNode::makeLoop(loop);
  failed = false;
  try {
    (void)model.runVfInfo(vfsim::InputAPI::fromCanonical(loopVf));
  } catch (const std::runtime_error &error) {
    failed =
        std::string(error.what()).find("not divisible") != std::string::npos;
  }
  require(failed, "Membar must not bypass the unroll divisibility check");
  std::filesystem::remove_all(root);
}
} // namespace
int main() {
  testBuilder();
  testRoundTripAndFailure();
  std::cout << "Builder, Canonical round trip and public execution boundary "
               "checks passed\n";
}
