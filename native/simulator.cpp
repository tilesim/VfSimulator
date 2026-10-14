// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/simulator.h"
#include "native/execution/engine.h"
#include "native/params/uarch.h"
namespace vfsim {
SimulationResult CoreVfCostModel::runVfInfo(const CanonicalVfInfo &vf,
                                            int64_t maxCycles) const {
  ParamDB db(baseDir_);
  const auto cfg = resolveUarch(db.uarch(), vf.uarch, vloopOnly_, directIssue_);
  const auto program = prepareProgram(vf, db, cfg);
  Engine engine(program, cfg, db);
  auto output = outDir_;
  if (!output.empty() && !output.is_absolute())
    output = ParamDB::resolveBaseDir(baseDir_) / output;
  return engine.run(output, maxCycles);
}
} // namespace vfsim
