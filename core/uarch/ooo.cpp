// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/uarch/ooo.h"
#include <algorithm>
#include <cmath>
namespace vfsim {
namespace {
std::unordered_set<std::string> sources(const Uop &u) {
  std::unordered_set<std::string> ids;
  for (const auto &p : u.pregSrc)
    if (p)
      ids.insert(*p);
  return ids;
}
bool hazard(const std::unordered_set<std::string> &used,
            const std::unordered_set<std::string> &src) {
  for (const auto &id : src)
    if (used.count(id))
      return true;
  return false;
}
} // namespace
int OoO::selectRoundRobin(const std::vector<int> &candidates, int &ptr) {
  for (int off = 0; off < cfg_.issuePorts; ++off) {
    int p = (ptr + off) % cfg_.issuePorts;
    if (std::find(candidates.begin(), candidates.end(), p) !=
        candidates.end()) {
      ptr = (p + 1) % cfg_.issuePorts;
      return p;
    }
  }
  throw std::logic_error("No OoO port candidate");
}
int64_t OoO::exu0Pressure(size_t index) const {
  int64_t seen = 0, pressure = 0;
  const auto &q = shq_.entries();
  for (size_t i = index + 1; i < q.size() && seen < cfg_.exu0ReserveLookahead;
       ++i, ++seen)
    pressure += q[i]->profile->dispatchExu == "EXU0_ONLY";
  return pressure >= std::max<int64_t>(1, cfg_.exu0ReserveMinCount) ? pressure
                                                                    : 0;
}
void OoO::dispatch(int64_t cycle) {
  const auto &policy = cfg_.shqExqDispatchPolicy;
  const bool fuFifo = policy == "fu_round_robin_fifo" ||
                      policy == "fu_rr_fifo" || policy == "fu_round_robin" ||
                      policy == "fu_round_robin_exu0_reserve";
  std::unordered_set<std::string> usedSources, blockedFu;
  std::vector<int64_t> admitted(cfg_.issuePorts);
  int count = 0;
  size_t index = 0;
  std::unordered_set<int64_t> selected;
  auto &q = shq_.entries();
  for (auto it = q.begin(); it != q.end(); ++index) {
    auto u = *it;
    const auto src = sources(*u);
    if (count >= cfg_.issuePorts)
      break;
    if (u->state != "ready" || (fuFifo && blockedFu.count(u->profile->fuType))) {
      ++it;
      continue;
    }
    if (cfg_.enforceSameCycleSrcHazard && !cfg_.theoreticalLimitMode &&
        hazard(usedSources, src)) {
      if (fuFifo)
        blockedFu.insert(u->profile->fuType);
      ++it;
      continue;
    }
    std::vector<int> candidates;
    for (int p = 0; p < cfg_.issuePorts; ++p)
      if (exu_.legalPort(*u, p) && admitted[p] < cfg_.shqToExqPortPerCycle &&
          exq_.canAccept(p))
        candidates.push_back(p);
    if (candidates.empty()) {
      if (fuFifo && u->profile->dispatchExu != "EXU0_ONLY")
        blockedFu.insert(u->profile->fuType);
      ++it;
      continue;
    }
    if (policy == "fu_round_robin_exu0_reserve" &&
        u->profile->dispatchExu != "EXU0_ONLY" && candidates.size() > 1) {
      const auto pressure = exu0Pressure(index);
      if (pressure > 0) {
        double best = 1e30;
        std::vector<int> balanced;
        for (int p : candidates) {
          double other = 0;
          for (int j = 1; j < cfg_.issuePorts; ++j)
            other += exq_.admissionOccupancy(j) + (p == j);
          const double error =
              std::abs(other / (cfg_.issuePorts - 1) -
                       (exq_.admissionOccupancy(0) + (p == 0)) - pressure);
          if (error < best) {
            best = error;
            balanced.clear();
          }
          if (error == best)
            balanced.push_back(p);
        }
        candidates = std::move(balanced);
      }
    }
    const auto recv = cycle + cfg_.exqRecvDelay;
    int port = candidates.front();
    if (fuFifo)
      port = selectRoundRobin(candidates, fuRr_[u->profile->fuType]);
    else if (cfg_.shqExqStaticRr)
      port = selectRoundRobin(candidates, rr_);
    else
      for (int p : candidates)
        if (std::make_tuple(exq_.predictIssueCycle(p, *u, recv),
                            exq_.admissionOccupancy(p), p) <
            std::make_tuple(exq_.predictIssueCycle(port, *u, recv),
                            exq_.admissionOccupancy(port), port))
          port = p;
    exq_.enqueue(u, port, recv, exq_.predictIssueCycle(port, *u, recv));
    shq_.release(*u, cycle);
    ++admitted[port];
    ++count;
    usedSources.insert(src.begin(), src.end());
    selected.insert(u->instId);
    ++it;
  }
  q.erase(
      std::remove_if(q.begin(), q.end(),
                     [&](const auto &u) { return selected.count(u->instId); }),
      q.end());
}
void OoO::issueDirect(int64_t cycle) {
  std::unordered_set<std::string> usedSources;
  std::vector<bool> used(cfg_.issuePorts);
  int count = 0;
  auto &q = shq_.entries();
  for (auto it = q.begin(); it != q.end();) {
    auto u = *it;
    const auto src = sources(*u);
    if (count >= cfg_.issuePorts)
      break;
    if (u->state != "ready" ||
        (cfg_.enforceSameCycleSrcHazard && !cfg_.theoreticalLimitMode &&
         hazard(usedSources, src))) {
      ++it;
      continue;
    }
    auto p = exu_.pickPort(*u, cycle, used);
    if (!p) {
      ++it;
      continue;
    }
    exu_.start(u, *p, cycle, false);
    regs_.onStart(*u, cycle);
    trace_.log("start", *u, cycle);
    used[*p] = true;
    usedSources.insert(src.begin(), src.end());
    ++count;
    it = q.erase(it);
  }
}
} // namespace vfsim
