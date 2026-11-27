// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/loop_control.h"
#include <algorithm>
#include <sstream>
namespace vfsim {
namespace {
std::string joinInts(const std::vector<int64_t> &values) {
  std::ostringstream out;
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      out << ',';
    out << values[i];
  }
  return out.str();
}
} // namespace
LoopControl::LoopControl(const UarchConfig &cfg, const PreparedProgram &p)
    : cfg_(cfg), vloopToDispatchDelay_(cfg.vloopToDispatchDelay),
      initialTopBlockVloopStartCycle_(cfg.initialTopBlockVloopStartCycle),
      nestedVloopInitialStartGap_(cfg.nestedVloopInitialStartGap),
      loop1MinFeedbackGap_(cfg.loop1MinFeedbackGap),
      innermostIterDispatchStride_(cfg.innermostIterDispatchStride),
      totalTopBlocks_(p.totalTopBlocks),
      topBlockLoopBounds_(p.topBlockLoopBounds),
      emptyTopBlocks_(p.emptyTopBlocks) {
  initVloopStarts();
}
void LoopControl::setTopBlockVloop(int64_t topBlockId, int64_t startCycle) {
  if (topBlockVloopStart_.count(topBlockId))
    return;
  topBlockVloopStart_[topBlockId] = startCycle;
  topBlockBodyOpenTime_[topBlockId] = startCycle + vloopToDispatchDelay_;
}

void LoopControl::initTopBlockNestedStarts(int64_t topBlockId,
                                           int64_t topVloopStart) {
  const auto it = topBlockLoopBounds_.find(static_cast<int>(topBlockId));
  const std::vector<int64_t> &bounds =
      it == topBlockLoopBounds_.end() ? std::vector<int64_t>{} : it->second;
  const int64_t depth = static_cast<int64_t>(bounds.size());
  if (depth <= 0)
    return;

  setTopBlockVloop(topBlockId, topVloopStart);
  if (depth >= 1) {
    const auto key0 = makeKey(topBlockId, "loop0", {});
    vloopStart_[key0] = topVloopStart;
    bodyOpenTime_[key0] = topVloopStart + vloopToDispatchDelay_;
    if (traceRecordsEnabled_)
      pushTrace({topBlockId, "loop0", {}, topVloopStart});
  }
  if (depth >= 2 && bounds[0] > 0) {
    const auto key1 = makeKey(topBlockId, "loop1", {0});
    vloopStart_[key1] = topVloopStart + nestedVloopInitialStartGap_;
    bodyOpenTime_[key1] = vloopStart_[key1] + vloopToDispatchDelay_;
    if (traceRecordsEnabled_)
      pushTrace({topBlockId, "loop1", {0}, vloopStart_[key1]});
  }
  if (depth >= 3 && bounds[0] > 0 && bounds[1] > 0) {
    const auto key2 = makeKey(topBlockId, "loop2", {0, 0});
    vloopStart_[key2] = topVloopStart + 2 * nestedVloopInitialStartGap_;
    bodyOpenTime_[key2] = vloopStart_[key2] + vloopToDispatchDelay_;
    if (traceRecordsEnabled_)
      pushTrace({topBlockId, "loop2", {0, 0}, vloopStart_[key2]});
  }
}




// Exact key retirement. blockEndLevels lists the closing depth indices, and
// schedulingKeys holds the identity of each level of this event, so the closed
// key is removed by identity rather than reconstructed from iteration numbers.
void LoopControl::retireClosedKeys(const DynamicInst &inst) {
  const size_t levels = inst.schedulingKeys.size();
  for (const int64_t level : inst.blockEndLevels) {
    if (level < 0 || static_cast<size_t>(level) >= levels)
      continue;
    // Level 0 closes with the top block itself; leave it while instructions of
    // the same block can still follow this one.
    if (level == 0 && !inst.isLastInTopBlock)
      continue;
    const SchedulingKey &key = inst.schedulingKeys[static_cast<size_t>(level)];
    vloopStart_.erase(key);
    bodyOpenTime_.erase(key);
    lastDispatchTime_.erase(key);
    blockBaseCycle_.erase(key);
  }
}

