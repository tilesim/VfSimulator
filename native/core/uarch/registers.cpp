// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/uarch/registers.h"
#include <algorithm>
#include <stdexcept>
namespace vfsim {
Registers::Registers(const UarchConfig &cfg, const ParamDB &db,
                     const std::unordered_map<std::string, ValueInfo> &values)
    : cfg_(cfg), db_(db), valueStorage_(values), vector_(cfg.vregNum, "p"),
      predicate_(cfg.physicalPredicateRegisters, "pred"),
      nextDynamicPregId_(cfg.vregNum), visibleVector_(cfg.vregNum),
      visiblePredicate_(cfg.physicalPredicateRegisters) {}
int64_t Registers::freeVector() const {
  return cfg_.theoreticalLimitMode
             ? 1000000000000000000LL
             : (cfg_.enableCreditVisibilityDelay ? visibleVector_
                                                 : vector_.freeCount());
}
int64_t Registers::freePredicate() const {
  return cfg_.theoreticalLimitMode
             ? 1000000000000000000LL
             : (cfg_.enableCreditVisibilityDelay ? visiblePredicate_
                                                 : predicate_.freeCount());
}
UopPtr Registers::rename(const DynamicInst &inst, int64_t cycle) {
  auto ptr = std::make_shared<Uop>();
  auto &u = *ptr;
  u.instId = inst.instId;
  u.op = inst.op;
  u.form = inst.form;
  u.profile = &db_.inst(u.op, u.form, inst.opClass, inst.catalogMode);
  u.src = inst.src;
  u.dst = inst.dst;
  u.topBlockId = inst.topBlockId;
  u.iterStack = inst.iterStack;
  u.isLastInTopBlock = inst.isLastInTopBlock;
  u.streamSeq = inst.streamSeq;
  u.staticInstructionId = inst.staticInstructionId;
  u.iterationPath = inst.iterationPath;
  u.srcValueInstances = inst.srcValueInstances;
  u.dstValueInstances = inst.dstValueInstances;
  int vec = 0, pred = 0;
  for (const auto &d : u.dst) {
    vec += valueStorage_.isRegister(d);
    pred += valueStorage_.isPredicate(d);
  }
  if (!cfg_.theoreticalLimitMode &&
      (vec > vector_.freeCount() || pred > predicate_.freeCount()))
    throw std::runtime_error(
        "Atomic rename precheck failed: physical register credit");
  std::vector<std::string> released;
  for (size_t i = 0; i < u.src.size(); ++i) {
    const auto &id = u.src[i];
    auto it = rat_.find(id);
    if (!valueStorage_.isRenameable(id) || it == rat_.end()) {
      if (valueStorage_.isPredicate(id))
        throw std::runtime_error("Predicate source has no renamed producer: " +
                                 id);
      u.pregSrc.push_back(std::nullopt);
      u.pregSrcGen.push_back(std::nullopt);
      continue;
    }
    u.pregSrc.push_back(it->second);
    u.pregSrcGen.push_back(pregGeneration_[it->second]);
    if (i < inst.srcValueRelease.size() && inst.srcValueRelease[i]) {
      released.push_back(it->second);
      rat_.erase(it);
    }
  }
  for (const auto &preg : u.pregSrc)
    if (preg) {
      ++pregConsumerCount_[*preg];
      pregReleaseEligibleCycle_.erase(*preg);
    }
  for (size_t i = 0; i < u.dst.size(); ++i) {
    const auto &id = u.dst[i];
    if (!valueStorage_.isRenameable(id)) {
      continue;
    }
    const bool pred = valueStorage_.isPredicate(id);
    const std::string physical =
        (pred ? predicate_ : vector_)
            .allocate(cfg_.theoreticalLimitMode,
                      cfg_.theoreticalLimitMode ? nextDynamicPregId_++ : -1);
    if (pred)
      predicateIds_.insert(physical);
    auto old = rat_.find(id);
    if (old != rat_.end()) {
      released.push_back(old->second);
      rat_.erase(old);
    }
    if (i >= inst.dstValueKeep.size() || inst.dstValueKeep[i])
      rat_[id] = physical;
    u.pregDst.push_back(physical);
    ++pregGeneration_[physical];
    pregConsumerCount_[physical] = 0;
    pregPending_.insert(physical);
    pregReleaseEligibleCycle_.erase(physical);
    if (pred)
      --visiblePredicate_;
    else if (cfg_.enableCreditVisibilityDelay)
      --visibleVector_;
  }
  bindAlignState(u, inst);
  for (const auto &physical : released)
    tryFree(physical, cycle);
  return ptr;
}
std::optional<ProducerInfo> Registers::producer(const std::string &id) const {
  auto it = pregProducer_.find(id);
  return it == pregProducer_.end() ? std::nullopt
                                   : std::optional<ProducerInfo>{it->second};
}
int64_t Registers::readyTimeForSource(const ProducerInfo &p,
                                      const Uop &u) const {
  const auto fwd = db_.forwardingCycles(p.op, p.form, u.op, u.form);
  return p.startCycle +
         ((u.profile->opClass == "COMPUTE" && cfg_.enableIsuQueueModel &&
           !cfg_.theoreticalLimitLegacyForwarding)
              ? std::max<int64_t>(0, fwd - 1)
              : fwd);
}
int64_t Registers::readyCycle(const Uop &u) const {
  int64_t ready = std::max(db_.isaDefaults().vfStartupCost, u.shqReadyCycle);
  for (const auto &id : u.pregSrc)
    if (id) {
      auto p = producer(*id);
      if (p)
        ready = std::max(ready, readyTimeForSource(*p, u));
      else if (pending(*id))
        return 1000000000;
    }
  return ready;
}
std::optional<int64_t> Registers::alignReady(const Uop &u) const {
  std::vector<std::shared_ptr<AlignProducerRecord>> producers;
  if (u.alignPreviousProducer)
    producers.push_back(u.alignPreviousProducer);
  else if (u.alignStateOperation == "consume" && u.alignGeneration)
    producers = u.alignGeneration->producers;
  else
    return std::nullopt;
  int64_t ready = u.lsqReadyCycle;
  for (const auto &p : producers) {
    if (!p->startCycle)
      return 1000000000;
    ready = std::max(ready, *p->startCycle +
                                db_.inst(p->op, p->form).alignStateLatency);
  }
  return ready;
}
void Registers::scheduleSrcReleaseFromStart(const Uop &u) {
  if (!u.startCycle)
    return;
  auto &events =
      srcReleaseEvents_[*u.startCycle + cfg_.consumerReleaseStartOffset];
  for (size_t i = 0; i < u.pregSrc.size(); ++i)
    if (u.pregSrc[i])
      events.push_back(SrcReleaseEvent{u.instId, *u.pregSrc[i],
                                       u.pregSrcGen[i].value_or(0)});
}
void Registers::onStart(Uop &u, int64_t cycle) {
  if (u.alignProducerRecord)
    u.alignProducerRecord->startCycle = cycle;
  scheduleSrcReleaseFromStart(u);
  if (u.profile->opClass == "LOAD" || u.profile->opClass == "COMPUTE")
    for (const auto &id : u.pregDst)
      if (!id.empty()) {
        pregProducer_[id] =
            ProducerInfo{u.op, u.form, cycle, u.profile->opClass};
        pregPending_.erase(id);
      }
}
void Registers::onDone(Uop &u, int64_t cycle) {
  if (u.alignProducerRecord)
    u.alignProducerRecord->doneCycle = u.doneCycle;
  for (const auto &id : u.pregDst)
    if (!id.empty())
      tryFree(id, cycle);
}
bool Registers::tryFree(const std::string &id, int64_t cycle) {
  if (cfg_.theoreticalLimitMode)
    return false;
  auto &bank = predicateIds_.count(id) ? predicate_ : vector_;
  if (!bank.allocated(id))
    return false;
  if (std::any_of(rat_.begin(), rat_.end(),
                  [&](const auto &x) { return x.second == id; }))
    return false;
  if (pregPending_.count(id) || pregConsumerCount_[id] > 0)
    return false;
  auto eligible = pregReleaseEligibleCycle_.find(id);
  if (eligible != pregReleaseEligibleCycle_.end() && cycle < eligible->second)
    return false;
  bank.release(id);
  pregProducer_.erase(id);
  pregConsumerCount_.erase(id);
  pregReleaseEligibleCycle_.erase(id);
  const auto delay =
      cfg_.enableCreditVisibilityDelay ? cfg_.iduVisiblePregDelay : 0;
  if (&bank == &predicate_) {
    if (delay > 0)
      ++visiblePredicateEvents_[cycle + delay];
    else {
      ++visiblePredicate_;
      ++predicateDelta_;
    }
  } else if (cfg_.enableCreditVisibilityDelay) {
    if (delay > 0)
      ++visibleVectorEvents_[cycle + delay];
    else {
      ++visibleVector_;
      ++vectorDelta_;
    }
  }
  return true;
}
void Registers::runReleaseEvents(int64_t cycle) {
  auto it = srcReleaseEvents_.find(cycle);
  if (it == srcReleaseEvents_.end())
    return;
  for (const auto &e : it->second) {
    if (pregGeneration_[e.preg] != e.gen) {
      if (cfg_.assertStartReleaseIntegrity)
        throw std::runtime_error("Stale source release generation");
      continue;
    }
    auto &count = pregConsumerCount_[e.preg];
    if (count > 0)
      --count;
    if (count == 0)
      pregReleaseEligibleCycle_[e.preg] = cycle;
    tryFree(e.preg, cycle);
  }
  srcReleaseEvents_.erase(it);
}
void Registers::tryFreeEligible(int64_t cycle) {
  std::vector<std::string> ids;
  for (const auto &[id, t] : pregReleaseEligibleCycle_)
    ids.push_back(id);
  for (const auto &id : ids)
    tryFree(id, cycle);
}
std::pair<int64_t, int64_t> Registers::updateVisibility(int64_t cycle) {
  const auto v = visibleVectorEvents_[cycle],
             p = visiblePredicateEvents_[cycle];
  visibleVectorEvents_.erase(cycle);
  visiblePredicateEvents_.erase(cycle);
  visibleVector_ += v;
  visiblePredicate_ += p;
  auto result = std::make_pair(vectorDelta_ + v, predicateDelta_ + p);
  vectorDelta_ = predicateDelta_ = 0;
  return result;
}
void Registers::bindAlignState(Uop &u, const DynamicInst &inst) {
  if (inst.alignStateOperation == "load_init" ||
      inst.alignStateOperation == "load_use") {
    if (inst.alignStateId.empty())
      throw std::runtime_error("Load align state ID is required");
    u.alignStateOperation = inst.alignStateOperation;
    u.alignStateId = inst.alignStateId;
    if (inst.alignStateOperation == "load_init") {
      auto record = std::make_shared<AlignProducerRecord>();
      record->instId = u.instId;
      record->streamSeq = u.streamSeq;
      record->op = u.op;
      record->form = u.form;
      alignLoadInitializers_[inst.alignStateId] = record;
      u.alignProducerRecord = record;
    } else {
      auto found = alignLoadInitializers_.find(inst.alignStateId);
      if (found == alignLoadInitializers_.end())
        throw std::runtime_error("Uninitialized load align state: " +
                                 inst.alignStateId);
      u.alignPreviousProducer = found->second;
      auto record = std::make_shared<AlignProducerRecord>();
      record->instId = u.instId;
      record->streamSeq = u.streamSeq;
      record->op = u.op;
      record->form = u.form;
      alignLoadInitializers_[inst.alignStateId] = record;
      u.alignProducerRecord = record;
    }
    return;
  }
  if ((inst.alignStateOperation != "append" &&
       inst.alignStateOperation != "consume") ||
      inst.alignStateId.empty())
    return;

  auto found = alignStateOpen_.find(inst.alignStateId);
  std::shared_ptr<AlignGeneration> generation;
  if (found == alignStateOpen_.end()) {
    const int64_t generationId = alignStateNextGeneration_[inst.alignStateId]++;
    generation = std::make_shared<AlignGeneration>();
    generation->stateId = inst.alignStateId;
    generation->generationId = generationId;
    alignStateOpen_[inst.alignStateId] = generation;
  } else {
    generation = found->second;
  }

  u.alignStateOperation = inst.alignStateOperation;
  u.alignStateId = inst.alignStateId;
  u.alignGeneration = generation;
  if (inst.alignStateOperation == "append") {
    if (!generation->producers.empty())
      u.alignPreviousProducer = generation->producers.back();
    auto record = std::make_shared<AlignProducerRecord>();
    record->instId = u.instId;
    record->streamSeq = u.streamSeq;
    record->op = u.op;
    record->form = u.form;
    generation->producers.push_back(record);
    u.alignProducerRecord = std::move(record);
    return;
  }

  generation->consumerInstId = u.instId;
  auto next = std::make_shared<AlignGeneration>();
  next->stateId = inst.alignStateId;
  next->generationId = alignStateNextGeneration_[inst.alignStateId]++;
  alignStateOpen_[inst.alignStateId] = std::move(next);
}

} // namespace vfsim
