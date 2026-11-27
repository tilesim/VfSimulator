// Cursor lifetime static plan: bounded use-site analysis over the post-LICM
// canonical structure. No dynamic instruction stream is materialised: the plan
// walks representative loop iterations (entry layers, interior copies, exit
// layers derived from each loop's static binding distance) and runs the same
// reverse release/keep scan the eager Expander uses. State therefore scales
// with the static program shape, not with the trip count.
#pragma once
#include "api/common/schema.h"
#include <cstdint>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {
namespace cursor_lifetime {

// One abstract iteration of a loop: representative class plus unroll lane.
struct IterationCoordinate {
  int32_t rep = 0;
  int32_t lane = 0;
};

// Abstract event key: static instruction plus one coordinate per enclosing
// loop, outermost first (same order as the canonical frame stack).
struct IterationKey {
  std::vector<IterationCoordinate> frames;
};

// One static instruction of the abstract stream.
//
// Operands are dense static value slots held in the plan's two contiguous stores
// (instruction i owns [srcOffset, srcOffset + srcCount) and
// [dstOffset, dstOffset + dstCount)). Keeping one vector of strings per operand
// list made the shape walk allocate three times per instruction and made
// publishing the plan a full deep copy of every name; the enclosing loop path is
// inline for the same reason. The slots are the same identity space the
// lowering's dense value table uses, so identity equality is plain integer
// equality and no name is ever rebuilt per event.
struct StaticInstructionInfo {
  // Deepest canonical loop nesting the abstract key can spell.
  static constexpr size_t kMaxLoopDepth = 4;
  std::string instructionId;
  int32_t loopPath[kMaxLoopDepth] = {-1, -1, -1, -1};
  uint8_t loopPathDepth = 0;
  uint32_t srcOffset = 0;
  uint32_t srcCount = 0;
  uint32_t dstOffset = 0;
  uint32_t dstCount = 0;
  const int32_t *loopPathData() const { return loopPath; }
  size_t loopPathSize() const { return loopPathDepth; }
};

struct LoopInfo {
  std::string loopId;
  int32_t index = 0;
  int64_t count = 0;
  int64_t unroll = 1;
  int64_t groups = 0;  // count / unroll
  // Maximum static binding distance of a use site to the definition serving it,
  // in loop iterations: 0 when every read binds inside its own iteration, 1
  // when carried or fallthrough reads bind the previous iteration. Derived from
  // the body's use sites, never from the trip count; -1 means "not derived".
  int32_t hop = -1;
  // Representative actual group index per rep class. A loop is represented
  // exactly (all groups enumerated) when groups <= representativeGroup.size();
  // otherwise the class set is 3 * (hop + 1) entries: hop+1 entry layers, hop+1
  // interior copies and hop+1 exit layers.
  std::vector<int64_t> representativeGroup;
  bool exactRepresentatives = false;
};

class Plan {
 public:
  const std::vector<StaticInstructionInfo> &instructions() const {
    return instructions_;
  }
  // Dense static value slots, one contiguous store per operand direction. A
  // view is (srcSlots() + info.srcOffset, info.srcCount); the stores never move
  // once the plan is published.
  const int32_t *srcSlots() const { return srcSlots_.data(); }
  const int32_t *dstSlots() const { return dstSlots_.data(); }
  size_t valueSlotCount() const { return valueSlotCount_; }
  const std::vector<LoopInfo> &loops() const { return loops_; }
  int32_t instructionIndex(const std::string &staticInstructionId) const;
  int32_t loopIndex(const std::string &loopId) const;
  // Maps a real iteration index onto its representative class and unroll lane.
  // Pass the cursor's absolute iteration number (0..count-1), not a group index.
  IterationCoordinate coordinateFor(int32_t loopIndex,
                                    int64_t iteration) const;
  // Builds the query key for a static instruction from the cursor's real
  // iteration numbers, outermost loop first. Returns false when the number of
  // iterations does not match the instruction's enclosing-loop depth. This is
  // the intended integration entry point (see INTEGRATION.md).
  bool iterationKeyFor(int32_t instructionIndex,
                       const std::vector<int64_t> &iterations,
                       IterationKey &out) const;
  // True when every loop is represented exactly (all of its groups enumerated),
  // i.e. the abstract event stream equals the eager stream event by event.
  bool exactRepresentatives() const { return allExact_; }
  bool hasEvent(int32_t instructionIndex, const IterationKey &key) const;
  bool srcRelease(int32_t instructionIndex, const IterationKey &key,
                  size_t srcIndex) const;
  bool dstKeep(int32_t instructionIndex, const IterationKey &key,
               size_t dstIndex) const;
  // Scored hot path: the cursor hands over the representative coordinates it
  // already amortised per frame, so no vector of iteration numbers is built and
  // coordinateFor is not re-run per event per loop. The frame pointer may be
  // null when depth is 0 (a root-level instruction owns no coordinate) and is
  // never read in that case. Equivalence with eventFlagsForIterations is exact:
  // same depth validation, same EventKey, same flags_ views.
  bool eventFlagsByCoord(int32_t instructionIndex,
                         const IterationCoordinate *frames, uint8_t depth,
                         const uint8_t **srcRelease, size_t *srcReleaseCount,
                         const uint8_t **dstKeep, size_t *dstKeepCount) const;
  // One-shot per-event lookup: builds the query key once and resolves the flags
  // table once, handing back read-only views of the release/keep bits. Kept as
  // the iteration-number spelling for standalone callers; the streaming hot path
  // uses eventFlagsByCoord.
  bool eventFlags(int32_t instructionIndex, const IterationKey &key,
                  const uint8_t **srcRelease, size_t *srcReleaseCount,
                  const uint8_t **dstKeep, size_t *dstKeepCount) const;
  bool eventFlagsForIterations(int32_t instructionIndex,
                               const std::vector<int64_t> &iterations,
                               const uint8_t **srcRelease,
                               size_t *srcReleaseCount, const uint8_t **dstKeep,
                               size_t *dstKeepCount) const;
  // Abstract event keys in emission order. Diagnostic only: the strings are
  // rebuilt on the first call and are never formed on the normal scoring path.
  const std::vector<std::string> &eventOrder() const;
  size_t abstractEventCount() const { return abstractEventCount_; }
  // FNV-1a over the abstract stream's lifetime bits, same mixing as the eager
  // oracle's digest_life (src bits then dst bits, event order). Comparable to
  // the oracle digest only when exactRepresentatives() is true.
  uint64_t digestLife() const;

