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
  vloopTrace_.push_back(
      VloopTraceRecord{topBlockId, "top_block", {}, startCycle});
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
    const std::string key0 = makeKey(topBlockId, "loop0", {});
    vloopStart_[key0] = topVloopStart;
    bodyOpenTime_[key0] = topVloopStart + vloopToDispatchDelay_;
  }
  if (depth >= 2 && bounds[0] > 0) {
    const std::string key1 = makeKey(topBlockId, "loop1", {0});
    vloopStart_[key1] = topVloopStart + nestedVloopInitialStartGap_;
    bodyOpenTime_[key1] = vloopStart_[key1] + vloopToDispatchDelay_;
  }
  if (depth >= 3 && bounds[0] > 0 && bounds[1] > 0) {
    const std::string key2 = makeKey(topBlockId, "loop2", {0, 0});
    vloopStart_[key2] = topVloopStart + 2 * nestedVloopInitialStartGap_;
    bodyOpenTime_[key2] = vloopStart_[key2] + vloopToDispatchDelay_;
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

std::string LoopControl::makeKey(int64_t topBlockId, const std::string &loopId,
                                 const std::vector<int64_t> &iters) const {
  return std::to_string(topBlockId) + "|" + loopId + "|" + joinInts(iters);
}

std::optional<std::string> LoopControl::normalizeBlockKey(
    const std::pair<std::string, std::vector<int64_t>> &raw,
    int64_t topBlockId) const {
  if (raw.first.empty())
    return std::nullopt;
  return makeKey(topBlockId, raw.first, raw.second);
}

std::optional<std::string>
LoopControl::currentInnerBlockKey(const DynamicInst &inst) const {
  const int64_t topBlockId = inst.topBlockId;
  const auto &bk = inst.blockKeyByLevel;
  if (!bk.empty()) {
    return normalizeBlockKey(bk.back(), topBlockId);
  }

  return std::nullopt;
}

void LoopControl::updateLastDispatch(const DynamicInst &inst, int64_t cycle) {
  const int64_t topBlockId = inst.topBlockId;
  const auto &bk = inst.blockKeyByLevel;
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
      const std::string curKey = makeKey(topBlockId, "loop1", {i});
      const int64_t endCy =
          lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (i + 1 < I) {
        const int64_t prevStart =
            vloopStart_.count(curKey) ? vloopStart_[curKey] : endCy;
        const int64_t nextStart =
            std::max<int64_t>(endCy, prevStart + loop1MinFeedbackGap_);
        const std::string nextKey = makeKey(topBlockId, "loop1", {i + 1});
        vloopStart_[nextKey] = nextStart;
        bodyOpenTime_[nextKey] = nextStart + vloopToDispatchDelay_;
        vloopTrace_.push_back(
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
      const std::string curKey = makeKey(topBlockId, "loop2", {i, j});
      const int64_t endCy =
          lastDispatchTime_.count(curKey) ? lastDispatchTime_[curKey] : cycle;
      if (j + 1 < M) {
        const std::string nextKey = makeKey(topBlockId, "loop2", {i, j + 1});
        vloopStart_[nextKey] = endCy;
        bodyOpenTime_[nextKey] = endCy + vloopToDispatchDelay_;
        vloopTrace_.push_back(
            VloopTraceRecord{topBlockId, "loop2", {i, j + 1}, endCy});
      }
    }

    if (std::find(inst.blockEndLevels.begin(), inst.blockEndLevels.end(), 1) !=
            inst.blockEndLevels.end() &&
        iterStack.size() >= 1) {
      const int64_t i = iterStack[0];
      const int64_t K = bounds[0];
      const int64_t M = bounds[1];
      const std::string curKey = makeKey(topBlockId, "loop1", {i});
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
        vloopTrace_.push_back(
            VloopTraceRecord{topBlockId, "loop1", {i + 1}, nextLoop1Start});
        if (M > 0) {
          const int64_t childStart =
              nextLoop1Start + nestedVloopInitialStartGap_;
          vloopStart_[makeKey(topBlockId, "loop2", {i + 1, 0})] = childStart;
          bodyOpenTime_[makeKey(topBlockId, "loop2", {i + 1, 0})] =
              childStart + vloopToDispatchDelay_;
          vloopTrace_.push_back(
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

  if (!cfg_.theoreticalLimitVloopOnly) {
    const auto innerKey = currentInnerBlockKey(inst);
    if (innerKey) {
      const auto openIt = bodyOpenTime_.find(*innerKey);
      if (openIt == bodyOpenTime_.end() || cycle < openIt->second)
        return false;
    }
  }

  const int64_t iterId = inst.iterStack.empty() ? 0 : inst.iterStack.back();
  if (!cfg_.theoreticalLimitMode && !cfg_.theoreticalLimitVloopOnly) {
    const auto innerKey = currentInnerBlockKey(inst);
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
