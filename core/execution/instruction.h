#pragma once
#include "api/common/schema.h"
#include "core/params/types.h"
#include <deque>
#include <tuple>
#include <unordered_set>
namespace vfsim {
using RuntimeParamMap = std::unordered_map<std::string, int64_t>;
using ValueStorageKind = CanonicalStorageKind;
struct ValueInfo {
  std::string valueId;
  ValueStorageKind storage = ValueStorageKind::Unknown;
  std::string dtype;
  std::vector<int64_t> shape;
};
struct AlignProducerRecord;
struct AlignGeneration;
struct DynamicInst {
  std::string opClass = "COMPUTE";
  int64_t instId = 0;
  std::string type = "inst";
  std::string op;
  std::string form;
  std::string barrier;
  int64_t pc = -1;
  int64_t streamSeq = -1;
  std::vector<std::string> src;
  std::vector<std::string> dst;
  std::string alignStateOperation;
  std::string alignStateId;
  std::string catalogMode;
  std::vector<CanonicalMemoryAccess> memoryAccesses;
  std::vector<bool> srcValueRelease;
  std::vector<bool> dstValueKeep;
  std::string staticInstructionId;
  std::vector<std::pair<std::string, int64_t>> iterationPath;
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
  std::vector<std::string> pregDst;

  // Owned by the run-local ParamDB; its lifetime covers all components and traces.
  const InstructionProfile *profile = nullptr;
  int64_t ubTransferBytes = 0;

  std::string state = "blocked";
  int64_t readyCycle = 0;
  std::optional<int64_t> startCycle;
  std::optional<int64_t> doneCycle;
  std::optional<std::string> blockedReason;

  std::optional<std::string> producerOpForStore;
  std::optional<std::string> producerFormForStore;
  std::optional<int64_t> producerStartForStore;
  bool storeDependenciesResolved = false;
  std::string alignStateOperation;
  std::string alignStateId;
  std::shared_ptr<AlignGeneration> alignGeneration;
  std::shared_ptr<AlignProducerRecord> alignProducerRecord;
  std::shared_ptr<AlignProducerRecord> alignLoadProducer;
  int64_t topBlockId = 0;
  std::vector<int64_t> iterStack;
  bool isLastInTopBlock = false;
  int64_t streamSeq = -1;
  std::string staticInstructionId;
  std::vector<std::pair<std::string, int64_t>> iterationPath;
  int exuPort = -1;
  int64_t shqReadyCycle = 0;
  int64_t lsqReadyCycle = 0;
  bool isShqTracked = false;
  int64_t exqRecvCycle = 0;
  int64_t exqPredIssue = 0;
};

using UopPtr = std::shared_ptr<Uop>;
} // namespace vfsim
