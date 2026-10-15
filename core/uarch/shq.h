#pragma once
#include "core/uarch/registers.h"
#include <algorithm>
namespace vfsim {
class SHQ {
public:
  explicit SHQ(const UarchConfig &cfg) : cfg_(cfg) {}
  std::deque<UopPtr> &entries() { return entries_; }
  int64_t freeQueue() const {
    return std::max<int64_t>(0, cfg_.shqDepth -
                                    static_cast<int64_t>(entries_.size()));
  }
  int64_t freeCredit() const {
    return cfg_.theoreticalLimitMode || !cfg_.enableShqCreditModel
               ? 1000000000000000000LL
               : std::max<int64_t>(0, cfg_.shqDepth -
                                          (cfg_.enableCreditVisibilityDelay
                                               ? visibleUsed_
                                               : used_));
  }
  void enqueue(const UopPtr &u, int64_t cycle) {
    u->shqReadyCycle = cycle + cfg_.oooToShqDelay;
    entries_.push_back(u);
  }
  void reserve(Uop &u) {
    if (cfg_.enableShqCreditModel &&
        (u.profile->opClass == "COMPUTE" || u.profile->opClass == "STORE")) {
      ++used_;
      if (cfg_.enableCreditVisibilityDelay)
        ++visibleUsed_;
      u.isShqTracked = true;
    }
  }
  void release(Uop &u, int64_t cycle) {
    if (u.isShqTracked) {
      ++releaseEvents_[cycle + cfg_.shqReleaseDelay];
      u.isShqTracked = false;
    }
  }
  void updateReady(int64_t cycle, const Registers &regs) {
    for (auto &u : entries_) {
      u->readyCycle = regs.readyCycle(*u);
      u->state = cycle >= u->readyCycle ? "ready" : "blocked";
    }
  }
  void runReleaseEvents(int64_t cycle) {
    auto it = releaseEvents_.find(cycle);
    if (it == releaseEvents_.end())
      return;
    const auto n = it->second;
    releaseEvents_.erase(it);
    used_ -= n;
    if (cfg_.enableCreditVisibilityDelay) {
      if (cfg_.iduVisibleShqDelay > 0)
        visibilityEvents_[cycle + cfg_.iduVisibleShqDelay] += n;
      else {
        visibleUsed_ -= n;
        delta_ += n;
      }
    }
  }
  int64_t updateVisibility(int64_t cycle) {
    auto it = visibilityEvents_.find(cycle);
    int64_t n = it == visibilityEvents_.end() ? 0 : it->second;
    if (it != visibilityEvents_.end())
      visibilityEvents_.erase(it);
    visibleUsed_ -= n;
    auto result = delta_ + n;
    delta_ = 0;
    return result;
  }
  bool empty() const { return entries_.empty(); }

private:
  const UarchConfig &cfg_;
  std::deque<UopPtr> entries_;
  int64_t used_ = 0, visibleUsed_ = 0, delta_ = 0;
  std::unordered_map<int64_t, int64_t> releaseEvents_, visibilityEvents_;
};
} // namespace vfsim
