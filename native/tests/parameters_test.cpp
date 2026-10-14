// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/native/common/uarch_validation.h"
#include "native/tests/support.h"
#include <chrono>
#include <fstream>
#include <iostream>
using namespace vfsim;
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
void write(const std::filesystem::path &path, const char *text) {
  std::ofstream(path) << text;
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
              db.inst("CUSTOM", "fp32").exu == "ALU",
          "Forms inherit instruction and ISA defaults");
  require(db.forwardingCycles("CUSTOM", "fp32", "OTHER", "fp32") == 3,
          "Qualified forwarding pair is loaded");
  require(db.forwardingCycles("CUSTOM", "fp32", "CUSTOM", "fp32") == 6,
          "Forwarding fallback uses configured offset");
  require(db.initiationInterval("CUSTOM", "fp32", "OTHER", "fp32") == 4,
          "Qualified II pair is loaded");
  require(db.initiationInterval("CUSTOM", "fp32", "CUSTOM", "fp32") == 1,
          "Missing II follows latency policy");
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
  std::filesystem::remove_all(root);
  std::cout
      << "Parameter inheritance, pair lookup and format validation passed\n";
}
