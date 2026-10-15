#pragma once
#include "api/input_api.h"
#include "api/json/serialization.h"
#include "core/execution/engine.h"
#include "core/params/uarch.h"
#include "core/simulator.h"
namespace vfsim {
inline SimulationResult runProgram(CanonicalVfInfo vf, const ParamDB &db,
                                   const std::filesystem::path &out = {},
                                   int64_t maxCycles = 0) {
  vf = InputAPI::fromCanonical(std::move(vf));
  auto cfg = resolveUarch(db.uarch(), vf.uarch);
  auto p = prepareProgram(vf, db, cfg);
  Engine engine(p, cfg, db);
  return engine.run(out, maxCycles);
}
} // namespace vfsim