void LoopControl::initVloopStarts() {
  if (totalTopBlocks_ <= 0)
    return;
  const auto firstTop = nextNonemptyTopBlock(0);
  if (!firstTop)
    return;
  setTopBlockVloop(*firstTop, initialTopBlockVloopStartCycle_);
  initTopBlockNestedStarts(*firstTop, initialTopBlockVloopStartCycle_);
}

std::optional<int64_t> LoopControl::nextNonemptyTopBlock(int64_t start) const {
  for (int64_t topBlockId = std::max<int64_t>(0, start);
       topBlockId < totalTopBlocks_; ++topBlockId) {
    if (!emptyTopBlocks_.count(topBlockId))
      return topBlockId;
  }
  return std::nullopt;
}

SchedulingKey LoopControl::makeKey(int64_t topBlockId, const std::string &loopId,
                                 const std::vector<int64_t> &iters) const {
  SchedulingKey key;
  key.topBlockId = topBlockId;
  // Only the canonical spellings are typed; everything else keeps the exact
  // historical text identity (loop00/loop01 etc. must not alias).
  const bool canonical = (loopId == "loop0" && iters.empty()) ||
                         (loopId == "loop1" && iters.size() == 1) ||
                         (loopId == "loop2" && iters.size() == 2);
  if (canonical) {
    key.depth = static_cast<int32_t>(iters.size());
    key.c0 = iters.empty() ? -1 : iters[0];
    key.c1 = iters.size() > 1 ? iters[1] : -1;
    return key;
  }
  key.slow = std::to_string(topBlockId) + "|" + loopId + "|" + joinInts(iters);
  return key;
}

SchedulingKey LoopControl::keyFromNormalized(const std::string &text) const {
  // Legacy normalised text is "<top>|loop<N>|<i,j>". Only the canonical shapes
  // are typed, and only when regenerating the canonical text reproduces the
  // input byte for byte; anything else keeps the exact historical string
  // identity. This rejects "01|loop1|02", "1|loop0|", trailing garbage and
  // numeric overflow instead of aliasing them.
  SchedulingKey key;
  key.slow = text;
  const size_t first = text.find('|');
  if (first == std::string::npos || first == 0)
    return key;
  const size_t second = text.find('|', first + 1);
  if (second == std::string::npos)
    return key;
  const auto parseNumber = [](const std::string &piece, int64_t *out) {
    if (piece.empty())
      return false;
    size_t i = 0;
    bool negative = false;
    if (piece[0] == '-') {
      negative = true;
      i = 1;
      if (piece.size() == 1)
        return false;
    }
    int64_t value = 0;
    for (; i < piece.size(); ++i) {
      const char c = piece[i];
      if (c < '0' || c > '9')
        return false;
      if (value > (std::numeric_limits<int64_t>::max() - (c - '0')) / 10)
        return false;
      value = value * 10 + (c - '0');
    }
    *out = negative ? -value : value;
    return true;
  };
  int64_t top = 0;
  if (!parseNumber(text.substr(0, first), &top) || top < 0)
    return key;
  const std::string loopId = text.substr(first + 1, second - first - 1);
  std::vector<int64_t> iters;
  const std::string coords = text.substr(second + 1);
  if (!coords.empty()) {
    size_t pos = 0;
    while (true) {
      const size_t comma = coords.find(',', pos);
      const std::string piece =
          comma == std::string::npos ? coords.substr(pos)
                                     : coords.substr(pos, comma - pos);
      int64_t value = 0;
      if (!parseNumber(piece, &value))
        return key;
      iters.push_back(value);
      if (comma == std::string::npos)
        break;
      pos = comma + 1;
    }
  }
  const bool canonical = (loopId == "loop0" && iters.empty()) ||
                         (loopId == "loop1" && iters.size() == 1) ||
                         (loopId == "loop2" && iters.size() == 2);
  if (!canonical)
    return key;
  const std::string regenerated =
      std::to_string(top) + "|" + loopId + "|" + joinInts(iters);
  if (regenerated != text)
    return key;
  SchedulingKey typed;
  typed.topBlockId = top;
  typed.depth = static_cast<int32_t>(iters.size());
  typed.c0 = iters.empty() ? -1 : iters[0];
  typed.c1 = iters.size() > 1 ? iters[1] : -1;
  return typed;
}

