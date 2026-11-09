// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/uarch/exq.h"
#include <algorithm>
namespace vfsim {
namespace {
std::string queueName(const Uop &u) {
  return u.profile->executionResource.empty() ? u.profile->fuType : u.profile->executionResource;
}
}
EXQ::EXQ(const UarchConfig &cfg, const ParamDB &db, Registers &regs, EXU &exu)
    : cfg_(cfg), db_(db), regs_(regs), exu_(exu), queues_(cfg.issuePorts) {
  for (auto &q : queues_) {
    for (const auto &name : db.bundle().executionResources)
      q[name] = {};
    q["ALU"] = {};
    q["SFU"] = {};
  }
}
int64_t EXQ::occupancy(int port) const {
  int64_t n = 0;
  for (const auto &[fu, q] : queues_.at(port))
    n += q.size();
  return n;
}
int64_t EXQ::admissionOccupancy(int port) const {
  return occupancy(port) +
         (cfg_.exqCapacityCountsInflight ? exu_.inflight(port) : 0);
}
int64_t EXQ::predictIssueCycle(int port, const Uop &u, int64_t recv) const {
  const auto &q = queues_.at(port).at(queueName(u));
  int64_t predicted = std::max(recv, exu_.nextIssueCycle(u, port, u.profile->executionResource.empty()));
  if (!q.empty()) {
    const auto &previous = *q.back();
    predicted = std::max(
        predicted,
        previous.exqPredIssue +
            db_.initiationInterval(previous.op, previous.form, u.op, u.form));
    predicted = std::max(predicted, exu_.nextResourceIssueCycle(u, port));
    for (auto pending = q.rbegin(); pending != q.rend(); ++pending) {
      if ((*pending)->op == u.op && (*pending)->form == u.form) {
        predicted = std::max(
            predicted, (*pending)->exqPredIssue +
                           db_.initiationInterval(u.op, u.form, u.op, u.form));
        break;
      }
    }
  }
  const auto target = u.op + "." + (u.profile->resolvedForm.empty() ? u.form : u.profile->resolvedForm);
  for (const auto &[name, waiting] : queues_.at(port))
    for (const auto &pending : waiting) {
      const auto &overrides = db_.issueOverrides(pending->op, pending->form);
      if (const auto found = overrides.find(target); found != overrides.end())
        predicted = std::max(predicted, pending->exqPredIssue+found->second);
    }
  if (cfg_.predictExqIssueWithCrossFu)
    predicted = std::max(predicted, exu_.nextIssueCycle(u, port));
  return exu_.nextCompletionIssueCycle(u, port, predicted);
}
void EXQ::enqueue(const UopPtr &u, int port, int64_t recv, int64_t predicted) {
  u->exuPort = port;
  u->exqRecvCycle = recv;
  u->exqPredIssue = predicted;
  u->state = "exq_wait";
  queues_.at(port).at(queueName(*u)).push_back(u);
}
UopPtr EXQ::popReady(int port, int64_t cycle) {
  UopPtr best;
  std::tuple<int64_t, int64_t, int64_t> key;
  for (auto &[fu, q] : queues_.at(port)) {
    if (q.empty())
      continue;
    const auto &u = q.front();
    const auto ready = regs_.readyCycle(*u);
    if (u->exqRecvCycle > cycle || ready > cycle ||
        !exu_.canIssue(*u, port, cycle, true))
      continue;
    auto current = std::make_tuple(ready, u->exqRecvCycle, u->instId);
    if (!best || current < key) {
      best = u;
      key = current;
    }
  }
  if (best)
    queues_[port][queueName(*best)].pop_front();
  return best;
}
} // namespace vfsim
