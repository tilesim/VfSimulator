// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/uarch/exu.h"
#include <algorithm>
#include <stdexcept>
namespace vfsim {
EXU::EXU(const UarchConfig &cfg, const ParamDB &db)
    : cfg_(cfg), db_(db), lastPort_(cfg.issuePorts), lastFu_(cfg.issuePorts),
      selfIssueReady_(cfg.issuePorts), completionCycles_(cfg.issuePorts),
      inflight_(cfg.issuePorts) {
  for (auto &fu : lastFu_) {
    fu["ALU"] = {};
    fu["SFU"] = {};
  }
  for (auto &banks : completionCycles_) {
    banks["vector"] = {};
    banks["predicate"] = {};
  }
}
bool EXU::legalPort(const Uop &u, int port) const {
  return port >= 0 && port < cfg_.issuePorts &&
         std::find(u.profile->allowedExuPorts.begin(),
                   u.profile->allowedExuPorts.end(), port) !=
             u.profile->allowedExuPorts.end();
}
bool EXU::canAccept(int port, bool queued) const {
  return !queued || cfg_.exqIssueInflightCapPerPort <= 0 ||
         inflight_.at(port) < cfg_.exqIssueInflightCapPerPort;
}
int64_t EXU::nextSelfIssueCycle(const Uop &u, int port) const {
  const auto &ready = selfIssueReady_.at(port);
  const auto found = ready.find({u.op, u.form});
  return found == ready.end() ? -1000000000 : found->second;
}
int64_t EXU::nextCompletionIssueCycle(const Uop &u, int port, int64_t cycle) const {
  const auto &reserved = completionCycles_.at(port);
  while (std::any_of(u.writebackBanks.begin(), u.writebackBanks.end(),
                     [&](const auto &bank) {
                       const auto &slots = reserved.at(bank);
                       return slots.find(cycle + u.profile->latency) != slots.end();
                     }))
    ++cycle;
  return cycle;
}
int64_t EXU::nextIssueCycle(const Uop &u, int port, bool perFu,
                          int64_t notBefore) const {
  const auto &last = perFu ? lastFu_.at(port).at(u.profile->fuType) : lastPort_.at(port);
  const auto adjacentReady = last.cycle +
      (last.op.empty() ? 1 : db_.initiationInterval(last.op, last.form, u.op, u.form));
  return nextCompletionIssueCycle(
      u, port, std::max({notBefore, adjacentReady, nextSelfIssueCycle(u, port)}));
}
bool EXU::canIssue(const Uop &u, int port, int64_t cycle, bool queued) const {
  return legalPort(u, port) && canAccept(port, queued) &&
         cycle >= nextIssueCycle(u, port, !queued && !cfg_.enableCrossFuIi, cycle);
}
std::optional<int> EXU::pickPort(const Uop &u, int64_t cycle,
                                 const std::vector<bool> &used) {
  std::vector<int> candidates;
  for (int p = 0; p < cfg_.issuePorts; ++p)
    if (!used[p] && canIssue(u, p, cycle, false))
      candidates.push_back(p);
  if (candidates.empty())
    return std::nullopt;
  if (!cfg_.enableExqGreedyBalance)
    return candidates.front();
  auto minLoad = inflight_[candidates.front()];
  for (int p : candidates)
    minLoad = std::min(minLoad, inflight_[p]);
  for (int off = 0; off < cfg_.issuePorts; ++off) {
    int p = (rr_ + off) % cfg_.issuePorts;
    if (inflight_[p] == minLoad &&
        std::find(candidates.begin(), candidates.end(), p) !=
            candidates.end()) {
      rr_ = (p + 1) % cfg_.issuePorts;
      return p;
    }
  }
  return candidates.front();
}
void EXU::start(const UopPtr &u, int port, int64_t cycle, bool queued) {
  const auto doneCycle = cycle + u->profile->latency;
  auto &reserved = completionCycles_.at(port);
  if (nextCompletionIssueCycle(*u, port, cycle) != cycle)
    throw std::runtime_error("EXU completion cycle is already reserved");
  u->startCycle = cycle;
  u->doneCycle = doneCycle;
  u->exuPort = port;
  u->state = "running";
  running_.emplace(u->instId, u);
  for (const auto &bank : u->writebackBanks)
    reserved.at(bank).insert(doneCycle);
  ++inflight_[port];
  selfIssueReady_[port][{u->op, u->form}] =
      cycle + db_.initiationInterval(u->op, u->form, u->op, u->form);
  if (queued || cfg_.enableCrossFuIi)
    lastPort_[port] = {cycle, u->op, u->form};
  if (queued || !cfg_.enableCrossFuIi)
    lastFu_[port][u->profile->fuType] = {cycle, u->op, u->form};
}
std::vector<UopPtr> EXU::finish(int64_t cycle) {
  std::vector<UopPtr> done;
  for (auto it = running_.begin(); it != running_.end();)
    if (cycle >= *it->second->doneCycle) {
      auto u = it->second;
      for (const auto &bank : u->writebackBanks)
        completionCycles_[u->exuPort].at(bank).erase(*u->doneCycle);
      --inflight_[u->exuPort];
      u->exuPort = -1;
      u->state = "done";
      done.push_back(u);
      it = running_.erase(it);
    } else
      ++it;
  return done;
}
} // namespace vfsim
