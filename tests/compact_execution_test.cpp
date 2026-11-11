// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "tests/support.h"
#include <iostream>
using namespace vfsim;
namespace {
struct Outcome {
  int64_t cycles = 0, end = 0, instructions = 0;
  std::string error;
  bool operator==(const Outcome &other) const {
    return cycles == other.cycles && end == other.end &&
           instructions == other.instructions && error == other.error;
  }
};
Outcome run(const CanonicalVfInfo &vf, const ParamDB &db, UarchConfig cfg,
            bool diagnostics, int64_t maxCycles = 10000) {
  try {
    const auto program = prepareProgram(vf, db, cfg, diagnostics);
    Engine engine(program, cfg, db);
    const auto result = engine.run({}, maxCycles);
    return {
        result.cyclesExecuted, result.vfEndCycle, result.linearInstCount, {}};
  } catch (const std::exception &error) {
    return {0, 0, 0, error.what()};
  }
}
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
} // namespace
int main(int argc, char **argv) {
  ParamDB db(VFSIM_SOURCE_ROOT);
  const auto fixtureRoot = argc > 1 ? std::filesystem::path(argv[1])
                                    : std::filesystem::path(VFSIM_SOURCE_ROOT) /
                                          "VFtest/canonical/fixtures";
  int fixtures = 0, comparisons = 0, invalid = 0;
  for (const auto &entry :
       std::filesystem::recursive_directory_iterator(fixtureRoot)) {
    if (entry.path().extension() != ".json")
      continue;
    CanonicalVfInfo vf;
    try {
      vf = InputAPI::loadJson(entry.path());
    } catch (const std::exception &) {
      ++invalid;
      continue;
    }
    ++fixtures;
    for (int mode = 0; mode < 3; ++mode) {
      const auto cfg = resolveUarch(db.uarch(), vf.uarch, mode != 0, mode == 2);
      for (int64_t budget : {int64_t{1}, int64_t{10000}}) {
        const auto ordinary = run(vf, db, cfg, true, budget);
        const auto compact = run(vf, db, cfg, false, budget);
        require(ordinary == compact,
                "Cost-only execution changed cycles or failure");
        ++comparisons;
      }
      auto limited = cfg;
      limited.canonicalDynamicInstructionLimit = 1;
      require(run(vf, db, limited, true) == run(vf, db, limited, false),
              "Cost-only execution changed the dynamic instruction budget");
      ++comparisons;
    }
  }
  auto vf = InputAPI::loadJson(fixtureRoot / "v1_valid_loop.json");
  auto loop = *std::get<std::shared_ptr<const CanonicalLoop>>(
      vf.context.front().payload);
  loop.count = int64_t{128};
  vf.context.front() = CanonicalNode::makeLoop(loop);
  auto collision = vf.values.at("acc.0");
  collision.definitionId = collision.logicalId = "@0";
  collision.producerNodeId.reset();
  vf.values.emplace("@0", collision);
  vf = InputAPI::fromCanonical(std::move(vf));
  const auto cfg = resolveUarch(db.uarch(), vf.uarch);
  require(run(vf, db, cfg, true) == run(vf, db, cfg, false),
          "Compact identities changed repeated register reuse or collided with "
          "a static ID");
  const auto program = prepareProgram(vf, db, cfg, false);
  bool rejected = false;
  try {
    Engine engine(program, cfg, db);
    engine.run("/tmp/vfsim-incomplete-trace-must-not-exist");
  } catch (const std::runtime_error &) {
    rejected = true;
  }
  require(rejected, "Compact preparation must reject incomplete trace export");
  std::cout << "valid_fixtures=" << fixtures << " invalid_fixtures=" << invalid
            << " equivalent_outcomes=" << comparisons + 1 << '\n';
}
