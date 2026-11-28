// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/simulator.h"
#include "core/execution/engine.h"
#include "core/execution/scoring_stream.h"
#include "core/params/loader.h"
#include "core/params/uarch.h"
namespace vfsim {
CoreVfCostModel::CoreVfCostModel(std::filesystem::path baseDir,
                                 std::filesystem::path outDir, bool vloopOnly,
                                 bool directIssue)
    : baseDir_(ParamDB::resolveBaseDir(std::move(baseDir))),
      outDir_(std::move(outDir)), vloopOnly_(vloopOnly),
      directIssue_(directIssue),
      parameters_(
          std::make_shared<const ParamBundle>(loadParameters(baseDir_))) {}

SimulationResult CoreVfCostModel::runVfInfo(const CanonicalVfInfo &vf,
                                            int64_t maxCycles) const {
  ParamDB db(parameters_);
  const auto cfg = resolveUarch(db.uarch(), vf.uarch, vloopOnly_, directIssue_);
  auto output = outDir_;
  if (!output.empty() && !output.is_absolute())
    output = baseDir_ / output;
  // Opt-in streamed scoring path (feature switch, default off). It is only
  // eligible when no trace/results directory is requested, because trace and
  // export need the diagnostic eager preparation and its instruction log.
  const bool wantsLogs = !outDir_.empty();
  if (scoringStreamEnabled() && !wantsLogs) {
    ScoringStream plan = prepareScoringStream(vf, db, cfg);
    if (plan.declineReason == nullptr || *plan.declineReason == '\0') {
      // Supported shape: run it. Runtime failures (budget, allocation, a stream
      // that ends early) propagate exactly as on the eager path; there is no
      // catch-all degradation here.
      Engine engine(plan.program, cfg, db);
      SimulationResult streamed = runScoringStream(plan, engine, maxCycles);
      streamed.compilerOptimizationStats = plan.program.compilerOptimizationStats;
      streamed.scoringMode = "scoring";
      streamed.scoringEventCount = plan.fetched;
      return streamed;
    }
    // Shape or configuration this path cannot stream: release the candidate
    // static plan first (program + cursor) so the rejection path does not hold
    // its memory while the eager preparation runs, then report the degradation.
    const std::string decline = plan.declineReason;
    plan.program = PreparedProgram{};
    plan.cursor.reset();
    plan.eventCount = -1;
    plan.fetched = 0;
    const auto program = prepareProgram(vf, db, cfg, false);
    Engine engine(program, cfg, db);
    auto result = engine.run(output, maxCycles);
    result.compilerOptimizationStats = program.compilerOptimizationStats;
    result.scoringMode = "eager-fallback:" + decline;
    return result;
  }
  const auto program = prepareProgram(vf, db, cfg, wantsLogs);
  Engine engine(program, cfg, db);
  auto result = engine.run(output, maxCycles);
  result.compilerOptimizationStats = program.compilerOptimizationStats;
  return result;
}
} // namespace vfsim
