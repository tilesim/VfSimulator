// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/engine.h"
#include "api/common/instruction_catalog.h"
#include <algorithm>
#include <limits>
#include <memory>
namespace vfsim {
Engine::Engine(const PreparedProgram &p, const UarchConfig &cfg,
               const ParamDB &db)
    : program_(p), cfg_(cfg), db_(db), registers_(cfg, db, p.values), shq_(cfg),
      exu_(cfg, db), exq_(cfg, db, registers_, exu_), trace_(registers_),
      control_(&db, cfg.membarTiming, db.isaDefaults().vfStartupCost),
      lsu_(cfg, db, registers_, shq_, control_, trace_), loops_(cfg, p),
      idu_(cfg, db, loops_, registers_.storage()),
      ooo_(cfg, registers_, shq_, exq_, exu_, trace_),
      ifu_(p.instructions, IFU::Borrowed{}),
      vectorCredit_(registers_.freeVector()),
      predicateCredit_(registers_.freePredicate()),
      shqCredit_(shq_.freeCredit()) {}
Engine::Reservation Engine::reservation(const DynamicInst &inst) const {
  Reservation counts{};
  if (inst.vectorDestCount >= 0) {
    counts[0] = inst.vectorDestCount;
    counts[1] = inst.predicateDestCount;
  } else
    for (const auto &d : inst.dst) {
      counts[0] += registers_.storage().isRegister(d);
      counts[1] += registers_.storage().isPredicate(d);
    }
  counts[2] = inst.opClassText() == "COMPUTE";
  counts[3] = inst.opClassText() == "LOAD" || inst.opClassText() == "STORE";
  counts[4] = inst.opClassText() == "COMPUTE" ||
               inst.opClassText() == "STORE";
  return counts;
}
void Engine::admit(const DynamicInst &inst) {
  if (ifu_.hasProvider()) {
    ++streamIssued_;
  }
  auto u = registers_.rename(inst, cycle_);
  if (u->profile->opClass == "LOAD" || u->profile->opClass == "STORE") {
    const auto &catalog = defaultInstructionCatalog();
    const auto *spec = inst.catalogModeText().empty()
                           ? catalog.lookup(u->op)
                           : catalog.lookupMemoryMode(u->op, inst.catalogModeText());
    if (!spec || spec->ubTransferBytes <= 0)
      throw std::runtime_error("Missing Catalog UB transfer semantics: " +
                               u->op);
    u->ubTransferBytes = spec->ubTransferBytes;
    lsu_.enqueue(u, cycle_);
  } else
    shq_.enqueue(u, cycle_);
  shq_.reserve(*u);
  ++activeCount_;
}
bool Engine::hasPendingDispatch(int64_t seq) const {
  for (const auto &i : idu_.borrowedWindow())
    if (i->streamSeq < seq)
      return true;
  for (const auto &[c, i] : pipe_)
    if (i->streamSeq < seq)
      return true;
  return false;
}
bool Engine::hasPending(int64_t seq, const std::string &cls) const {
  // The scored lowering leaves the class in a borrowed view, so read it through
  // the accessor; the raw member is empty for streamed events and a raw compare
  // would let a LOAD/STORE barrier release while its class is still in flight.
  for (const auto &i : idu_.borrowedWindow())
    if (i->streamSeq < seq && i->opClassText() == cls)
      return true;
  for (const auto &[c, i] : pipe_)
    if (i->streamSeq < seq && i->opClassText() == cls)
      return true;
  return lsu_.hasPendingBefore(seq, cls);
}
DynamicInst *Engine::acquireStreamSlot() {
  size_t index = 0;
  if (streamFreeSlots_.empty())
    reclaimStreamArena();
  if (!streamFreeSlots_.empty()) {
    index = streamFreeSlots_.back();
    streamFreeSlots_.pop_back();
  } else {
    streamSlots_.push_back(std::make_unique<DynamicInst>());
    index = streamSlots_.size() - 1;
  }
  if (streamMaxSlots_ > 0 && index >= static_cast<size_t>(streamMaxSlots_))
    throw std::runtime_error("stream slot pool exceeded numeric id bound");
  streamInFlight_.push_back(index);
  lastAcquiredStreamSlot_ = index;
  streamArenaHighWater_ = std::max<int64_t>(
      streamArenaHighWater_, static_cast<int64_t>(streamInFlight_.size()));
  DynamicInst *slot = streamSlots_[index].get();
  if (streamOperandBlockSize_ > 0)
    slot->srcIdBase =
        static_cast<int32_t>(index) * streamOperandBlockSize_;
  else
    slot->srcIdBase = -1;
  slot->dstIdBase = -1;
  return slot;
}

void Engine::returnStreamSlotTail() {
  // Only the slot that this fetch actually acquired may be returned, and only
  // while it is still the FIFO tail. After the pop below the tail is a different
  // index (or the queue is empty), so a second call is a no-op by construction:
  // no free-list scan is needed on the steady-state barrier path.
  if (streamInFlight_.empty() || streamInFlight_.back() != lastAcquiredStreamSlot_)
    return;
#ifndef NDEBUG
  const int64_t seq = streamSlots_[lastAcquiredStreamSlot_]->streamSeq;
  for (const size_t inFlight : streamInFlight_)
    assert(streamSlots_[inFlight]->streamSeq <= seq);
#endif
  streamInFlight_.pop_back();
  streamFreeSlots_.push_back(lastAcquiredStreamSlot_);
}

void Engine::reclaimStreamArena() {
  if (streamInFlight_.empty())
    return;
  int64_t minSeq = std::numeric_limits<int64_t>::max();
  for (const auto *inst : idu_.borrowedWindow())
    minSeq = std::min(minSeq, inst->streamSeq);
  for (const auto &[cycle, inst] : pipe_)
    minSeq = std::min(minSeq, inst->streamSeq);
  while (!streamInFlight_.empty()) {
    const size_t index = streamInFlight_.front();
    if (streamSlots_[index]->streamSeq >= minSeq)
      break;
    streamInFlight_.pop_front();
    streamFreeSlots_.push_back(index);
  }
}

void Engine::backendStep() {
  shq_.runReleaseEvents(cycle_);
  registers_.runReleaseEvents(cycle_);
  auto completed = exu_.finish(cycle_);
  auto lsuDone = lsu_.finish(cycle_);
  completed.insert(completed.end(), lsuDone.begin(), lsuDone.end());
  std::sort(completed.begin(), completed.end(),
            [](const auto &a, const auto &b) { return a->instId < b->instId; });
  for (const auto &u : completed) {
    trace_.log("done", *u, cycle_);
    lastDone_ = std::max(lastDone_, *u->doneCycle);
    registers_.onDone(*u, cycle_);
    --activeCount_;
  }
  registers_.tryFreeEligible(cycle_);
  lsu_.updateReady(cycle_);
  shq_.updateReady(cycle_, registers_);
  std::unordered_set<int64_t> blocked;
  auto counts = lsu_.issue(cycle_, {}, blocked);
  shq_.updateReady(cycle_, registers_);
  lsu_.updateReady(cycle_, true);
  if (cfg_.enableIsuQueueModel) {
    ooo_.dispatch(cycle_);
    for (int port = 0; port < cfg_.issuePorts; ++port) {
      auto u = exq_.popReady(port, cycle_);
      if (u) {
        exu_.start(u, port, cycle_, true);
        registers_.onStart(*u, cycle_);
        trace_.log("start", *u, cycle_);
      }
    }
  } else
    ooo_.issueDirect(cycle_);
  lsu_.updateReady(cycle_, true);
  lsu_.issue(cycle_, counts, blocked);
}
SimulationResult Engine::run(const std::filesystem::path &outDir,
                             int64_t maxCycles) {
  // Traces are diagnostic output and never participate in scheduling.
  // Headless scoring needs only the final cycles, not copies of every uop.
  if (!outDir.empty() && !program_.retainsDiagnostics)
    throw std::runtime_error("Trace export requires diagnostic preparation");
  registers_.setTraceEnabled(!outDir.empty());
  // Same gate for the loop scheduling history: a scored run that writes no
  // trace/export never consumes the records, so keep the vector empty (this
  // also drops the few records built while the control was constructed).
  loops_.setTraceRecordsEnabled(!outDir.empty() || !streamNumericTokens_);

  // Choose the operand-id mode once for the whole run: only a program whose
  // complete table is present and in range may use the integer RAT; anything
  // else (hand-built programs included) keeps the string RAT for the run.
  bool useLogicalIds = !program_.operandIds.empty();
  if (useLogicalIds) {
    const size_t tableSize = program_.operandIds.size();
    for (const auto &inst : program_.instructions) {
      if (inst.type != "inst")
        continue;
      const size_t srcCount = inst.src.size();
      const size_t dstCount = inst.dst.size();
      if (inst.srcIdBase < 0 || inst.dstIdBase < 0 ||
          static_cast<size_t>(inst.srcIdBase) + srcCount > tableSize ||
          static_cast<size_t>(inst.dstIdBase) + dstCount > tableSize) {
        useLogicalIds = false;
        break;
      }
    }
  }
  registers_.setOperandIds(useLogicalIds ? program_.operandIds.data() : nullptr,
                           useLogicalIds ? program_.operandIds.size() : 0,
                           useLogicalIds);
  trace_.setEnabled(!outDir.empty());
  idu_.setTraceEnabled(!outDir.empty());
  if (maxCycles <= 0) {
    auto it = program_.params.find("max_cycles");
    maxCycles = it == program_.params.end() ? 1000000 : it->second;
  }
  bool completed = false;
  while (cycle_ < maxCycles) {
    auto delta = registers_.updateVisibility(cycle_);
    auto shqDelta = shq_.updateVisibility(cycle_);
    if (cfg_.useExplicitIduCreditBank) {
      vectorCredit_ += delta.first;
      predicateCredit_ += delta.second;
      shqCredit_ += shqDelta;
    }
    while (!pipe_.empty() && pipe_.front().first <= cycle_) {
      auto item = std::move(pipe_.front());
      pipe_.pop_front();
      if (cfg_.useExplicitIduCreditBank) {
        auto r = reservation(*item.second);
        pendingShq_ -= r[2];
        pendingLsq_ -= r[3];
      }
      admit(*item.second);
    }
    Reservation pending{};
    if (!cfg_.useExplicitIduCreditBank)
      for (const auto &[cy, inst] : pipe_) {
        auto r = reservation(*inst);
        for (size_t i = 0; i < 5; ++i)
          pending[i] += r[i];
      }
    while (idu_.canAccept() && !ifu_.done()) {
      const DynamicInst *instPtr = nullptr;
      if (ifu_.hasProvider()) {
        DynamicInst *slot = acquireStreamSlot();
        ifu_.nextInto(*slot);
        instPtr = slot;
        streamLiveValueHighWater_ = std::max<int64_t>(
            streamLiveValueHighWater_, registers_.streamValueCount());
      } else {
        instPtr = &ifu_.nextBorrowed();
      }
      const auto &inst = *instPtr;
      if (inst.type == "membar")
      {
        control_.acceptMembar(inst, cycle_);
        // acceptMembar copies streamSeq/pc/barrier into its own Barrier object;
        // no pointer into this slot survives, so return it immediately instead
        // of holding it until the FIFO reclaim scan.
        if (inst.type == "membar")
          returnStreamSlotTail();
      }
      else {
        idu_.acceptBorrowed(inst);
        control_.observeInstruction(inst);
      }
    }
    control_.update(
        [&](int64_t seq, const std::string &cls) {
          return hasPending(seq, cls);
        },
        cycle_, [&](int64_t seq) { return hasPendingDispatch(seq); });
    IDUDispatchBudget budget;
    budget.theoreticalLimitMode = cfg_.theoreticalLimitMode;
    budget.theoreticalLimitVloopOnly = cfg_.theoreticalLimitVloopOnly;
    budget.issueBudget = cfg_.iduIssueWidth;
    budget.freePreg =
        cfg_.useExplicitIduCreditBank
            ? vectorCredit_
            : std::max<int64_t>(0, registers_.freeVector() - pending[0]);
    budget.freePredicate =
        cfg_.useExplicitIduCreditBank
            ? predicateCredit_
            : std::max<int64_t>(0, registers_.freePredicate() - pending[1]);
    budget.freeShqQueue = std::max<int64_t>(
        0, shq_.freeQueue() -
               (cfg_.useExplicitIduCreditBank ? pendingShq_ : pending[2]));
    budget.freeLsq = std::max<int64_t>(
        0, lsu_.freeQueue() -
               (cfg_.useExplicitIduCreditBank ? pendingLsq_ : pending[3]));
    budget.freeShq = cfg_.useExplicitIduCreditBank
                         ? shqCredit_
                         : std::max<int64_t>(0, shq_.freeCredit() - pending[4]);
    const auto dispatched = idu_.dispatchBorrowed(cycle_, budget);
    for (const auto *dispatchedPtr : dispatched) {
      const auto &inst = *dispatchedPtr;
      if (cfg_.useExplicitIduCreditBank) {
        auto r = reservation(inst);
        vectorCredit_ -= r[0];
        predicateCredit_ -= r[1];
        shqCredit_ -= r[4];
        if (cfg_.iduToOooDelay > 0) {
          pendingShq_ += r[2];
          pendingLsq_ += r[3];
        }
      }
      if (cfg_.iduToOooDelay > 0)
        pipe_.emplace_back(cycle_ + cfg_.iduToOooDelay, dispatchedPtr);
      else
        admit(inst);
    }
    backendStep();
    // Reclaim lazily: the arena only needs a free slot when the next pull asks
    // for one, and the ready-bound scan inside reclaimStreamArena is the stream
    // path's only per-cycle cost. Same predicate, same slot reuse order, one
    // call per needed slot instead of one per simulated cycle.
    if (ifu_.hasProvider() && streamFreeSlots_.empty())
      reclaimStreamArena();
    lastDone_ = std::max(lastDone_, control_.lastRetireCycle());
    if (ifu_.done() && idu_.empty() && activeCount_ == 0 && pipe_.empty() &&
        control_.empty()) {
      completed = true;
      break;
    }
    ++cycle_;
  }
  if (!completed)
    throw std::runtime_error(
        "Simulation did not complete before max_cycles: cycle=" +
        std::to_string(cycle_));
  if (!outDir.empty())
    dumpLogs(outDir, trace_, idu_, loops_, control_, db_, cfg_.issuePorts);
  if (ifu_.hasProvider())
    reclaimStreamArena();
  return {cycle_, lastDone_ + db_.isaDefaults().vfDrainCost, outDir.string(),
          outDir.empty() ? std::string{} : (outDir / "trace.json").string(),
          program_.linearInstCount};
}
} // namespace vfsim
