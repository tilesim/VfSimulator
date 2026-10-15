// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/uarch/exq.h"
#include <algorithm>
namespace vfsim {
EXQ::EXQ(const UarchConfig &cfg, const ParamDB &db, Registers &regs, EXU &exu)
    : cfg_(cfg), db_(db), regs_(regs), exu_(exu), queues_(cfg.issuePorts) {
  for (auto &q : queues_) {
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
  const auto &q = queues_.at(port).at(u.profile->fuType);
  int64_t predicted = recv;
  if (!q.empty()) {
    const auto &previous = *q.back();
    predicted = std::max(
        predicted,
        previous.exqPredIssue +
            db_.initiationInterval(previous.op, previous.form, u.op, u.form));
  } else
    predicted = std::max(predicted, exu_.nextIssueCycle(u, port, true));
  if (cfg_.predictExqIssueWithCrossFu)
    predicted = std::max(predicted, exu_.nextIssueCycle(u, port));
  return predicted;
}
void EXQ::enqueue(const UopPtr &u, int port, int64_t recv, int64_t predicted) {
  u->exuPort = port;
  u->exqRecvCycle = recv;
  u->exqPredIssue = predicted;
  u->state = "exq_wait";
  queues_.at(port).at(u->profile->fuType).push_back(u);
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
    queues_[port][best->profile->fuType].pop_front();
  return best;
}
} // namespace vfsim
