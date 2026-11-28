#pragma once
#include "core/execution/membar.h"
#include "core/trace/recorder.h"
#include "core/uarch/registers.h"
#include "core/uarch/shq.h"
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
    if (u->profile->opClass == "STORE")
      pendingStoreRefreshes_.push_back(u.get());
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
  // Non-owning, FIFO-ordered list of queued STOREs that still need a state
  // refresh. waiting_ keeps ownership and the issue/hasPendingBefore order.
  std::vector<Uop *> pendingStoreRefreshes_;
  void compactPendingStores(bool dropReady, int64_t cycle);
  // Reused across cycles: rebuilding the ready candidate list must not allocate
  // two containers (vector + sort scratch) on every issue attempt.
  std::vector<UopPtr> issueCandidates_;
  // Completed-order bookkeeping only needs the in-flight set: a contiguous
  // buffer avoids one tree node per started uop, and finish() stable-compacts
  // the survivors before returning the due batch ordered by instId.
  std::vector<UopPtr> running_;
  std::map<std::string, int64_t> resourceReady_;
  int64_t budgetCycle_ = -1, issuedBytes_ = 0;
};
} // namespace vfsim
