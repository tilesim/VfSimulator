#pragma once
#include "native/execution/membar.h"
#include "native/trace/recorder.h"
#include "native/uarch/registers.h"
#include "native/uarch/shq.h"
namespace vfsim {
struct LsuIssueCounts {
  int64_t loads = 0, stores = 0, total = 0;
};
class LSU {
public:
  LSU(const UarchConfig &cfg, const ParamDB &db, Registers &regs, SHQ &shq,
      MembarController &control, TraceRecorder &trace)
      : cfg_(cfg), db_(db), regs_(regs), shq_(shq), control_(control),
        trace_(trace) {}
  void enqueue(const UopPtr &u, int64_t cycle) {
    u->lsqReadyCycle = cycle + cfg_.oooToLsqDelay;
    waiting_.push_back(u);
  }
  int64_t freeQueue() const {
    return cfg_.theoreticalLimitMode
               ? 1000000000000000000LL
               : cfg_.ldqWidth - static_cast<int64_t>(waiting_.size());
  }
  bool hasPendingBefore(int64_t seq, const std::string &cls) const;
  void updateReady(int64_t cycle, bool storesOnly = false);
  LsuIssueCounts issue(int64_t cycle, LsuIssueCounts counts,
                       std::unordered_set<int64_t> &loggedBlocked);
  std::vector<UopPtr> finish(int64_t cycle);

private:
  int64_t storeReady(Uop &u) const;
  const UarchConfig &cfg_;
  const ParamDB &db_;
  Registers &regs_;
  SHQ &shq_;
  MembarController &control_;
  TraceRecorder &trace_;
  std::deque<UopPtr> waiting_;
  std::map<int64_t, UopPtr> running_;
  int64_t budgetCycle_ = -1, issuedBytes_ = 0;
};
} // namespace vfsim