  // Exact event key: static instruction index, enclosing-loop depth and one
  // representative class / lane coordinate per enclosing loop. It is the
  // unordered_map key, so a lookup is coordinate equality (the hash only selects
  // the bucket); a hash collision can never alias two events. Fixed size, no
  // allocation.
  static constexpr size_t kMaxEventDepth = StaticInstructionInfo::kMaxLoopDepth;
  struct EventKey {
    int32_t instruction = -1;
    uint8_t depth = 0;
    IterationCoordinate frames[kMaxEventDepth];
    bool operator==(const EventKey &other) const {
      if (instruction != other.instruction || depth != other.depth)
        return false;
      for (size_t index = 0; index < depth; ++index)
        if (frames[index].rep != other.frames[index].rep ||
            frames[index].lane != other.frames[index].lane)
          return false;
      return true;
    }
  };
  struct EventKeyHash {
    size_t operator()(const EventKey &key) const {
      uint64_t hash = 1469598103934665603ull;
      auto mix = [&hash](int64_t value) {
        hash ^= static_cast<uint64_t>(value) + 0x9e3779b97f4a7c15ull +
                (hash << 6) + (hash >> 2);
      };
      mix(key.instruction);
      mix(key.depth);
      for (size_t index = 0; index < key.depth; ++index) {
        mix(key.frames[index].rep);
        mix(key.frames[index].lane);
      }
      return static_cast<size_t>(hash);
    }
  };
  // One event record: its exact key plus the event's slice of the contiguous
  // flag store (srcRelease bits followed by dstKeep bits, each sized by the
  // static instruction's operand counts).
  struct EventEntry {
    EventKey key;
    uint32_t flagOffset = 0;
    uint32_t srcCount = 0;
    uint32_t dstCount = 0;
  };
  // Numeric event key: static instruction index plus the representative
  // coordinates of every enclosing loop. Kept as a stable utility; the scoring
  // path no longer needs a numeric address because the exact key is the index.
  static uint64_t numericKey(int32_t instructionIndex, const IterationKey &key);
  std::vector<StaticInstructionInfo> instructions_;
  std::vector<LoopInfo> loops_;
  std::unordered_map<std::string, int32_t> instructionById_;
  std::unordered_map<std::string, int32_t> loopById_;
  // One exact-key index plus two contiguous stores: event records in emission
  // order and their lifetime bits. No string key, no per-event container, and no
  // hash-address side table with a string fallback. A straight-line program (no
  // loops) emits exactly one depth-0 event per instruction, so the index is the
  // instruction index and the hash index is skipped entirely.
  std::unordered_map<EventKey, uint32_t, EventKeyHash> eventByKey_;
  std::vector<EventEntry> events_;
  std::vector<uint8_t> flags_;
  // Dense static value slots owned by the plan (see StaticInstructionInfo).
  std::vector<int32_t> srcSlots_;
  std::vector<int32_t> dstSlots_;
  size_t valueSlotCount_ = 0;
  size_t abstractEventCount_ = 0;
  bool allExact_ = true;
  // True when events_ is exactly one depth-0 event per static instruction, so a
  // lookup is the instruction index (straight-line program, no loops at all).
  bool denseEventIndex_ = false;
  // Diagnostic cache: event key strings in emission order, built on demand.
  mutable std::vector<std::string> orderedKeys_;
  mutable bool orderedKeysBuilt_ = false;
};

// Builds the plan from a canonical program. LICM is applied internally with
// the frozen optimisation helper, mirroring prepareProgram's context rewrite.
// Returns nullopt and fills declineReason when the shape is outside the
// supported range. Standalone callers (oracle tests, tools) use this entry.
std::optional<Plan> buildPlan(const CanonicalVfInfo &vf,
                              std::string &declineReason);

// Builds the plan from an already post-LICM canonical context. Callers that
// already ran the frozen LICM rewrite (the canonical cursor hands over exactly
// the context it expands) use this entry so LICM runs once per program while
// sharing the same builder, validation and decline strings. The context must
// be the same opaque rewrite hoistLoopInvariantContext produced for the vf.
std::optional<Plan> buildPlanForContext(const CanonicalVfInfo &vf,
                                        const std::vector<CanonicalNode> &context,
                                        std::string &declineReason);

}  // namespace cursor_lifetime
}  // namespace vfsim
