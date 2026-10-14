#pragma once
#include "native/execution/instruction.h"
#include "native/execution/prepare.h"
#include "native/params/database.h"
#include "native/uarch/register_bank.h"
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
};

struct ProducerInfo {
  std::string op;
  std::string form;
  int64_t startCycle = 0;
  std::string kind;
};

class Registers {
public:
  Registers(const UarchConfig &cfg, const ParamDB &db,
            const std::unordered_map<std::string, ValueInfo> &values);
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
  bool pending(const std::string &id) const { return pregPending_.count(id); }
  std::optional<int64_t> alignReady(const Uop &u) const;

private:
  const UarchConfig &cfg_;
  const ParamDB &db_;
  ValueStorageLookup valueStorage_;
  PhysicalRegisterBank vector_, predicate_;
  std::unordered_set<std::string> predicateIds_;
  std::unordered_map<std::string, std::string> rat_;
  std::unordered_map<std::string, ProducerInfo> pregProducer_;
  std::unordered_set<std::string> pregPending_;
  std::unordered_map<std::string, int> pregConsumerCount_;
  std::unordered_map<std::string, int64_t> pregGeneration_,
      pregReleaseEligibleCycle_;
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
  bool tryFree(const std::string &id, int64_t cycle);
};
} // namespace vfsim
