#pragma once
#include "core/execution/prepare.h"
namespace vfsim {
struct VloopTraceRecord {
  int64_t topBlockId = 0;
  std::string loopId;
  std::vector<int64_t> iter;
  int64_t startCycle = 0;
};

class LoopControl {
public:
  LoopControl(const UarchConfig &, const PreparedProgram &);
  bool canDispatch(const DynamicInst &, int64_t cycle);
  void notifyDispatch(const DynamicInst &inst, int64_t cycle) {
    updateLastDispatch(inst, cycle);
    triggerNextVloops(inst, cycle);
    // fillBlockBoundaries only emits a level when blockClosedAt proves no later
    // instruction can carry that key, so after the reads above the exact key is
    // dead. Records that are actually kept (trace/export/legacy) skip this.
    if (!traceRecordsEnabled_)
      retireClosedKeys(inst);
  }
  const std::vector<VloopTraceRecord> &trace() const { return vloopTrace_; }
  // A scored run without trace/export never reads the records back; it turns
  // them off so the vector stays empty instead of growing per loop group.
  void setTraceRecordsEnabled(bool enabled) {
    traceRecordsEnabled_ = enabled;
    if (!enabled)
      vloopTrace_.clear();
  }

private:
  const UarchConfig &cfg_;
  int64_t vloopToDispatchDelay_, initialTopBlockVloopStartCycle_,
      nestedVloopInitialStartGap_, loop1MinFeedbackGap_,
      innermostIterDispatchStride_;
  int64_t totalTopBlocks_ = 1;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds_;
  std::unordered_set<int64_t> emptyTopBlocks_;

  std::unordered_map<int64_t, int64_t> topBlockVloopStart_;
  std::unordered_map<int64_t, int64_t> topBlockBodyOpenTime_;
  std::unordered_map<SchedulingKey, int64_t, SchedulingKeyHash> vloopStart_;
  std::unordered_map<SchedulingKey, int64_t, SchedulingKeyHash> bodyOpenTime_;
  std::unordered_map<SchedulingKey, int64_t, SchedulingKeyHash> lastDispatchTime_;
  std::unordered_map<SchedulingKey, int64_t, SchedulingKeyHash> blockBaseCycle_;
  std::vector<VloopTraceRecord> vloopTrace_;
  // Defaults to true so eager, trace, export and legacy runs are unchanged.
  bool traceRecordsEnabled_ = true;
  void pushTrace(VloopTraceRecord record) {
    if (traceRecordsEnabled_)
      vloopTrace_.push_back(std::move(record));
  }
  void retireClosedKeys(const DynamicInst &inst);
  void initVloopStarts();
  std::optional<int64_t> nextNonemptyTopBlock(int64_t start) const;
  void setTopBlockVloop(int64_t topBlockId, int64_t startCycle);
  void initTopBlockNestedStarts(int64_t topBlockId, int64_t topVloopStart);

  SchedulingKey keyFromNormalized(const std::string &text) const;
  SchedulingKey makeKey(int64_t topBlockId, const std::string &loopId,
                      const std::vector<int64_t> &iters) const;
  std::optional<SchedulingKey>
  normalizeBlockKey(const std::pair<std::string, std::vector<int64_t>> &raw,
                    int64_t topBlockId) const;
  std::optional<SchedulingKey>
  currentInnerBlockKey(const DynamicInst &inst) const;

  void updateLastDispatch(const DynamicInst &inst, int64_t cycle);
  void triggerNextVloops(const DynamicInst &inst, int64_t cycle);
};
} // namespace vfsim
