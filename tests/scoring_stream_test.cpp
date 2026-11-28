// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
//
// Regression coverage for the opt-in streamed scoring route through the real
// scoring entry (CoreVfCostModel::runVfInfo).
//
// The route is only reachable through the feature switch, so every other test
// exercises the eager preparation exclusively. This one asserts that
//   * the default stays eager and reports no stream counters,
//   * VFSIM-style opt-in really selects the streamed preparation (scoringMode),
//   * the streamed route keeps the eager result: same cycles, vfEndCycle and
//     linearInstCount, with the exact static event count,
//   * an unbounded configuration is refused from the configuration alone,
//   * the stream provider does not outlive the single run it was installed for.
#include "api/input_api.h"
#include "core/execution/engine.h"
#include "core/execution/scoring_stream.h"
#include "core/params/database.h"
#include "core/params/uarch.h"
#include "core/simulator.h"
#include <filesystem>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>

namespace {
void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

struct Counts {
  int64_t cycles = 0, vfEnd = 0, linear = 0;
  int64_t events = -1;
  std::string mode;
};

Counts run(const std::filesystem::path &path, bool stream) {
  const auto vf = vfsim::InputAPI::loadJson(path);
  vfsim::setScoringStreamEnabled(stream);
  // Empty results directory: the cost-only preparation, i.e. exactly the scoring
  // call the compiler-side integration makes.
  const vfsim::CoreVfCostModel model({}, {}, false, false);
  const auto result = model.runVfInfo(vf, 0);
  Counts counts;
  counts.cycles = result.cyclesExecuted;
  counts.vfEnd = result.vfEndCycle;
  counts.linear = result.linearInstCount;
  counts.events = result.scoringEventCount;
  counts.mode = result.scoringMode;
  return counts;
}

// expectedEvents is the static event total of the fixture; it is asserted
// exactly so a silent stream truncation cannot pass as a shorter correct run.
void checkCase(const std::filesystem::path &fixture, const char *name,
               int64_t expectedEvents) {
  require(!vfsim::scoringStreamEnabled(),
          std::string(name) + ": the streamed route must default to off");
  const Counts eager = run(fixture, false);
  require(eager.mode == "eager",
          std::string(name) + ": default route must be eager, got " + eager.mode);
  require(eager.events == -1,
          std::string(name) + ": eager must not report a stream event count");

  const Counts stream = run(fixture, true);
  require(vfsim::scoringStreamEnabled(),
          std::string(name) + ": the switch must enable the streamed route");
  require(stream.mode == "scoring",
          std::string(name) + ": streamed route must be selected, got " +
              stream.mode);
  require(stream.events == expectedEvents,
          std::string(name) + ": event count " +
              std::to_string(stream.events) + " != " +
              std::to_string(expectedEvents));
  require(stream.cycles == eager.cycles,
          std::string(name) + ": cyclesExecuted " +
              std::to_string(stream.cycles) + " != " +
              std::to_string(eager.cycles));
  require(stream.vfEnd == eager.vfEnd,
          std::string(name) + ": vfEndCycle " + std::to_string(stream.vfEnd) +
              " != " + std::to_string(eager.vfEnd));
  require(stream.linear == eager.linear,
          std::string(name) + ": linearInstCount " +
              std::to_string(stream.linear) + " != " +
              std::to_string(eager.linear));

  // Back to the default: the switch must not leak into the next caller.
  vfsim::setScoringStreamEnabled(false);
  const Counts again = run(fixture, false);
  require(again.mode == "eager",
          std::string(name) + ": route must return to eager after reset");

  std::cout << "case=" << name << " eager=" << eager.cycles << "/" << eager.vfEnd
            << "/" << eager.linear << " stream=" << stream.cycles << "/"
            << stream.vfEnd << "/" << stream.linear
            << " events=" << stream.events << '\n';
}

// A configuration whose in-flight window is not provably bounded must be refused
// from the configuration alone: no static layout, no cursor, no events.
void checkPureConfigDeclines(const std::filesystem::path &fixture) {
  const auto vf = vfsim::InputAPI::loadJson(fixture);
  vfsim::ParamDB db(VFSIM_SOURCE_ROOT);
  {
    const auto cfg = vfsim::resolveUarch(db.uarch(), vf.uarch, true, false);
    require(cfg.theoreticalLimitMode,
            "vloop-only must enable the theoretical limit mode");
    const auto plan = vfsim::prepareScoringStream(vf, db, cfg);
    require(plan.cursor == nullptr && plan.program.instructions.empty() &&
                plan.eventCount == -1 && plan.slotBound == 0,
            "an unbounded configuration must be refused before preparation");
    require(std::string(plan.declineReason) ==
                "theoretical-limit-mode-unbounded",
            std::string("unexpected decline: ") + plan.declineReason);
  }
  {
    auto cfg = vfsim::resolveUarch(db.uarch(), vf.uarch, false, false);
    cfg.iduWindowWidth = std::numeric_limits<int32_t>::max();
    const auto plan = vfsim::prepareScoringStream(vf, db, cfg);
    require(plan.cursor == nullptr && plan.program.instructions.empty() &&
                plan.eventCount == -1 && plan.slotBound == 0,
            "an unbounded slot window must be refused before preparation");
    require(std::string(plan.declineReason) == "stream-slot-bound-unavailable",
            std::string("unexpected decline: ") + plan.declineReason);
  }
  std::cout << "pure_config_decline=OK\n";
}

// A streamed Engine is single-run by construction: when the run ends (normally
// or with a budget refusal) the provider is detached and the engine keeps
// partially borrowed instructions, so a second run must fail with a clear logic
// error before it touches any queue. It used to call into the destroyed provider
// and segfault.
void checkSingleRunContract(const std::filesystem::path &fixture) {
  const auto vf = vfsim::InputAPI::loadJson(fixture);
  vfsim::ParamDB db(VFSIM_SOURCE_ROOT);
  const auto cfg = vfsim::resolveUarch(db.uarch(), vf.uarch, false, false);
  {
    auto plan = vfsim::prepareScoringStream(vf, db, cfg);
    require(plan.declineReason == nullptr || *plan.declineReason == '\0',
            "the fixture must be streamable");
    vfsim::Engine engine(plan.program, cfg, db);
    const auto first = vfsim::runScoringStream(plan, engine, 0);
    require(first.cyclesExecuted > 0,
            "the streamed run must execute the program");
    bool rejected = false;
    try {
      (void)engine.run({}, 0);
    } catch (const std::logic_error &e) {
      rejected =
          std::string(e.what()).find("exactly one run") != std::string::npos;
    }
    require(rejected,
            "a second run on a streamed Engine must be refused with a logic "
            "error naming the single-run contract");
  }
  {
    auto plan = vfsim::prepareScoringStream(vf, db, cfg);
    vfsim::Engine engine(plan.program, cfg, db);
    std::string budgetError;
    try {
      (void)vfsim::runScoringStream(plan, engine, 5);
    } catch (const std::exception &e) {
      budgetError = e.what();
    }
    require(budgetError ==
                "Simulation did not complete before max_cycles: cycle=5",
            std::string("unexpected budget error: ") + budgetError);
    bool rejected = false;
    try {
      (void)engine.run({}, 0);
    } catch (const std::logic_error &e) {
      rejected =
          std::string(e.what()).find("exactly one run") != std::string::npos;
    }
    require(rejected,
            "a budget-refused streamed run must consume the Engine as well");
  }
  std::cout << "single_run=OK\n";
}

// The provider takes over instruction supply, so a program that already
// materialised its instructions would have them silently dropped.
void checkMaterializedProgramRejected(const std::filesystem::path &fixture) {
  const auto vf = vfsim::InputAPI::loadJson(fixture);
  vfsim::ParamDB db(VFSIM_SOURCE_ROOT);
  const auto cfg = vfsim::resolveUarch(db.uarch(), vf.uarch, false, false);
  const auto program = vfsim::prepareProgram(vf, db, cfg, false);
  require(!program.instructions.empty(),
          "the eager preparation must materialise instructions");
  vfsim::Engine engine(program, cfg, db);
  bool rejected = false;
  try {
    engine.useInstructionStream([] { return true; },
                                [](vfsim::DynamicInst &) {});
  } catch (const std::logic_error &) {
    rejected = true;
  }
  require(rejected,
          "useInstructionStream must refuse a materialised program instead of "
          "dropping its instructions");
  std::cout << "materialized_program=OK\n";
}

// A declined plan has no cursor: running it would dereference an empty cursor.
void checkDeclinedPlanRejected(const std::filesystem::path &fixture) {
  const auto vf = vfsim::InputAPI::loadJson(fixture);
  vfsim::ParamDB db(VFSIM_SOURCE_ROOT);
  const auto bounded = vfsim::resolveUarch(db.uarch(), vf.uarch, false, false);
  const auto unbounded = vfsim::resolveUarch(db.uarch(), vf.uarch, true, false);
  auto plan = vfsim::prepareScoringStream(vf, db, unbounded);
  require(plan.cursor == nullptr && *plan.declineReason != '\0',
          "the unbounded configuration must decline before preparation");
  vfsim::Engine engine(plan.program, bounded, db);
  bool rejected = false;
  try {
    (void)vfsim::runScoringStream(plan, engine, 0);
  } catch (const std::logic_error &) {
    rejected = true;
  }
  require(rejected, "runScoringStream must refuse a declined plan");
  std::cout << "declined_plan=OK\n";
}
} // namespace

int main() {
  const auto root = std::filesystem::path(VFSIM_SOURCE_ROOT) / "VFtest/canonical";
  checkCase(root / "fixtures/v1_valid_loop.json", "v1_valid_loop", 4);
  checkCase(root / "fixtures/v1_valid_loop_carried.json", "v1_valid_loop_carried",
            4);
  checkCase(root / "VADD_twoloop.json", "VADD_twoloop", 96);
  checkPureConfigDeclines(root / "fixtures/v1_valid_loop.json");
  checkSingleRunContract(root / "fixtures/v1_valid_loop.json");
  checkMaterializedProgramRejected(root / "fixtures/v1_valid_loop.json");
  checkDeclinedPlanRejected(root / "fixtures/v1_valid_loop.json");
  std::cout << "scoring_stream=OK\n";
  return 0;
}