// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/ControlUnit.h"

#include "native/ISATraits.h"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <stdexcept>

namespace vfsim {
namespace {

std::string upper(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
    return static_cast<char>(std::toupper(c));
  });
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

ControlUnit::ControlUnit(const ParamDB *db, std::optional<MembarTimingConfig> timing,
                         int64_t issueFloor)
    : db_(db), timing_(std::move(timing)), issueFloor_(issueFloor) {
  if (timing_) {
    if (timing_->admissionDelay < 0)
      throw std::invalid_argument("negative membar admission delay");
    for (const char *cls : {"LOAD", "STORE"})
      if (timing_->startFeedback.at(cls) < 0)
        throw std::invalid_argument("negative membar LSU feedback");
    for (const char *name : {"VLD_VST", "VST_VLD"}) {
      const auto &r = timing_->directions.at(name);
      if (r.releaseLatency < 0 || r.retireLatency < r.releaseLatency ||
          r.consumerDelay < 0 || r.nextIssueDelay < 1)
        throw std::invalid_argument("invalid membar direction timing");
    }
  }
}

void ControlUnit::acceptMembar(const DynamicInst &inst, int64_t cycle) {
  const std::string barrier = normalizeBarrier(inst.barrier.empty() ? "VST_VLD" : inst.barrier);
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
  } else {
    if (db_ != nullptr) {
      db_->recordWarning(
          "unsupported_membar_type",
          {{"pc", std::to_string(inst.pc)},
           {"barrier", barrier.empty() ? inst.barrier : barrier},
           {"stream_seq", std::to_string(inst.streamSeq)}});
    }
    return;
  }
  if (timing_) {
    item.availableCycle = std::max(issueFloor_, cycle + timing_->admissionDelay);
    item.previous = lastBarrier_;
    const auto cls = lastBarrier_ ? lastBarrier_->blockClass : "STORE";
    const auto it = segment_.find(cls);
    if (it != segment_.end())
      item.predecessors = it->second;
    segment_.clear();
  }
  auto barrierPtr = std::make_shared<Barrier>(std::move(item));
  if (timing_)
    lastBarrier_ = barrierPtr;
  barriers_.push_back(std::move(barrierPtr));
}

void ControlUnit::observeInstruction(const DynamicInst &inst, const std::string &dtype) {
  if (!timing_ || db_ == nullptr)
    return;
  const auto form = inst.form.empty() ? dtype : inst.form;
  const std::string cls = isLoadOp(*db_, inst.op, form) ? "LOAD" :
                          isStoreOp(*db_, inst.op, form) ? "STORE" : "";
  if (cls.empty())
    return;
  auto &progress = segment_[cls];
  if (!progress) {
    progress = std::make_shared<LsuProgress>();
    progress->opClass = cls;
  }
  ++progress->pending;
  pendingStarts_[inst.streamSeq] = progress;
}

void ControlUnit::notifyLsuStart(int64_t streamSeq, int64_t cycle) {
  const auto it = pendingStarts_.find(streamSeq);
  if (it == pendingStarts_.end())
    return;
  auto &p = *it->second;
  --p.pending;
  p.startCycle = std::max(cycle, p.startCycle.value_or(0));
  pendingStarts_.erase(it);
}

void ControlUnit::log(const std::string &event, const Barrier &b) {
  history_.push_back({cycle_, b.streamSeq, b.pc, event, b.barrier});
}

void ControlUnit::dumpHistory(const std::string &path) const {
  if (!timing_)
    return;
  std::ofstream out(path);
  if (!out)
    throw std::runtime_error("Cannot write membar history: " + path);
  out << "[\n";
  for (size_t i = 0; i < history_.size(); ++i) {
    const auto &e = history_[i];
    // Event and barrier names are closed internal enums, not input strings.
    out << (i ? ",\n" : "") << "{\"cy\":" << e.cy
        << ",\"stream_seq\":" << e.streamSeq << ",\"pc\":" << e.pc
        << ",\"event\":\"" << e.event << "\",\"barrier\":\"" << e.barrier << "\"}";
  }
  out << "\n]\n";
}

void ControlUnit::update(
    const std::function<bool(int64_t, const std::string &)> &hasPendingBefore,
    int64_t cycle, const std::function<bool(int64_t)> &hasPendingDispatch) {
  cycle_ = cycle;
  for (auto &ptr : barriers_) {
    auto &b = *ptr;
    if (!timing_) {
      b.released = !hasPendingBefore(b.streamSeq, b.waitClass);
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
      if (b.predecessors) {
        const auto &p = *b.predecessors;
        if (p.pending || !p.startCycle)
          continue;
        ready = std::max(ready, *p.startCycle + timing_->startFeedback.at(p.opClass));
      }
      if (cycle < ready || (hasPendingDispatch && hasPendingDispatch(b.streamSeq)))
        continue;
      b.issueCycle = cycle;
      b.previous.reset();
      b.predecessors.reset();
      log("issue", b);
    }
    if (!b.releaseCycle && cycle >= *b.issueCycle + rule.releaseLatency &&
        !hasPendingBefore(b.streamSeq, b.waitClass)) {
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

bool ControlUnit::blocks(const DynamicInst &inst, const ParamDB &db,
                         const std::string &dtype) const {
  if (inst.type != "inst")
    return false;
  std::string cls;
  if (isLoadOp(db, inst.op, inst.form.empty() ? dtype : inst.form))
    cls = "LOAD";
  else if (isStoreOp(db, inst.op, inst.form.empty() ? dtype : inst.form))
    cls = "STORE";
  else
    return false;

  for (const auto &ptr : barriers_) {
    const auto &barrier = *ptr;
    if (barrier.released)
      continue;
    if (inst.streamSeq > barrier.streamSeq && cls == barrier.blockClass) {
      if (!timing_ || !barrier.releaseCycle ||
          cycle_ < *barrier.releaseCycle + timing_->directions.at(barrier.barrier).consumerDelay)
        return true;
    }
  }
  return false;
}

} // namespace vfsim
