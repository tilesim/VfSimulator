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
  std::string preg;
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
  std::string physical;
  bool predicate = false;
  std::optional<int64_t> releaseEligibleCycle;
};

class Registers {
public:
  Registers(const UarchConfig &cfg, const ParamDB &db,
            const std::unordered_map<std::string, ValueInfo> &values);
  void setTraceEnabled(bool enabled) { traceEnabled_ = enabled; }
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
  std::optional<ProducerInfo> producer(const std::string &id) const;
  bool pending(const std::string &id) const {
    const auto it = producerStates_.find(id);
    return it != producerStates_.end() && it->second.pending;
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
  std::unordered_map<std::string, ProducerState> producerStates_;
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
  void bindAlignState(Uop &u, const DynamicInst &inst);
  void scheduleSrcReleaseFromStart(const Uop &u);
  bool tryFree(ProducerState &state, int64_t cycle);
};
} // namespace vfsim