std::optional<SchedulingKey> LoopControl::normalizeBlockKey(
    const std::pair<std::string, std::vector<int64_t>> &raw,
    int64_t topBlockId) const {
  if (raw.first.empty())
    return std::nullopt;
  return makeKey(topBlockId, raw.first, raw.second);
}

std::optional<SchedulingKey>
LoopControl::currentInnerBlockKey(const DynamicInst &inst) const {
  const int64_t topBlockId = inst.topBlockId;
  if (!inst.schedulingKeys.empty())
    return inst.schedulingKeys.back();
  if (!inst.normalizedBlockKeys.empty())
    return keyFromNormalized(inst.normalizedBlockKeys.back());
  const auto &bk = inst.blockKeyByLevel;
  if (!bk.empty()) {
    return normalizeBlockKey(bk.back(), topBlockId);
  }
  return std::nullopt;
}

void LoopControl::updateLastDispatch(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto &bk = inst.blockKeyByLevel;
  if (!inst.schedulingKeys.empty()) {
    for (const auto &key : inst.schedulingKeys)
      lastDispatchTime_[key] = cycle;
    return;
  }
  if (!inst.normalizedBlockKeys.empty()) {
    for (const auto &key : inst.normalizedBlockKeys)
      lastDispatchTime_[keyFromNormalized(key)] = cycle;
    return;
  }
  if (!bk.empty()) {
    for (const auto &raw : bk) {
      if (auto key = normalizeBlockKey(raw, topBlockId))
        lastDispatchTime_[*key] = cycle;
    }
    return;
  }
}

