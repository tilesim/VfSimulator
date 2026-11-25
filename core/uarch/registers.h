#pragma once
#include "core/execution/instruction.h"
#include "core/execution/prepare.h"
#include "core/params/database.h"
#include "core/uarch/register_bank.h"
namespace vfsim {
struct AlignProducerRecord {
  int64_t instId = 0;
  int64_t streamSeq = -1;
  std::string op;
  std::string form;
  std::optional<int64_t> startCycle;
  std::optional<int64_t> doneCycle;
};

struct AlignGeneration {
  std::string stateId;
  int64_t generationId = 0;
  std::vector<std::shared_ptr<AlignProducerRecord>> producers;
  std::optional<int64_t> consumerInstId;
};

struct SrcReleaseEvent {
  int64_t instId = 0;
  int64_t gen = 0;
  ProducerState *state = nullptr;
};

struct ProducerInfo {
  std::string op;
  std::string form;
  int64_t startCycle = 0;
  std::string kind;
  const InstructionProfile *profile = nullptr;
};

// One stable entry per physical slot. Uops may borrow its address because
// producerStates_ never erases nodes; a generation remains live until every
// consumer has started. Reallocation resets the state and increments its ID.
struct ProducerState {
  std::optional<ProducerInfo> producer;
  bool pending = false;
  unsigned ratBindings = 0;
  int64_t generation = 0, consumers = 0;
  int64_t slot = -1; // dense physical slot in the vector/predicate bank
  bool predicate = false;
  // Mirrors membership in releaseEligible_ so the hot rename path can skip a
  // string hash probe when the state is not eligible.
  bool releaseEligible = false;
  std::optional<int64_t> releaseEligibleCycle;
};

class Registers {
public:
  Registers(const UarchConfig &cfg, const ParamDB &db,
            const std::unordered_map<std::string, ValueInfo> &values);
  void setTraceEnabled(bool enabled) { traceEnabled_ = enabled; }
  // Dense logical-id operand table (PreparedProgram::operandIds). Engine::run
  // validates the whole table once and enables this mode for the entire run.
  void setOperandIds(const int32_t *ids, size_t count, bool enabled) {
    operandIds_ = ids;
    operandIdCount_ = count;
    operandIdsEnabled_ = enabled;
  }
  UopPtr rename(const DynamicInst &inst, int64_t cycle);
  void onStart(Uop &u, int64_t cycle);
  void onDone(Uop &u, int64_t cycle);
  void runReleaseEvents(int64_t cycle);
  void tryFreeEligible(int64_t cycle);
  std::pair<int64_t, int64_t> updateVisibility(int64_t cycle);
  int64_t freeVector() const;
  int64_t freePredicate() const;
  int64_t physicalVectorFree() const { return vector_.freeCount(); }
  int64_t physicalPredicateFree() const { return predicate_.freeCount(); }
  bool isPredicatePhysical(const std::string &id) const {
    return predicateIds_.count(id);
  }
  const ValueStorageLookup &storage() const { return valueStorage_; }
  int64_t readyCycle(const Uop &u) const;
  int64_t readyTimeForSource(const ProducerInfo &producer,
                             const Uop &consumer) const;
  // Historical API; no callers (see task-65): kept for source compatibility.
  std::optional<ProducerInfo> producer(const std::string &id) const;
  // Historical API: match the diagnostic physical name ("p<slot>"/"pred<slot>").
  bool pending(const std::string &id) const {
    for (const auto &table : {&vectorStates_, &predicateStates_})
      for (const auto &entry : *table)
        if ((entry.second.predicate ? predicate_ : vector_).name(entry.first) == id)
          return entry.second.pending;
    return false;
  }
  std::optional<int64_t> alignReady(const Uop &u) const;

private:
  bool traceEnabled_ = true;
  std::unordered_map<int64_t, const InstructionProfile *> staticProfiles_;
  const UarchConfig &cfg_;
  const ParamDB &db_;
  ValueStorageLookup valueStorage_;
  PhysicalRegisterBank vector_, predicate_;
  int64_t nextDynamicPregId_;
  std::unordered_set<std::string> predicateIds_;
  std::unordered_map<std::string, ProducerState *> rat_;
  std::vector<ProducerState *> ratById_;
  const int32_t *operandIds_ = nullptr;
  size_t operandIdCount_ = 0;
  bool operandIdsEnabled_ = false;
  int32_t operandIdAt(int32_t base, size_t index) const {
    const size_t position = static_cast<size_t>(base) + index;
    if (position >= operandIdCount_)
      throw std::runtime_error("operand id access past the operand table");
    return operandIds_[position];
  }
  ProducerState *lookupById(int32_t id) const {
    return id >= 0 && static_cast<size_t>(id) < ratById_.size()
               ? ratById_[static_cast<size_t>(id)]
               : nullptr;
  }
  ProducerState *&ratById(int32_t id) {
    if (static_cast<size_t>(id) >= ratById_.size())
      ratById_.resize(static_cast<size_t>(id) + 1, nullptr);
    return ratById_[static_cast<size_t>(id)];
  }
  // One node table per bank: stable addresses for Uops that borrow them, and
  // slot ids are never packed/truncated.
  std::unordered_map<int64_t, ProducerState> vectorStates_, predicateStates_;
  ProducerState &stateAt(int64_t slot, bool pred) {
    auto &table = pred ? predicateStates_ : vectorStates_;
    return table[slot];
  }
  std::unordered_map<std::string, ProducerState *> releaseEligible_;
  std::unordered_map<std::string, std::shared_ptr<AlignGeneration>>
      alignStateOpen_;
  std::unordered_map<std::string, std::shared_ptr<AlignProducerRecord>>
      alignLoadInitializers_;
  std::unordered_map<std::string, int64_t> alignStateNextGeneration_;
  std::unordered_map<int64_t, std::vector<SrcReleaseEvent>> srcReleaseEvents_;
  std::unordered_map<int64_t, int64_t> visibleVectorEvents_,
      visiblePredicateEvents_;
  int64_t visibleVector_, visiblePredicate_, vectorDelta_ = 0,
                                             predicateDelta_ = 0;
  // Diagnostic name of a physical slot (historical spelling).
  std::string pregName(const ProducerState &state) const {
    return (state.predicate ? predicate_ : vector_).name(state.slot);
  }
  void bindAlignState(Uop &u, const DynamicInst &inst);
  void scheduleSrcReleaseFromStart(const Uop &u);
  bool tryFree(ProducerState &state, int64_t cycle);
};
} // namespace vfsim
