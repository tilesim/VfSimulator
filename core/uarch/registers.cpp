// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/uarch/registers.h"
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
  const InstructionProfile *&cached = staticProfiles_[inst.pc];
  if (inst.pc >= 0 && cached)
    u.profile = cached;
  else {
    u.profile = &db_.inst(u.op, u.form, inst.opClass, inst.catalogMode);
    if (inst.pc >= 0)
      cached = u.profile;
  }
  if (traceEnabled_) {
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
  }
  u.streamSeq = inst.streamSeq;
  int vec = 0, pred = 0;
  if (inst.vectorDestCount >= 0) {
    vec = inst.vectorDestCount;
    pred = inst.predicateDestCount;
  } else
    for (const auto &d : inst.dst) {
      const auto storage = valueStorage_.storageOf(d);
      vec += storage == ValueStorageKind::Register;
      pred += storage == ValueStorageKind::PredicateRegister;
    }
  if (!cfg_.theoreticalLimitMode &&
      (vec > vector_.freeCount() || pred > predicate_.freeCount()))
    throw std::runtime_error(
        "Atomic rename precheck failed: physical register credit");
  std::vector<ProducerState *> released;
  u.pregSrc.reserve(inst.src.size());
  u.pregSrcGen.reserve(inst.src.size());
  u.sourceStates.reserve(inst.src.size());
  u.pregDst.reserve(inst.dst.size());
  u.destinationStates.reserve(inst.dst.size());
  for (size_t i = 0; i < inst.src.size(); ++i) {
    const auto &id = inst.src[i];
    const auto storage = inst.srcStorage.empty() ? valueStorage_.storageOf(id)
                                                 : inst.srcStorage[i];
    const bool pred = storage == ValueStorageKind::PredicateRegister;
    auto it = rat_.find(id);
    if ((storage != ValueStorageKind::Register && !pred) || it == rat_.end()) {
      if (pred)
        throw std::runtime_error("Predicate source has no renamed producer: " +
                                 id);
      u.pregSrc.push_back(std::nullopt);
      u.pregSrcGen.push_back(std::nullopt);
      u.sourceStates.push_back(nullptr);
      continue;
    }
    auto *state = it->second;
    u.pregSrc.push_back(state->physical);
    u.pregSrcGen.push_back(state->generation);
    u.sourceStates.push_back(state);
    ++state->consumers;
    state->releaseEligibleCycle.reset();
    releaseEligible_.erase(state->physical);
    if (i < inst.srcValueRelease.size() && inst.srcValueRelease[i]) {
      released.push_back(it->second);
      --it->second->ratBindings;
      rat_.erase(it);
    }
  }
  for (size_t i = 0; i < inst.dst.size(); ++i) {
    const auto &id = inst.dst[i];
    const auto storage = inst.dstStorage.empty() ? valueStorage_.storageOf(id)
                                                 : inst.dstStorage[i];
    const bool pred = storage == ValueStorageKind::PredicateRegister;
    if (storage != ValueStorageKind::Register && !pred)
      continue;
    const std::string physical =
        (pred ? predicate_ : vector_)
            .allocate(cfg_.theoreticalLimitMode,
                      cfg_.theoreticalLimitMode ? nextDynamicPregId_++ : -1);
    if (pred)
      predicateIds_.insert(physical);
    auto old = rat_.find(id);
    if (old != rat_.end()) {
      released.push_back(old->second);
      --old->second->ratBindings;
      rat_.erase(old);
    }
    const bool keep = i >= inst.dstValueKeep.size() || inst.dstValueKeep[i];
    auto &state = producerStates_[physical];
    if (keep)
      rat_[id] = &state;
    u.pregDst.push_back(physical);
    u.writebackBanks.insert(pred ? "predicate" : "vector");
    ++state.generation;
    state.producer.reset();
    state.pending = true;
    state.ratBindings = keep ? 1u : 0u;
    state.consumers = 0;
    state.physical = physical;
    state.predicate = pred;
    state.releaseEligibleCycle.reset();
    releaseEligible_.erase(state.physical);
    u.destinationStates.push_back(&state);
    if (pred)
      --visiblePredicate_;
    else if (cfg_.enableCreditVisibilityDelay)
      --visibleVector_;
  }
  bindAlignState(u, inst);
  for (auto *state : released)
    tryFree(*state, cycle);
  return ptr;
}
std::optional<ProducerInfo> Registers::producer(const std::string &id) const {
  const auto it = producerStates_.find(id);
  return it == producerStates_.end() ? std::nullopt : it->second.producer;
}
int64_t Registers::readyTimeForSource(const ProducerInfo &p,
                                      const Uop &u) const {
  const auto fwd = p.profile ? db_.forwardingCycles(*p.profile, *u.profile)
                             : db_.forwardingCycles(p.op, p.form, u.op, u.form);
  return p.startCycle +
         ((u.profile->opClass == "COMPUTE" && cfg_.enableIsuQueueModel &&
           !cfg_.theoreticalLimitLegacyForwarding)
              ? std::max<int64_t>(0, fwd - 1)
              : fwd);
}
int64_t Registers::readyCycle(const Uop &u) const {
  const int64_t queueReady =
      std::max(db_.isaDefaults().vfStartupCost, u.shqReadyCycle);
  int64_t sourceReady = u.sourceReadyCycle;
  bool resolved = true;
  for (size_t i = u.resolvedSourceCount; i < u.pregSrc.size(); ++i) {
    const auto *state = u.sourceStates[i];
    if (state) {
      if (state->producer)
        sourceReady =
            std::max(sourceReady, readyTimeForSource(*state->producer, u));
      else {
        resolved = false;
        if (state->pending)
          return 1000000000;
      }
    }
    // Remember only a fully resolved prefix. Unknown external sources must
    // still be checked again; unstarted producers never become cached times.
    if (resolved) {
      u.resolvedSourceCount = i + 1;
      u.sourceReadyCycle = sourceReady;
    }
  }
  return std::max(queueReady, sourceReady);
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
                                       u.pregSrcGen[i].value_or(0),
                                       u.sourceStates[i]});
}
void Registers::onStart(Uop &u, int64_t cycle) {
  if (u.alignProducerRecord)
    u.alignProducerRecord->startCycle = cycle;
  scheduleSrcReleaseFromStart(u);
  if (u.profile->opClass == "LOAD" || u.profile->opClass == "COMPUTE")
    for (auto *state : u.destinationStates) {
      state->producer =
          ProducerInfo{u.op, u.form, cycle, u.profile->opClass, u.profile};
      state->pending = false;
    }
}
void Registers::onDone(Uop &u, int64_t cycle) {
  if (u.alignProducerRecord)
    u.alignProducerRecord->doneCycle = u.doneCycle;
  for (auto *state : u.destinationStates)
    tryFree(*state, cycle);
}
bool Registers::tryFree(ProducerState &state, int64_t cycle) {
  const auto &id = state.physical;
  if (cfg_.theoreticalLimitMode)
    return false;
  auto &bank = state.predicate ? predicate_ : vector_;
  if (!bank.allocated(id))
    return false;
  if (state.ratBindings != 0)
    return false;
  if (state.pending || state.consumers > 0)
    return false;
  if (state.releaseEligibleCycle && cycle < *state.releaseEligibleCycle)
    return false;
  bank.release(id);
  state.producer.reset();
  state.consumers = 0;
  state.releaseEligibleCycle.reset();
  releaseEligible_.erase(state.physical);
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
    auto &state = *e.state;
    if (state.generation != e.gen) {
      if (cfg_.assertStartReleaseIntegrity)
        throw std::runtime_error("Stale source release generation");
      continue;
    }
    auto &count = state.consumers;
    if (count > 0)
      --count;
    if (count == 0) {
      state.releaseEligibleCycle = cycle;
      releaseEligible_[state.physical] = &state;
    }
    tryFree(state, cycle);
  }
  srcReleaseEvents_.erase(it);
}
void Registers::tryFreeEligible(int64_t cycle) {
  for (auto it = releaseEligible_.begin(); it != releaseEligible_.end();) {
    auto *state = (it++)->second;
    tryFree(*state, cycle);
  }
}
std::pair<int64_t, int64_t> Registers::updateVisibility(int64_t cycle) {
  const auto take = [cycle](auto &events) {
    const auto it = events.find(cycle);
    if (it == events.end())
      return int64_t{0};
    const auto count = it->second;
    events.erase(it);
    return count;
  };
  const auto v = take(visibleVectorEvents_), p = take(visiblePredicateEvents_);
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
