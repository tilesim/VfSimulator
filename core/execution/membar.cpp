// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/membar.h"

#include <algorithm>
#include <cctype>
#include <stdexcept>

namespace vfsim {
namespace {

std::string upper(std::string value) {
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return value;
}

std::string normalizeBarrier(std::string value) {
  value = upper(std::move(value));
  const auto dot = value.rfind('.');
  if (dot != std::string::npos)
    value = value.substr(dot + 1);
  return value;
}

} // namespace

MembarController::MembarController(const ParamDB *db,
                                   std::optional<MembarTimingConfig> timing,
                                   int64_t issueFloor)
    : db_(db), timing_(std::move(timing)), issueFloor_(issueFloor) {
  if (timing_) {
    if (timing_->admissionDelay < 0)
      throw std::invalid_argument("negative membar admission delay");
    for (const char *cls : {"LOAD", "STORE"})
      if (timing_->startFeedback.at(cls) < 0)
        throw std::invalid_argument("negative membar LSU feedback");
    for (const auto &[name, r] : timing_->directions) {
      if (r.releaseLatency < 0 || r.retireLatency < r.releaseLatency ||
          r.consumerDelay < 0 || r.nextIssueDelay < 1)
        throw std::invalid_argument("invalid membar direction timing");
    }
  }
}

void MembarController::acceptMembar(const DynamicInst &inst, int64_t cycle) {
  const std::string barrier =
      normalizeBarrier(inst.barrier.empty() ? "VST_VLD" : inst.barrier);
  Barrier item;
  item.streamSeq = inst.streamSeq;
  item.pc = inst.pc;
  item.barrier = barrier;
  if (barrier == "VST_VLD") {
    item.waitClass = "STORE";
    item.blockClass = "LOAD";
  } else if (barrier == "VLD_VST") {
    item.waitClass = "LOAD";
    item.blockClass = "STORE";
  } else if (barrier == "VST_VST") {
    item.waitClass = item.blockClass = "STORE";
  } else if (barrier == "VV_ALL") {
    item.waitClass = item.blockClass = "ALL";
  } else {
    if (db_ != nullptr) {
      db_->recordWarning("unsupported_membar_type",
                         {{"pc", inst.pc},
                          {"barrier", barrier.empty() ? inst.barrier : barrier},
                          {"stream_seq", inst.streamSeq}});
    }
    return;
  }
  if (timing_) {
    if (!timing_->directions.count(barrier) && db_)
      db_->recordWarning("unmeasured_membar_timing", {{"barrier", barrier}});
    item.availableCycle =
        std::max(issueFloor_, cycle + timing_->admissionDelay);
    item.previous = lastBarrier_;
    const auto cls = lastBarrier_ ? lastBarrier_->blockClass : "STORE";
    const auto it = segment_.find(cls);
    if (cls == "ALL") {
      for (const auto &[kind, progress] : segment_)
        item.predecessors.push_back(progress);
    } else if (it != segment_.end())
      item.predecessors.push_back(it->second);
    segment_.clear();
  }
  auto barrierPtr = std::make_shared<Barrier>(std::move(item));
  if (timing_)
    lastBarrier_ = barrierPtr;
  barriers_.push_back(std::move(barrierPtr));
}

void MembarController::observeInstruction(const DynamicInst &inst) {
  if (!timing_ || db_ == nullptr)
    return;
  const std::string &cls = inst.opClass;
  if (cls != "LOAD" && cls != "STORE")
    return;
  auto &progress = segment_[cls];
  if (!progress) {
    progress = std::make_shared<LsuProgress>();
    progress->opClass = cls;
  }
  ++progress->pending;
  pendingStarts_[inst.streamSeq] = progress;
}

void MembarController::notifyLsuStart(int64_t streamSeq, int64_t cycle) {
  const auto it = pendingStarts_.find(streamSeq);
  if (it == pendingStarts_.end())
    return;
  auto &p = *it->second;
  --p.pending;
  p.startCycle = std::max(cycle, p.startCycle.value_or(0));
  pendingStarts_.erase(it);
}

void MembarController::log(const std::string &event, const Barrier &b) {
  history_.push_back({cycle_, b.streamSeq, b.pc, event, b.barrier});
}

void MembarController::update(
    const std::function<bool(int64_t, const std::string &)> &hasPendingBefore,
    int64_t cycle, const std::function<bool(int64_t)> &hasPendingDispatch) {
  cycle_ = cycle;
  auto pending = [&](const Barrier &b) {
    return b.waitClass == "ALL" ? hasPendingBefore(b.streamSeq, "LOAD") ||
                                      hasPendingBefore(b.streamSeq, "STORE")
                                : hasPendingBefore(b.streamSeq, b.waitClass);
  };
  for (auto &ptr : barriers_) {
    auto &b = *ptr;
    if (!timing_) {
      b.released = !pending(b);
      continue;
    }
    if (!timing_->directions.count(b.barrier)) {
      if ((!b.previous ||
           (b.previous->retireCycle && cycle >= *b.previous->retireCycle)) &&
          !pending(b) &&
          !(hasPendingDispatch && hasPendingDispatch(b.streamSeq))) {
        b.issueCycle = b.releaseCycle = b.retireCycle = cycle;
        b.retired = b.released = true;
        lastRetireCycle_ = std::max(lastRetireCycle_, cycle);
        log("sync_release", b);
      }
      continue;
    }
    const auto &rule = timing_->directions.at(b.barrier);
    if (!b.issueCycle) {
      int64_t ready = b.availableCycle;
      if (b.previous) {
        if (!b.previous->retireCycle)
          continue;
        ready = std::max(ready, *b.previous->retireCycle + rule.nextIssueDelay);
      }
      bool predecessorPending = false;
      for (const auto &progress : b.predecessors) {
        const auto &p = *progress;
        if (p.pending || !p.startCycle) {
          predecessorPending = true;
          break;
        }
        ready = std::max(ready,
                         *p.startCycle + timing_->startFeedback.at(p.opClass));
      }
      if (predecessorPending)
        continue;
      if (cycle < ready ||
          (hasPendingDispatch && hasPendingDispatch(b.streamSeq)))
        continue;
      b.issueCycle = cycle;
      b.previous.reset();
      b.predecessors.clear();
      log("issue", b);
    }
    if (!b.releaseCycle && cycle >= *b.issueCycle + rule.releaseLatency &&
        !pending(b)) {
      b.releaseCycle = cycle;
      b.retireCycle = cycle + rule.retireLatency - rule.releaseLatency;
      log("sync_release", b);
    }
    if (b.retireCycle && cycle >= *b.retireCycle && !b.retired) {
      b.retired = true;
      lastRetireCycle_ = std::max(lastRetireCycle_, *b.retireCycle);
      log("retire", b);
    }
    b.released = b.retired && b.releaseCycle &&
                 cycle >= *b.releaseCycle + rule.consumerDelay;
  }
  barriers_.erase(
      std::remove_if(barriers_.begin(), barriers_.end(),
                     [](const auto &barrier) { return barrier->released; }),
      barriers_.end());
}

bool MembarController::blocks(int64_t streamSeq, const std::string &cls) const {
  for (const auto &ptr : barriers_) {
    const auto &barrier = *ptr;
    if (barrier.released)
      continue;
    if (streamSeq > barrier.streamSeq &&
        ((barrier.blockClass == "ALL" && (cls == "LOAD" || cls == "STORE")) ||
         cls == barrier.blockClass)) {
      const auto delay =
          timing_ && timing_->directions.count(barrier.barrier)
              ? timing_->directions.at(barrier.barrier).consumerDelay
              : 0;
      if (!timing_ || !barrier.releaseCycle ||
          cycle_ < *barrier.releaseCycle + delay)
        return true;
    }
  }
  return false;
}

} // namespace vfsim
