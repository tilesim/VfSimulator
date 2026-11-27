#include "core/execution/scoring_stream.h"
#include <cstdlib>

#include "core/execution/engine.h"
#include "core/execution/expansion.h"
#include "core/execution/optimization.h"

namespace vfsim {
namespace {
bool gScoringStreamEnabled = false;
} // namespace

int32_t scoringStreamSlotBound(const UarchConfig &cfg) {
  // In-flight borrowed instructions live in: the slot being fetched (1), the IDU
  // window, the store/load queues, the exq, and the per-port issue inflight caps.
  // Live borrowed slots are exactly: the slot being fetched (1), the IDU
  // window, and Engine::pipe_ -- the pointer-carrying delay line holds one
  // issue group per delayed cycle. SHQ/LDQ/EXQ hold admitted Uop copies, not
  // borrowed DynamicInst slots, so their depths must not appear here (LDQ_width
  // alone used to inflate the bound to 2,400,113 against a high water of 6).
  const int64_t window = std::max<int64_t>(0, cfg.iduWindowWidth);
  const int64_t width = std::max<int64_t>(0, cfg.iduIssueWidth);
  const int64_t delay = std::max<int64_t>(0, cfg.iduToOooDelay);
  // Checked mul/add: every term is clamped to >= 0 first (a negative or missing
  // uarch field must not shrink the bound), then the whole expression is proven
  // to fit int32 before the cast. Refuse (0) instead of wrapping.
  constexpr int64_t kMax = std::numeric_limits<int32_t>::max();
  if (window > kMax - 2) return 0;
  const int64_t room = kMax - 2 - window;
  if (width != 0 && delay > room / width) return 0;
  const int64_t bound = window + width * delay + 2;
  if (bound > static_cast<int64_t>(std::numeric_limits<int32_t>::max()))
    return 0;
  return static_cast<int32_t>(bound);
}

bool scoringStreamEnabled() { return gScoringStreamEnabled; }

void setScoringStreamEnabled(bool enabled) { gScoringStreamEnabled = enabled; }

ScoringStream prepareScoringStream(const CanonicalVfInfo &vf,
                                   const ParamDB &db, const UarchConfig &cfg) {
  ScoringStream plan;
  CompilerOptimizationStats stats;
  // The rewrite is handed over only when LICM actually moved something; an
  // unchanged program stays borrowed, so no canonical node is copied.
  auto hoisted = hoistLoopInvariantContext(vf, stats);
  // One static layout for both halves: the Engine-facing metadata and the
  // cursor's lowering plan are interned by the same Expander instead of one
  // being built for the metadata and thrown away before the cursor rebuilds it.
  auto prepared = prepareProgramForStreamingLayout(vf, std::move(hoisted), db,
                                                   cfg, std::move(stats));
  plan.program = std::move(prepared.program);
  plan.eventCount = plan.program.dynamicEventCount;
  plan.cursor =
      makeCanonicalCursorForLayout(vf, std::move(prepared.layout), db, cfg);
  const char *why = plan.cursor->declineReason();
  if (why != nullptr && *why != '\0') {
    plan.declineReason = why;
    return plan;
  }
  // theoreticalLimitMode lets the IDU window grow without a cap, so the number
  // of simultaneously borrowed instructions is not provably bounded; refuse the
  // streamed path and let the caller run the eager preparation.
  if (cfg.theoreticalLimitMode) {
    plan.declineReason = "theoretical-limit-mode-unbounded";
    return plan;
  }
  plan.slotBound = scoringStreamSlotBound(cfg);
  if (plan.slotBound <= 0) {
    plan.declineReason = "stream-slot-bound-unavailable";
    return plan;
  }
  return plan;
}

SimulationResult runScoringStream(ScoringStream &plan, Engine &engine,
                                  int64_t maxCycles) {
  // The provider lowers directly into the Engine's stream slot. done() is exact:
  // with a known static event total it is a counter comparison, otherwise one
  // event is peeked so the IFU never pulls past the end.
  struct Provider {
    CanonicalCursor &cursor;
    int64_t expected = -1;
    int64_t fetched = 0;
    int64_t prefetches = 0;
    DynamicInst peeked;
    bool havePeek = false;
    bool ended = false;

    bool done() {
      if (expected >= 0)
        return fetched >= expected;
      if (!havePeek && !ended) {
        ++prefetches;
        havePeek = cursor.next(peeked);
        ended = !havePeek;
      }
      return ended;
    }
    void fetch(DynamicInst &out) {
      const int32_t slotBase = out.srcIdBase;
      ++fetched;
      if (expected >= 0) {
        if (!cursor.next(out))
          throw std::runtime_error(
              "scoring stream ended before the static event total");
      } else {
        if (!havePeek)
          throw std::runtime_error("scoring fetch without a peeked event");
        std::swap(out, peeked);
        havePeek = false;
      }
      out.srcIdBase = slotBase;
    }
  } provider{*plan.cursor, plan.eventCount};
  // The cursor lowers numeric value tokens (no identity strings), so the
  // Engine keys its live-binding RAT by uint64 instead of by string.
  engine.useInstructionStream([&provider]() { return provider.done(); },
                              [&provider](DynamicInst &out) {
                                provider.fetch(out);
                              },
                              /*operandBlockSize=*/0, plan.slotBound,
                              /*numericTokens=*/true);
  SimulationResult result = engine.run({}, maxCycles);
  plan.fetched = provider.fetched;
  plan.prefetches = provider.prefetches;
  if (const LoweringProfile *profile = plan.cursor->loweringProfile())
    plan.loweringProfile = *profile;
  return result;
}

} // namespace vfsim
