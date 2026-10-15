// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/execution/engine.h"
#include "native/api/common/instruction_catalog.h"
#include <algorithm>
namespace vfsim {
Engine::Engine(const PreparedProgram &p, const UarchConfig &cfg,
               const ParamDB &db)
    : program_(p), cfg_(cfg), db_(db), registers_(cfg, db, p.values), shq_(cfg),
      exu_(cfg, db), exq_(cfg, db, registers_, exu_), trace_(registers_),
      control_(&db, cfg.membarTiming, db.isaDefaults().vfStartupCost),
      lsu_(cfg, db, registers_, shq_, control_, trace_), loops_(cfg, p),
      idu_(cfg, db, loops_, registers_.storage()),
      ooo_(cfg, registers_, shq_, exq_, exu_, trace_), ifu_(p.instructions),
      vectorCredit_(registers_.freeVector()),
      predicateCredit_(registers_.freePredicate()),
      shqCredit_(shq_.freeCredit()) {}
Engine::Reservation Engine::reservation(const DynamicInst &inst) const {
  Reservation counts{};
  for (const auto &d : inst.dst) {
    counts[0] += registers_.storage().isRegister(d);
    counts[1] += registers_.storage().isPredicate(d);
  }
  counts[2] = inst.opClass == "COMPUTE";
  counts[3] = inst.opClass == "LOAD" || inst.opClass == "STORE";
  counts[4] = inst.opClass == "COMPUTE" || inst.opClass == "STORE";
  return counts;
}
void Engine::admit(const DynamicInst &inst) {
  auto u = registers_.rename(inst, cycle_);
  if (u->profile->opClass == "LOAD" || u->profile->opClass == "STORE") {
    const auto &catalog = defaultInstructionCatalog();
    const auto *spec = inst.catalogMode.empty()
                           ? catalog.lookup(u->op)
                           : catalog.lookupMemoryMode(u->op, inst.catalogMode);
    if (!spec || spec->ubTransferBytes <= 0)
      throw std::runtime_error("Missing Catalog UB transfer semantics: " +
                               u->op);
    u->ubTransferBytes = spec->ubTransferBytes;
    lsu_.enqueue(u, cycle_);
  } else
    shq_.enqueue(u, cycle_);
  shq_.reserve(*u);
  active_.emplace(u->instId, u);
}
bool Engine::hasPendingDispatch(int64_t seq) const {
  for (const auto &i : idu_.window())
    if (i.streamSeq < seq)
      return true;
  for (const auto &[c, i] : pipe_)
    if (i.streamSeq < seq)
      return true;
  return false;
}
bool Engine::hasPending(int64_t seq, const std::string &cls) const {
  for (const auto &i : idu_.window())
    if (i.streamSeq < seq && i.opClass == cls)
      return true;
  for (const auto &[c, i] : pipe_)
    if (i.streamSeq < seq && i.opClass == cls)
      return true;
  return lsu_.hasPendingBefore(seq, cls);
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
    active_.erase(u->instId);
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
        auto r = reservation(item.second);
        pendingShq_ -= r[2];
        pendingLsq_ -= r[3];
      }
      admit(item.second);
    }
    Reservation pending{};
    if (!cfg_.useExplicitIduCreditBank)
      for (const auto &[cy, inst] : pipe_) {
        auto r = reservation(inst);
        for (size_t i = 0; i < 5; ++i)
          pending[i] += r[i];
      }
    while (idu_.canAccept() && !ifu_.done()) {
      auto inst = ifu_.nextInst();
      if (inst.type == "membar")
        control_.acceptMembar(inst, cycle_);
      else {
        idu_.accept(inst);
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
    for (const auto &inst : idu_.dispatch(cycle_, budget)) {
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
        pipe_.emplace_back(cycle_ + cfg_.iduToOooDelay, inst);
      else
        admit(inst);
    }
    backendStep();
    lastDone_ = std::max(lastDone_, control_.lastRetireCycle());
    if (ifu_.done() && idu_.empty() && active_.empty() && pipe_.empty() &&
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
  return {cycle_, lastDone_ + db_.isaDefaults().vfDrainCost, outDir.string(),
          outDir.empty() ? std::string{} : (outDir / "trace.json").string(),
          program_.linearInstCount};
}
} // namespace vfsim
