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
  int32_t streamSlot = -1;
  int32_t streamLoop = -1;
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
  // Numeric value tokens for the streamed scoring path (diagnostic trace off).
  // bit 63 set: a dynamically materialised identity whose payload is the
  // cursor's monotonic identity counter; bit 63 clear: the payload is the dense
  // static operand slot, so a root/static definition keeps one stable token for
  // the whole run. The two spaces never intersect and neither is a table index:
  // the Engine's RAT is a live-binding map, not a value-indexed array.
  std::vector<uint64_t> srcTokens, dstTokens;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::string alignStateOperation;
  std::string alignStateId;
  std::string catalogMode;
  std::vector<CanonicalMemoryAccess> memoryAccesses;
  std::vector<ValueStorageKind> srcStorage, dstStorage;
  int64_t vectorDestCount = -1, predicateDestCount = -1;
  // ---- borrowed static halves (streamed scoring lowering only) -----------
  // The canonical cursor's lowering plan owns these arrays for the whole run and
  // outlives the Engine, so an event stores a read-only view instead of
  // rebuilding the same static content per event. A null view keeps the owned
  // spine above authoritative (eager preparation, diagnostics, hand-built
  // programs): every reader must go through the accessors below.
  const std::string *opView = nullptr;
  const std::string *formView = nullptr;
  const std::string *opClassView = nullptr;
  const std::string *catalogModeView = nullptr;
  const std::string *alignStateOperationView = nullptr;
  const std::string *alignStateIdView = nullptr;
  const ValueStorageKind *srcStorageView = nullptr;
  const ValueStorageKind *dstStorageView = nullptr;
  uint32_t srcStorageViewCount = 0, dstStorageViewCount = 0;
  const CanonicalMemoryAccess *memoryAccessView = nullptr;
  uint32_t memoryAccessViewCount = 0;
  // Scored lifetime bits live in the plan's contiguous flag store; the eager
  // path keeps its per-event vectors.
  const uint8_t *srcReleaseView = nullptr;
  const uint8_t *dstKeepView = nullptr;
  uint32_t srcReleaseViewCount = 0, dstKeepViewCount = 0;
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

  // ---- read-only accessors -------------------------------------------------
  // One spelling for both spines: the borrowed view wins when present, else the
  // owned container the eager/diagnostic path fills.
  const std::string &opText() const { return opView != nullptr ? *opView : op; }
  const std::string &formText() const {
    return formView != nullptr ? *formView : form;
  }
  const std::string &opClassText() const {
    return opClassView != nullptr ? *opClassView : opClass;
  }
  const std::string &catalogModeText() const {
    return catalogModeView != nullptr ? *catalogModeView : catalogMode;
  }
  const std::string &alignStateOperationText() const {
    return alignStateOperationView != nullptr ? *alignStateOperationView
                                              : alignStateOperation;
  }
  const std::string &alignStateIdText() const {
    return alignStateIdView != nullptr ? *alignStateIdView : alignStateId;
  }
  size_t srcStorageCount() const {
    return srcStorageView != nullptr ? srcStorageViewCount : srcStorage.size();
  }
  size_t dstStorageCount() const {
    return dstStorageView != nullptr ? dstStorageViewCount : dstStorage.size();
  }
  ValueStorageKind srcStorageAt(size_t index) const {
    return srcStorageView != nullptr ? srcStorageView[index]
                                     : srcStorage[index];
  }
  ValueStorageKind dstStorageAt(size_t index) const {
    return dstStorageView != nullptr ? dstStorageView[index]
                                     : dstStorage[index];
  }
  size_t memoryAccessCount() const {
    return memoryAccessView != nullptr ? memoryAccessViewCount
                                       : memoryAccesses.size();
  }
  const CanonicalMemoryAccess &memoryAccessAt(size_t index) const {
    return memoryAccessView != nullptr ? memoryAccessView[index]
                                       : memoryAccesses[index];
  }
  const CanonicalMemoryAccess *memoryAccessData() const {
    return memoryAccessView != nullptr ? memoryAccessView
                                       : memoryAccesses.data();
  }
  // Historical semantics of the per-operand bits: a missing source bit means
  // "do not release", a missing destination bit means "keep".
  bool srcReleased(size_t index) const {
    if (srcReleaseView != nullptr)
      return index < srcReleaseViewCount && srcReleaseView[index] != 0;
    return index < srcValueRelease.size() && srcValueRelease[index];
  }
  bool dstKept(size_t index) const {
    if (dstKeepView != nullptr)
      return index >= dstKeepViewCount || dstKeepView[index] != 0;
    return index >= dstValueKeep.size() || dstValueKeep[index];
  }
  bool hasBorrowedLifetime() const {
    return srcReleaseView != nullptr || dstKeepView != nullptr;
  }

  // Reset for one streamed scoring event: only the fields the cursor actually
  // rewrites per event are touched. The static halves are borrowed (views) and
  // the diagnostic spines stay empty for the whole run, so this is a handful of
  // stores instead of the full clearForReuse sweep.
  void clearForScoredEvent() {
    type = "inst";
    pc = -1;
    opView = formView = opClassView = catalogModeView = nullptr;
    alignStateOperationView = alignStateIdView = nullptr;
    srcStorageView = dstStorageView = nullptr;
    srcStorageViewCount = dstStorageViewCount = 0;
    memoryAccessView = nullptr;
    memoryAccessViewCount = 0;
    srcReleaseView = dstKeepView = nullptr;
    srcReleaseViewCount = dstKeepViewCount = 0;
    srcTokens.clear();
    dstTokens.clear();
    iterStack.clear();
    schedulingKeys.clear();
    blockEndLevels.clear();
    isLastInTopBlock = false;
  }

  // Reset for the next streamed event while keeping every container's capacity,
  // so a recycled arena slot / provider buffer / cursor scratch does not
  // allocate per dynamic instruction.
  void clearForReuse() {
    opClass = "COMPUTE";
    instId = 0;
    type = "inst";
    op.clear();
    form.clear();
    barrier.clear();
    pc = -1;
    streamSeq = -1;
    srcIdBase = -1;
    dstIdBase = -1;
    srcTokens.clear();
    dstTokens.clear();
    src.clear();
    dst.clear();
    alignStateOperation.clear();
    alignStateId.clear();
    catalogMode.clear();
    memoryAccesses.clear();
    srcStorage.clear();
    dstStorage.clear();
    vectorDestCount = predicateDestCount = -1;
    normalizedBlockKeys.clear();
    schedulingKeys.clear();
    srcValueRelease.clear();
    dstValueKeep.clear();
    staticInstructionId.clear();
    iterationPath.clear();
    srcValueInstances.clear();
    dstValueInstances.clear();
    iterStack.clear();
    topBlockId = 0;
    isLastInTopBlock = false;
    blockKeyByLevel.clear();
    blockEndLevels.clear();
    opView = formView = opClassView = catalogModeView = nullptr;
    alignStateOperationView = alignStateIdView = nullptr;
    srcStorageView = dstStorageView = nullptr;
    srcStorageViewCount = dstStorageViewCount = 0;
    memoryAccessView = nullptr;
    memoryAccessViewCount = 0;
    srcReleaseView = dstKeepView = nullptr;
    srcReleaseViewCount = dstKeepViewCount = 0;
  }
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
