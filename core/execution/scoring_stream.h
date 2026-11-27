#pragma once
#include <memory>

#include "core/execution/cursor.h"
#include "core/execution/prepare.h"

namespace vfsim {

class Engine;
struct SimulationResult;

// Normal no-trace scoring entry point for the streamed engine path.
//
// One LICM rewrite, one static metadata build (no dynamic instruction, operand
// id or per-instance value materialisation) and one cursor over the very same
// post-LICM context. The instruction stream is produced on demand by the
// cursor; nothing in the program grows with the dynamic event count.
struct ScoringStream {
  PreparedProgram program;
  std::unique_ptr<CanonicalCursor> cursor;
  // Exact dynamic event total derived from the static graph (-1 when the count
  // overflowed int64, in which case the provider peeks one event ahead).
  int64_t eventCount = -1;
  int64_t fetched = 0;
  int64_t prefetches = 0;
  // Non-empty when this shape cannot be streamed at all (cursor decline, or a
  // configuration whose in-flight window is not provably bounded): the caller
  // must run the eager path for this program.
  const char *declineReason = "";
  // Upper bound on simultaneous in-flight stream slots, derived from the queue
  // configuration. The Engine enforces it (a breach means the bound is wrong,
  // not that the program is unsupported).
  int32_t slotBound = 0;
  // Diagnostic lowering split, filled only when VFSIM_PROFILE_LOWERING is on.
  LoweringProfile loweringProfile;
};

ScoringStream prepareScoringStream(const CanonicalVfInfo &, const ParamDB &,
                                   const UarchConfig &);

// Slot bound implied by the queue configuration: every structure that can hold
// a borrowed streamed instruction contributes its capacity. theoreticalLimitMode
// removes the IDU window cap (and with it the bound), so that configuration is
// refused instead of run unbounded.
int32_t scoringStreamSlotBound(const UarchConfig &);

// Attaches the on-demand provider to an Engine constructed from
// plan.program and runs it. fetch() lowers straight into the Engine's stream
// slot; done() is exact (static event total, or a one-event peek).
SimulationResult runScoringStream(ScoringStream &plan, Engine &engine,
                                  int64_t maxCycles);

// Feature switch for callers that keep both paths compiled: the default is the
// eager preparation (prepareProgram). Enabling this routes the normal no-trace
// scoring path through prepareScoringStream/runScoringStream.
bool scoringStreamEnabled();
void setScoringStreamEnabled(bool enabled);

} // namespace vfsim
