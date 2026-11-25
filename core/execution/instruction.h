#pragma once
#include "api/common/schema.h"
#include "core/params/types.h"
#include <deque>
#include <set>
#include <tuple>
#include <unordered_set>

// Narrow, branch-free state used by every hot reader. Diagnostics keep the
// historical spellings through stateName().
enum class UopState : uint8_t { blocked = 0, ready, running, done, exqWait };
inline const char *stateName(UopState s) {
  switch (s) {
  case UopState::blocked: return "blocked";
  case UopState::ready: return "ready";
  case UopState::running: return "running";
  case UopState::done: return "done";
  case UopState::exqWait: return "exq_wait";
  }
  return "blocked";
}

namespace vfsim {
using RuntimeParamMap = std::unordered_map<std::string, int64_t>;
using ValueStorageKind = CanonicalStorageKind;
struct ValueInfo {
  std::string valueId;
  ValueStorageKind storage = ValueStorageKind::Unknown;
  std::string dtype;
  std::vector<int64_t> shape;
};
struct ProducerState;
struct AlignProducerRecord;
struct AlignGeneration;
struct IterationIdentity {
  std::string loopId;
  int64_t iteration = 0;
  std::string inductionVariable;
  int64_t inductionValue = 0;
};
struct ValueInstance {
  std::string definitionId;
  std::vector<IterationIdentity> iterationPath;
};
struct SchedulingKey {
  int64_t topBlockId = 0;
  int32_t depth = -1;
  int64_t c0 = -1, c1 = -1;
  std::string slow;
  bool operator==(const SchedulingKey &o) const {
    if (!slow.empty() || !o.slow.empty())
      return slow == o.slow && topBlockId == o.topBlockId;
    return topBlockId == o.topBlockId && depth == o.depth && c0 == o.c0 && c1 == o.c1;
  }
};

struct SchedulingKeyHash {
  size_t operator()(const SchedulingKey &k) const {
    if (!k.slow.empty())
      return std::hash<std::string>()(k.slow);
    size_t h = std::hash<int64_t>()(k.topBlockId);
    h = h * 1315423911u + static_cast<size_t>(k.depth);
    h = h * 1315423911u + static_cast<size_t>(k.c0);
    h = h * 1315423911u + static_cast<size_t>(k.c1);
    return h;
  }
};

struct DynamicInst {
  std::string opClass = "COMPUTE";
  int64_t instId = 0;
  std::string type = "inst";
  std::string op;
  std::string form;
  std::string barrier;
  int64_t pc = -1;
  int64_t streamSeq = -1;
  // Dense logical-operand span into PreparedProgram::operandIds. -1 means
  // the instruction has no id range (hand-built programs, diagnostics).
  int32_t srcIdBase = -1;
  int32_t dstIdBase = -1;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::string alignStateOperation;
  std::string alignStateId;
  std::string catalogMode;
  std::vector<CanonicalMemoryAccess> memoryAccesses;
  std::vector<ValueStorageKind> srcStorage, dstStorage;
  int64_t vectorDestCount = -1, predicateDestCount = -1;
  std::vector<std::string> normalizedBlockKeys;
  std::vector<SchedulingKey> schedulingKeys;
  std::vector<bool> srcValueRelease;
  std::vector<bool> dstValueKeep;
  std::string staticInstructionId;
  std::vector<IterationIdentity> iterationPath;
  std::vector<ValueInstance> srcValueInstances, dstValueInstances;
  std::vector<int64_t> iterStack;
  int64_t topBlockId = 0;
  bool isLastInTopBlock = false;
  std::vector<std::pair<std::string, std::vector<int64_t>>> blockKeyByLevel;
  std::vector<int64_t> blockEndLevels;
};

struct Uop {
  int64_t instId = 0;
  std::string op;
  std::string form;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::vector<std::optional<std::string>> pregSrc;
  std::vector<std::optional<int64_t>> pregSrcGen;
  std::vector<ProducerState *> sourceStates, destinationStates;
  std::vector<std::string> pregDst;
  // Destination banks used by this instruction, recorded during rename.
  std::set<std::string> writebackBanks;

  // Owned by the run-local ParamDB; its lifetime covers all components and
  // traces.
  const InstructionProfile *profile = nullptr;
  int64_t ubTransferBytes = 0;

  UopState state = UopState::blocked;
  int64_t readyCycle = 0;
  // Cache the prefix whose renamed sources have known producer start times.
  // Source generations remain live until this consumer starts; queue delays
  // are still applied separately on every readiness query.
  mutable size_t resolvedSourceCount = 0;
  mutable int64_t sourceReadyCycle = 0;
  std::optional<int64_t> startCycle;
  std::optional<int64_t> doneCycle;
  std::optional<std::string> blockedReason;

  std::optional<std::string> producerOpForStore;
  std::optional<std::string> producerFormForStore;
  std::optional<int64_t> producerStartForStore;
  bool storeDependenciesResolved = false;
  int64_t storeSourceReadyCycle = 0;
  std::string alignStateOperation;
  std::string alignStateId;
  std::shared_ptr<AlignGeneration> alignGeneration;
  std::shared_ptr<AlignProducerRecord> alignProducerRecord;
  std::shared_ptr<AlignProducerRecord> alignPreviousProducer;
  int64_t topBlockId = 0;
  std::vector<int64_t> iterStack;
  bool isLastInTopBlock = false;
  int64_t streamSeq = -1;
  std::string staticInstructionId;
  std::vector<IterationIdentity> iterationPath;
  std::vector<ValueInstance> srcValueInstances, dstValueInstances;
  int exuPort = -1;
  int64_t shqReadyCycle = 0;
  int64_t lsqReadyCycle = 0;
  bool isShqTracked = false;
  int64_t exqRecvCycle = 0;
  int64_t exqPredIssue = 0;
};

using UopPtr = std::shared_ptr<Uop>;
} // namespace vfsim
