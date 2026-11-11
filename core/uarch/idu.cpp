// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/uarch/idu.h"
#include <algorithm>
namespace vfsim {
std::vector<const DynamicInst *>
IDU::dispatchBorrowed(int64_t cycle, const IDUDispatchBudget &budget) {
  if (window_.empty())
    return {};

  const int64_t dispatchStartGate =
      std::max<int64_t>(0, vfStartupCost_ - cfg_.iduDispatchStartAdvance);
  if (cycle < dispatchStartGate)
    return {};

  int64_t credits =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freePreg;
  int64_t predicateCredits = budget.theoreticalLimitMode ? 1000000000000000000LL
                                                         : budget.freePredicate;
  int64_t shqQueueFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeShqQueue;
  int64_t lsqFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeLsq;
  int64_t shqFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeShq;
  const int64_t issueBudget = budget.theoreticalLimitMode
                                  ? static_cast<int64_t>(window_.size())
                                  : budget.issueBudget;

  std::vector<const DynamicInst *> dispatched;
  std::vector<std::vector<AddressDependency>> addressDependencies;
  dispatched.reserve(static_cast<size_t>(
      std::max<int64_t>(0, std::min<int64_t>(issueBudget, window_.size()))));

  if (shqQueueFree <= 0 && lsqFree <= 0)
    return {};
  if (cfg_.globalShqPregGate && (credits <= 0 || shqFree <= 0))
    return {};

  for (const auto *pointer : window_) {
    const auto &inst = *pointer;
    if (static_cast<int64_t>(dispatched.size()) >= issueBudget)
      break;

    if (!loops_.canDispatch(inst, cycle))
      break;
    const bool isLoad = inst.opClass == "LOAD";
    const bool isStore = inst.opClass == "STORE";
    if (isLoad) {
      if (lsqFree <= 0)
        break;
    } else if (isStore) {
      if (lsqFree <= 0 || shqFree <= 0)
        break;
    } else {
      if (shqQueueFree <= 0 || shqFree <= 0)
        break;
    }

    int64_t dstCount = inst.vectorDestCount;
    int64_t predicateCount = inst.predicateDestCount;
    if (dstCount < 0) {
      dstCount = predicateCount = 0;
      for (const auto &d : inst.dst) {
        if (valueStorage_.isRegister(d))
          ++dstCount;
      }
      for (const auto &d : inst.dst)
        predicateCount += valueStorage_.isPredicate(d);
    }
    if (credits < dstCount || predicateCredits < predicateCount) {
      if (traceEnabled_) {
        IDUDispatchRecord record;
        record.cycle = cycle;
        record.instId = inst.instId;
        record.op = inst.op;
        record.staticInstructionId = inst.staticInstructionId;
        record.iterationPath = inst.iterationPath;
        record.streamSeq = inst.streamSeq;
        record.event = "blocked";
        record.blockedReason =
            credits < dstCount ? "vector_credit" : "predicate_credit";
        record.vreg = credits;
        record.predicatePhysFree = predicateCredits;
        resourceBlockLog_.push_back(std::move(record));
      }
      break;
    }

    const auto dependencies =
        traceEnabled_ ? addressStates_.dependencies(inst.memoryAccesses)
                      : std::vector<AddressDependency>{};
    if (!addressStates_.canDispatch(inst.memoryAccesses, cycle)) {
      if (traceEnabled_) {
        IDUDispatchRecord record;
        record.cycle = cycle;
        record.instId = inst.instId;
        record.op = inst.op;
        record.staticInstructionId = inst.staticInstructionId;
        record.iterationPath = inst.iterationPath;
        record.streamSeq = inst.streamSeq;
        record.addressDependencies = dependencies;
        record.event = "blocked";
        addressBlockLog_.push_back(std::move(record));
      }
      break;
    }

    dispatched.push_back(pointer);
    addressStates_.notifyDispatch(inst.instId, inst.memoryAccesses, cycle);
    if (traceEnabled_)
      addressDependencies.push_back(dependencies);
    credits -= dstCount;
    predicateCredits -= predicateCount;
    if ((isLoad || isStore)) {
      --lsqFree;
      if (isStore)
        --shqFree;
    } else if ((!isLoad && !isStore)) {
      --shqQueueFree;
      --shqFree;
    }
  }

  credits =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freePreg;
  predicateCredits = budget.theoreticalLimitMode ? 1000000000000000000LL
                                                 : budget.freePredicate;
  shqQueueFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeShqQueue;
  lsqFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeLsq;
  shqFree =
      budget.theoreticalLimitMode ? 1000000000000000000LL : budget.freeShq;
  size_t dispatchIndex = 0;
  for (const auto *pointer : dispatched) {
    const auto &inst = *pointer;
    window_.pop_front();
    if (traceEnabled_) {
      dispatchLog_.push_back(IDUDispatchRecord{
          cycle,
          inst.instId,
          inst.op,
          inst.dst,
          inst.src,
          inst.topBlockId,
          credits,
          shqQueueFree,
          lsqFree,
          shqFree,
          inst.staticInstructionId,
          inst.iterationPath,
          inst.streamSeq,
          addressDependencies[dispatchIndex++],
      });
      dispatchLog_.back().predicatePhysFree = predicateCredits;
    }
    loops_.notifyDispatch(inst, cycle);
  }

  return dispatched;
}

std::vector<DynamicInst> IDU::dispatch(int64_t cycle,
                                       const IDUDispatchBudget &budget) {
  const auto pointers = dispatchBorrowed(cycle, budget);
  std::vector<DynamicInst> result;
  result.reserve(pointers.size());
  for (const auto *inst : pointers) {
    result.push_back(*inst);
    if (!owned_.empty() && inst == &owned_.front())
      owned_.pop_front();
  }
  return result;
}

} // namespace vfsim
