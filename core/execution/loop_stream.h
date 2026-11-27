#pragma once
#include "api/common/schema.h"
#include "core/execution/prepare.h"
#include "core/trace/writer.h"
#include <optional>
#include <string>
namespace vfsim {
class ParamDB;
struct LoopStreamStats {
  bool attempted = false;
  bool accepted = false;
  std::string declineReason;
  int64_t staticInstructions = 0;
  int64_t staticLoopNodes = 0;
  int64_t iterations = 0;
  int64_t fetchedInstructions = 0;
  int64_t dynamicHighWater = 0;
  // Bridge-facing counters, derived from the verified post-LICM template.
  bool expandedCounterValid = false;
  // Opt-in: digesting every dynamic instruction is verification-only and
  // must not tax the production scoring path.
  bool collectVerificationDigest = false;
  bool collectTiming = false;
  int64_t horizontalReductions = 0;
  int64_t loopLoads = 0;
  int64_t predicateSets = 0;
  int64_t vectorDups = 0;
  int64_t loopVectorDups = 0;
  int64_t peakLive = 0;
  int64_t windowHighWater = 0;
  int64_t arenaHighWater = 0;
  int64_t liveValueHighWater = 0;
  int64_t liveValueCount = 0;
  int64_t valueLookupOps = 0;
  int64_t arenaBytesHighWater = 0;
  int64_t staticTemplateBytes = 0;
  int64_t maxOutstanding = 0;
  int64_t fetchCount = 0;
  int64_t issuedInstructions = 0;
  double prepareSeconds = 0;
  double runSeconds = 0;
  double fetchSeconds = 0;
  double engineSeconds = 0;
  int64_t numericSlots = 0;
  int64_t numericPeakId = 0;
  int64_t numericTableBytes = 0;
  int64_t numericRing = 0;
  int64_t numericIdBytes = 0;
  uint64_t instructionDigest = 1469598103934665603ull;
  uint64_t structureDigest = 1469598103934665603ull;
  uint64_t lifetimeDigest = 1469598103934665603ull;
  uint64_t addressDigest = 1469598103934665603ull;
};
// Scoring-only bounded-stream prototype. On any unsupported shape it returns
// nullopt before executing; callers must use the old eager path.
std::optional<SimulationResult>
tryRunLoopStream(const CanonicalVfInfo &, const ParamDB &, const UarchConfig &,
                 int64_t maxCycles, LoopStreamStats *stats = nullptr);
} // namespace vfsim