void LoopControl::triggerNextVloops(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto boundsIt = topBlockLoopBounds_.find(static_cast<int>(topBlockId));
  const std::vector<int64_t> &bounds = boundsIt == topBlockLoopBounds_.end()
                                           ? std::vector<int64_t>{}
                                           : boundsIt->second;
  const int64_t depth = static_cast<int64_t>(bounds.size());

  if (inst.isLastInTopBlock) {
    const auto nextTop = nextNonemptyTopBlock(topBlockId + 1);
    if (nextTop && !topBlockVloopStart_.count(*nextTop)) {
      setTopBlockVloop(*nextTop, cycle);
      initTopBlockNestedStarts(*nextTop, cycle);
    }
  }

  if (depth <= 0 || inst.blockEndLevels.empty())
    return;

  const auto &iterStack = inst.iterStack;
  if (depth == 1)
    return;

  if (depth == 2) {
    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 1) !=
            inst.blockEndLevels.end() &&
        iterStack.size() >= 1) {
      const int64_t i = iterStack[0];
      const int64_t I = bounds[0];
      const auto curKey = makeKey(topBlockId, "loop1", {i});
      const int64_t endCy =
          lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (i + 1 < I) {
        const int64_t prevStart =
            vloopStart_.count(curKey) ? vloopStart_[curKey] : endCy;
        const int64_t nextStart =
            std::max<int64_t>(endCy, prevStart + loop1MinFeedbackGap_);
        const auto nextKey = makeKey(topBlockId, "loop1", {i + 1});
        vloopStart_[nextKey] = nextStart;
        bodyOpenTime_[nextKey] = nextStart + vloopToDispatchDelay_;
        if (traceRecordsEnabled_)
          pushTrace(
            VloopTraceRecord{topBlockId, "loop1", {i + 1}, nextStart});
      }
    }
    return;
  }

  if (depth == 3) {
    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 2) !=
            inst.blockEndLevels.end() &&
        iterStack.size() >= 2) {
      const int64_t i = iterStack[0];
      const int64_t j = iterStack[1];
      const int64_t M = bounds[1];
      const auto curKey = makeKey(topBlockId, "loop2", {i, j});
      const int64_t endCy =
          lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (j + 1 < M) {
        const auto nextKey = makeKey(topBlockId, "loop2", {i, j + 1});
        vloopStart_[nextKey] = endCy;
        bodyOpenTime_[nextKey] = endCy + vloopToDispatchDelay_;
        if (traceRecordsEnabled_)
          pushTrace(
            VloopTraceRecord{topBlockId, "loop2", {i, j + 1}, endCy});
      }
    }

    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 1) !=
            inst.blockEndLevels.end() &&
        iterStack.size() >= 1) {
      const int64_t i = iterStack[0];
      const int64_t K = bounds[0];
      const int64_t M = bounds[1];
      const auto curKey = makeKey(topBlockId, "loop1", {i});
      const int64_t endCy =
          lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (i + 1 < K) {
        const int64_t prevStart =
            vloopStart_.count(curKey) ? vloopStart_[curKey] : endCy;
        const int64_t nextLoop1Start =
            std::max<int64_t>(endCy, prevStart + loop1MinFeedbackGap_);
        vloopStart_[makeKey(topBlockId, "loop1", {i + 1})] = nextLoop1Start;
        bodyOpenTime_[makeKey(topBlockId, "loop1", {i + 1})] =
            nextLoop1Start + vloopToDispatchDelay_;
        if (traceRecordsEnabled_)
          pushTrace(
            VloopTraceRecord{topBlockId, "loop1", {i + 1}, nextLoop1Start});
        if (M > 0) {
          const int64_t childStart =
              nextLoop1Start + nestedVloopInitialStartGap_;
          vloopStart_[makeKey(topBlockId, "loop2", {i + 1, 0})] = childStart;
          bodyOpenTime_[makeKey(topBlockId, "loop2", {i + 1, 0})] =
              childStart + vloopToDispatchDelay_;
          if (traceRecordsEnabled_)
            pushTrace(
              VloopTraceRecord{topBlockId, "loop2", {i + 1, 0}, childStart});
        }
      }
    }
  }
}

bool LoopControl::canDispatch(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto topOpenIt = topBlockBodyOpenTime_.find(topBlockId);
  if (topOpenIt == topBlockBodyOpenTime_.end() || cycle < topOpenIt->second)
    return false;

  std::optional<SchedulingKey> fallback;
  const SchedulingKey *innerKey = nullptr;
  if (!inst.schedulingKeys.empty())
    innerKey = &inst.schedulingKeys.back();
  else {
    fallback = currentInnerBlockKey(inst);
    if (fallback)
      innerKey = &*fallback;
  }
  if (!cfg_.theoreticalLimitVloopOnly) {
    if (innerKey) {
      const auto openIt = bodyOpenTime_.find(*innerKey);
      if (openIt == bodyOpenTime_.end() || cycle < openIt->second)
        return false;
    }
  }

  const int64_t iterId = inst.iterStack.empty() ? 0 : inst.iterStack.back();
  if (!cfg_.theoreticalLimitMode && !cfg_.theoreticalLimitVloopOnly) {
    if (innerKey) {
      if (iterId == 0 && !blockBaseCycle_.count(*innerKey))
        blockBaseCycle_[*innerKey] = cycle;
      const auto baseIt = blockBaseCycle_.find(*innerKey);
      if (baseIt == blockBaseCycle_.end())
        return false;
      if (cycle < baseIt->second + iterId * innermostIterDispatchStride_)
        return false;
    }
  }

  return true;
}
} // namespace vfsim
