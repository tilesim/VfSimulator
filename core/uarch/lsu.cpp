// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/uarch/lsu.h"
#include <algorithm>
namespace vfsim {
namespace {
// K1: the issue order only needs (preferred-class rank, seq fallback instId,
// instId). Compute that key once per ready candidate instead of once per
// comparison, and sort POD keys instead of sharing UopPtr copies.
struct IssueKey {
  int64_t seqKey;
  int64_t instId;
  uint32_t index; // index into waiting_ (stable during one issue() call)
  uint8_t cls;    // 0 = preferred class, 1 = other
};
} // namespace

bool LSU::hasPendingBefore(int64_t seq, const std::string &cls) const {
  for (const auto &u : waiting_)
    if (u->streamSeq < seq && u->profile->opClass == cls)
      return true;
  for (const auto &u : running_)
    if (u->streamSeq < seq && u->profile->opClass == cls)
      return true;
  return false;
}
int64_t LSU::storeReady(Uop &u) const {
  if (u.storeDependenciesResolved)
    return std::max(u.storeSourceReadyCycle, u.lsqReadyCycle);
  u.storeDependenciesResolved = false;
  int64_t best = -1;
  u.producerOpForStore.reset();
  u.producerFormForStore.reset();
  u.producerStartForStore.reset();
  for (const auto *state : u.sourceStates)
    if (state && !state->producer && state->pending)
      return 1000000000;
  for (const auto *state : u.sourceStates)
    if (state && state->producer) {
      const auto &p = *state->producer;
      if (p.kind != "COMPUTE" && p.kind != "LOAD")
        continue;
      auto t = regs_.readyTimeForSource(p, u);
      if (t > best) {
        best = t;
        u.producerOpForStore = p.op;
        u.producerFormForStore = p.form;
        u.producerStartForStore = p.startCycle;
      }
    }
  auto align = regs_.alignReady(u);
  if (align) {
    if (*align == 1000000000)
      return *align;
    best = std::max(best, *align);
  }
  if (best < 0)
    return 1000000000;
  u.storeDependenciesResolved = true;
  u.storeSourceReadyCycle = best;
  return std::max(best, u.lsqReadyCycle);
}
void LSU::updateReady(int64_t cycle, bool storesOnly) {
  if (storesOnly) {
    // L1: a resolved store's deadline is frozen (lsqReadyCycle is written once
    // at enqueue, the source bound once at resolve), so the second-phase
    // refresh only needs the cached comparison; stores that are not resolved
    // yet still run the original storeReady path (pending producer / align
    // sentinel / no producer).
    for (Uop *u : pendingStoreRefreshes_) {
      if (u->state == UopState::running)
        continue;
      if (!u->storeDependenciesResolved)
        u->readyCycle = storeReady(*u);
      u->state = cycle >= u->readyCycle ? UopState::ready : UopState::blocked;
    }
    compactPendingStores(true, cycle);
    return;
  }
  for (auto &u : waiting_) {
    if (u->profile->opClass == "LOAD") {
      u->readyCycle = std::max(regs_.readyCycle(*u), u->lsqReadyCycle);
      auto align = regs_.alignReady(*u);
      if (align)
        u->readyCycle = std::max(u->readyCycle, *align);
    } else if (u->storeDependenciesResolved)
      u->readyCycle = std::max(u->storeSourceReadyCycle, u->lsqReadyCycle);
    else
      u->readyCycle = storeReady(*u);
    u->state = cycle >= u->readyCycle ? UopState::ready : UopState::blocked;
  }
  compactPendingStores(true, cycle);
}

// Drops issued/ready-resolved stores from the refresh list; the order of the
// survivors (enqueue order) is preserved.
void LSU::compactPendingStores(bool dropReady, int64_t cycle) {
  pendingStoreRefreshes_.erase(
      std::remove_if(pendingStoreRefreshes_.begin(), pendingStoreRefreshes_.end(),
                     [&](const Uop *u) {
                       if (u->state == UopState::running)
                         return true;
                       return dropReady && u->storeDependenciesResolved &&
                              cycle >= u->readyCycle;
                     }),
      pendingStoreRefreshes_.end());
}
LsuIssueCounts LSU::issue(int64_t cycle, LsuIssueCounts counts,
                          std::unordered_set<int64_t> &loggedBlocked) {
  if (budgetCycle_ != cycle) {
    budgetCycle_ = cycle;
    issuedBytes_ = 0;
  }
  if (cycle < db_.isaDefaults().vfStartupCost || counts.total >= cfg_.ubSlots)
    return counts;
  const std::string preferred =
      regs_.physicalVectorFree() < cfg_.lsuStorePriorityPregThreshold ? "STORE"
                                                                      : "LOAD";
  // F4: the issue order is (preferred-class rank, seqKey, instId), so keep one
  // compact list per rank. Concatenating the two ascending lists reproduces the
  // old total order; a list only needs sorting when its own subsequence is not
  // already ascending (the FIFO scan frequently delivers it sorted).
  static thread_local std::vector<IssueKey> rankKeys[2];
  std::vector<IssueKey> &rank0 = rankKeys[0];
  std::vector<IssueKey> &rank1 = rankKeys[1];
  rank0.clear();
  rank1.clear();
  for (size_t i = 0; i < waiting_.size(); ++i) {
    const UopPtr &u = waiting_[i];
    if (u->state != UopState::ready)
      continue;
    const IssueKey key{u->streamSeq < 0 ? u->instId : u->streamSeq, u->instId,
                       static_cast<uint32_t>(i),
                       static_cast<uint8_t>(u->profile->opClass == preferred ? 0
                                                                             : 1)};
    (key.cls == 0 ? rank0 : rank1).push_back(key);
  }
  // The key ends in the unique instruction id, so std::sort yields the same
  // total order as stable_sort; comparing POD keys avoids rebuilding tuples and
  // avoids touching shared_ptr reference counts for every candidate.
  const auto seqLess = [](const IssueKey &a, const IssueKey &b) {
    if (a.seqKey != b.seqKey)
      return a.seqKey < b.seqKey;
    return a.instId < b.instId;
  };
  const auto isReversed = [&seqLess](const std::vector<IssueKey> &v) {
    for (std::size_t i = 1; i < v.size(); ++i)
      if (seqLess(v[i], v[i - 1]))
        return true;
    return false;
  };
  if (isReversed(rank0))
    std::sort(rank0.begin(), rank0.end(), seqLess);
  if (isReversed(rank1))
    std::sort(rank1.begin(), rank1.end(), seqLess);
  const int64_t issuedBefore = counts.total;
  for (int bucket = 0; bucket < 2; ++bucket) {
    if (counts.total >= cfg_.ubSlots) break;
    const std::vector<IssueKey> &bucketKeys = bucket == 0 ? rank0 : rank1;
    for (const IssueKey &c : bucketKeys) {
        UopPtr &u = waiting_[c.index];
        if (counts.total >= cfg_.ubSlots)
          break;
        if (u->ubTransferBytes <= 0)
          throw std::runtime_error("Missing Catalog UB transfer semantics");
        if (u->ubTransferBytes > cfg_.ubBandwidthBytesPerCycle)
          throw std::runtime_error(
              "UB bandwidth budget cannot fit one LSU transaction");
        if (issuedBytes_ + u->ubTransferBytes > cfg_.ubBandwidthBytesPerCycle)
          continue;
        if ((u->profile->opClass == "LOAD" && counts.loads >= cfg_.loadPorts) ||
            (u->profile->opClass == "STORE" && counts.stores >= cfg_.storePorts))
          continue;
        if (control_.blocks(u->streamSeq, u->profile->opClass)) {
          if (loggedBlocked.insert(u->instId).second) {
            u->blockedReason = "membar";
            trace_.log("blocked", *u, cycle);
            u->blockedReason.reset();
          }
          continue;
        }
        if (u->profile->opClass == "STORE" && !u->storeDependenciesResolved &&
            !u->producerOpForStore)
          continue;
        const auto &resource = u->profile->lsuResource;
        if (!resource.empty() && cycle < resourceReady_[resource])
          continue;
        if (!resource.empty())
          resourceReady_[resource] = cycle + u->profile->lsuIssueInterval;
        u->startCycle = cycle;
        u->doneCycle = cycle + u->profile->latency;
        u->state = UopState::running;
        running_.push_back(u);
        control_.notifyLsuStart(u->streamSeq, cycle);
        regs_.onStart(*u, cycle);
        if (u->profile->opClass == "LOAD")
          ++counts.loads;
        else {
          ++counts.stores;
          shq_.release(*u, cycle);
        }
        ++counts.total;
        issuedBytes_ += u->ubTransferBytes;
        trace_.log("start", *u, cycle);
    }
  }
  // I1: issued uops are already marked Running and nothing else in waiting_
  // can be Running (enqueue pushes fresh uops; issue() removes its own), so a
  // single stable compaction replaces the previous temporary id set and its
  // per-issue hash inserts/lookups. The relative order of the survivors is
  // preserved, which is what the FIFO-order detection relies on.
  if (counts.total != issuedBefore)
    waiting_.erase(
        std::remove_if(waiting_.begin(), waiting_.end(),
                       [](const auto &u) {
                         return u->state == UopState::running;
                       }),
        waiting_.end());
  if (counts.total != issuedBefore)
    compactPendingStores(false, 0);
  return counts;
}
std::vector<UopPtr> LSU::finish(int64_t cycle) {
  std::vector<UopPtr> done;
  std::size_t keep = 0;
  for (std::size_t i = 0; i < running_.size(); ++i) {
    UopPtr u = std::move(running_[i]);
    if (cycle >= *u->doneCycle) {
      u->state = UopState::done;
      done.push_back(std::move(u));
    } else {
      running_[keep++] = std::move(u);
    }
  }
  running_.resize(keep);
  // Preserve the previous keyed-container order for the completion batch.
  std::sort(done.begin(), done.end(), [](const UopPtr &a, const UopPtr &b) {
    return a->instId < b->instId;
  });
  return done;
}
} // namespace vfsim
