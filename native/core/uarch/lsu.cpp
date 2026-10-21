// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/uarch/lsu.h"
#include <algorithm>
namespace vfsim {
bool LSU::hasPendingBefore(int64_t seq, const std::string &cls) const {
  for (const auto &u : waiting_)
    if (u->streamSeq < seq && u->profile->opClass == cls)
      return true;
  for (const auto &[id, u] : running_)
    if (u->streamSeq < seq && u->profile->opClass == cls)
      return true;
  return false;
}
int64_t LSU::storeReady(Uop &u) const {
  u.storeDependenciesResolved = false;
  int64_t best = -1;
  u.producerOpForStore.reset();
  u.producerFormForStore.reset();
  u.producerStartForStore.reset();
  for (const auto &id : u.pregSrc)
    if (id) {
      auto p = regs_.producer(*id);
      if (!p && regs_.pending(*id))
        return 1000000000;
    }
  for (const auto &id : u.pregSrc)
    if (id) {
      auto p = regs_.producer(*id);
      if (!p || (p->kind != "COMPUTE" && p->kind != "LOAD"))
        continue;
      auto t = regs_.readyTimeForSource(*p, u);
      if (t > best) {
        best = t;
        u.producerOpForStore = p->op;
        u.producerFormForStore = p->form;
        u.producerStartForStore = p->startCycle;
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
  return std::max(best, u.lsqReadyCycle);
}
void LSU::updateReady(int64_t cycle, bool storesOnly) {
  for (auto &u : waiting_) {
    if (storesOnly && u->profile->opClass != "STORE")
      continue;
    if (u->profile->opClass == "LOAD") {
      u->readyCycle = std::max(regs_.readyCycle(*u), u->lsqReadyCycle);
      auto align = regs_.alignReady(*u);
      if (align)
        u->readyCycle = std::max(u->readyCycle, *align);
    } else
      u->readyCycle = storeReady(*u);
    u->state = cycle >= u->readyCycle ? "ready" : "blocked";
  }
}
LsuIssueCounts LSU::issue(int64_t cycle, LsuIssueCounts counts,
                          std::unordered_set<int64_t> &loggedBlocked) {
  if (budgetCycle_ != cycle) {
    budgetCycle_ = cycle;
    issuedBytes_ = 0;
  }
  if (cycle < db_.isaDefaults().vfStartupCost || counts.total >= cfg_.ubSlots)
    return counts;
  std::vector<UopPtr> candidates;
  for (const auto &u : waiting_)
    if (u->state == "ready")
      candidates.push_back(u);
  const std::string preferred =
      regs_.physicalVectorFree() < cfg_.lsuStorePriorityPregThreshold ? "STORE"
                                                                      : "LOAD";
  auto key = [&](const UopPtr &u) {
    return std::make_tuple(u->profile->opClass == preferred ? 0 : 1,
                           u->streamSeq < 0 ? u->instId : u->streamSeq,
                           u->instId);
  };
  std::stable_sort(
      candidates.begin(), candidates.end(),
      [&](const auto &a, const auto &b) { return key(a) < key(b); });
  std::unordered_set<int64_t> issued;
  for (auto &u : candidates) {
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
    u->state = "running";
    running_[u->instId] = u;
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
    issued.insert(u->instId);
  }
  waiting_.erase(
      std::remove_if(waiting_.begin(), waiting_.end(),
                     [&](const auto &u) { return issued.count(u->instId); }),
      waiting_.end());
  return counts;
}
std::vector<UopPtr> LSU::finish(int64_t cycle) {
  std::vector<UopPtr> done;
  for (auto it = running_.begin(); it != running_.end();)
    if (cycle >= *it->second->doneCycle) {
      it->second->state = "done";
      done.push_back(it->second);
      it = running_.erase(it);
    } else
      ++it;
  return done;
}
} // namespace vfsim
