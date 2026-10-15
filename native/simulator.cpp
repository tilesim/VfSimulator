// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/simulator.h"
#include "native/execution/engine.h"
#include "native/params/loader.h"
#include "native/params/uarch.h"
namespace vfsim {
CoreVfCostModel::CoreVfCostModel(std::filesystem::path baseDir,
                               std::filesystem::path outDir,
                               bool vloopOnly, bool directIssue)
    : baseDir_(ParamDB::resolveBaseDir(std::move(baseDir))),
      outDir_(std::move(outDir)), vloopOnly_(vloopOnly),
      directIssue_(directIssue),
      parameters_(std::make_shared<const ParamBundle>(loadParameters(baseDir_))) {}

SimulationResult CoreVfCostModel::runVfInfo(const CanonicalVfInfo &vf,
                                            int64_t maxCycles) const {
  ParamDB db(parameters_);
  const auto cfg = resolveUarch(db.uarch(), vf.uarch, vloopOnly_, directIssue_);
  const auto program = prepareProgram(vf, db, cfg);
  Engine engine(program, cfg, db);
  auto output = outDir_;
  if (!output.empty() && !output.is_absolute())
    output = baseDir_ / output;
  return engine.run(output, maxCycles);
}
} // namespace vfsim
