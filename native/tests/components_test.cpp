// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/tests/support.h"
#include "native/core/uarch/register_bank.h"
#include <iostream>
using namespace vfsim;
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
UopPtr compute(int64_t id, const std::string &op, const ParamDB &db,
               const std::string &form = "fp32") {
  auto u = std::make_shared<Uop>();
  u->instId = id;
  u->op = op;
  u->form = form;
  u->profile = &db.inst(op, u->form, "COMPUTE");
  u->writebackBanks = {"vector"};
  return u;
}
void testIssueIntervals(const ParamDB &db) {
  auto cfg = db.uarch();
  cfg.enableCrossFuIi = true;
  cfg.exqIssueInflightCapPerPort = 0;
  for (bool queued : {false, true}) {
    for (const auto &middleOp : {"VADD", "VCMAX"}) {
      EXU exu(cfg, db);
      exu.start(compute(0, "VEXP", db), 0, 100, queued);
      exu.start(compute(1, middleOp, db), 0, 101, queued);
      auto last = compute(2, "VEXP", db);
      require(exu.nextIssueCycle(*last, 0) == 104,
              "Interleaving preserves opcode/form self-II");
      require(!exu.canIssue(*last, 0, 102, queued) &&
                  exu.canIssue(*last, 0, 104, queued),
              "Self-II remains active through different ALU/SFU instructions");
      require(exu.canIssue(*last, 1, 102, queued),
              "Self-II state belongs to one execution port");
    }
  }
  EXU absolute(cfg, db);
  absolute.start(compute(0, "VEXP", db), 0, 100, true);
  absolute.start(compute(1, "VADD", db), 0, 103, true);
  require(absolute.nextIssueCycle(*compute(2, "VEXP", db), 0) == 104,
          "Each II constraint uses its own producer issue cycle");

  EXU forms(cfg, db);
  forms.start(compute(0, "VEXP", db), 0, 100, true);
  forms.start(compute(1, "VEXP", db, "fp16"), 0, 101, true);
  forms.start(compute(2, "VADD", db), 0, 102, true);
  require(forms.nextIssueCycle(*compute(3, "VEXP", db), 0) == 104 &&
              forms.nextIssueCycle(*compute(4, "VEXP", db, "fp16"), 0) == 109,
          "Each opcode/form preserves its own self-II deadline");

  std::unordered_map<std::string, ValueInfo> values;
  Registers regs(cfg, db, values);
  EXU exu(cfg, db);
  exu.start(compute(0, "VEXP", db), 0, 100, true);
  exu.start(compute(1, "VCMAX", db), 0, 101, true);
  EXQ prediction(cfg, db, regs, exu);
  prediction.enqueue(compute(2, "VCMAX", db), 0, 101, 101);
  auto last = compute(3, "VEXP", db);
  require(prediction.predictIssueCycle(0, *last, 102) == 104,
          "EXQ prediction retains issued self-II through a different tail");
  EXQ selection(cfg, db, regs, exu);
  selection.enqueue(last, 0, 102, 102);
  require(!selection.popReady(0, 102) && selection.popReady(0, 104) == last,
          "EXQ selection waits for persistent self-II");

  EXU empty(cfg, db);
  EXQ pending(cfg, db, regs, empty);
  pending.enqueue(compute(4, "VEXP", db), 0, 100, 100);
  pending.enqueue(compute(5, "VCMAX", db), 0, 101, 101);
  require(pending.predictIssueCycle(0, *compute(6, "VEXP", db), 102) == 104,
          "EXQ prediction retains a pending same-form instruction");

  for (const auto &firstOp : {"VEXP", "VDIV"}) {
    const auto middleOp = std::string(firstOp) == "VEXP" ? "VDIV" : "VEXP";
    const auto gap = std::string(firstOp) == "VEXP" ? 6 : 5;
    EXU mixed(cfg, db);
    mixed.start(compute(0, firstOp, db), 0, 100, true);
    auto middle = compute(1, middleOp, db);
    require(!mixed.canIssue(*middle, 0, 100 + gap - 1, true) &&
                mixed.canIssue(*middle, 0, 100 + gap, true),
            "VEXP/VDIV conversion intervals remain directional");
    mixed.start(middle, 0, 100 + gap, true);
    require(mixed.nextIssueCycle(*compute(2, firstOp, db), 0) == 111,
            "Controlled VEXP/VDIV triples preserve both timing constraints");
  }
}
void testCompletionBandwidth(const ParamDB &db) {
  auto cfg = db.uarch();
  cfg.exqIssueInflightCapPerPort = 0;
  cfg.enableCrossFuIi = true;
  for (bool queued : {false, true}) {
    for (bool cross : {false, true}) {
      cfg.enableCrossFuIi = cross;
      EXU exu(cfg, db);
      exu.start(compute(0, "VEXP", db), 0, 100, queued);
      auto add = compute(1, "VADD", db);
      require(!exu.canIssue(*add, 0, 109, queued) &&
                  exu.canIssue(*add, 0, 110, queued),
              "Future completion conflicts delay issue in every mode");
      bool rejected = false;
      try {
        exu.start(add, 0, 109, queued);
      } catch (const std::runtime_error &) {
        rejected = true;
      }
      require(rejected && !add->startCycle,
              "Rejected completion reservation leaves the candidate unchanged");
      exu.start(add, 0, 110, queued);
      require(*add->doneCycle == 117 && *add->doneCycle - *add->startCycle == 7,
              "Delaying issue preserves the instruction latency");
    }
  }
  cfg.enableCrossFuIi = true;
  EXU consecutive(cfg, db);
  consecutive.start(compute(0, "VEXP", db), 0, 100, true);
  consecutive.start(compute(1, "VCMAX", db), 0, 101, true);
  auto add = compute(2, "VADD", db);
  require(!consecutive.canIssue(*add, 0, 109, true) &&
              !consecutive.canIssue(*add, 0, 110, true) &&
              consecutive.canIssue(*add, 0, 111, true),
          "Consecutive occupied completion cycles are all skipped");
  require(consecutive.nextIssueCycle(*add, 0, false, 109) == 111,
          "Earliest issue includes the requested cycle and completion slots");

  EXU older(cfg, db);
  older.start(compute(0, "VEXP", db), 0, 100, true);
  older.start(compute(1, "VADD", db), 0, 101, true);
  auto mul = compute(2, "VMUL", db);
  require(!older.canIssue(*mul, 0, 108, true) &&
              older.canIssue(*mul, 0, 109, true),
          "An older instruction retains its completion reservation");

  EXU independent(cfg, db);
  independent.start(compute(0, "VEXP", db), 0, 100, true);
  independent.start(compute(1, "VADD", db), 1, 109, true);
  require(independent.finish(116).size() == 2,
          "Different execution ports can finish in the same cycle");
  require(independent.inflight(0) == 0 && independent.inflight(1) == 0,
          "Completion releases inflight and completion reservations");

  for (bool greedy : {false, true}) {
    cfg.enableExqGreedyBalance = greedy;
    EXU ports(cfg, db);
    ports.start(compute(0, "VEXP", db), 0, 100, true);
    ports.start(compute(1, "VEXP", db), 1, 101, true);
    require(ports.pickPort(*add, 109, {false, false}) == 1,
            "Port selection checks completions at the candidate issue cycle");
  }
  std::unordered_map<std::string, ValueInfo> values;
  Registers regs(cfg, db, values);
  for (bool pending : {false, true}) {
    EXU exu(cfg, db);
    exu.start(compute(0, "VEXP", db), 0, 100, true);
    EXQ queue(cfg, db, regs, exu);
    if (pending)
      queue.enqueue(compute(1, "VADD", db), 0, 107, 107);
    require(queue.predictIssueCycle(0, *add, 109) == 110,
            "EXQ prediction accounts for reserved completion cycles");
  }
  EXU selection(cfg, db);
  selection.start(compute(0, "VEXP", db), 0, 100, true);
  EXQ queue(cfg, db, regs, selection);
  queue.enqueue(add, 0, 109, 109);
  require(!queue.popReady(0, 109) && queue.popReady(0, 110) == add,
          "EXQ actual issue waits for a free completion slot");

  EXU pipeline(cfg, db);
  for (int i = 0; i < 6; ++i) {
    auto current = compute(i, "VADD", db);
    require(pipeline.canIssue(*current, 0, 100 + i, true),
            "Independent pipelined instructions keep unit issue bandwidth");
    pipeline.start(current, 0, 100 + i, true);
  }
  require(pipeline.inflight(0) == 6, "Completion bandwidth preserves pipelined overlap");
}
void testSeparateWritebackBanks(const ParamDB &db) {
  auto cfg = db.uarch();
  cfg.exqIssueInflightCapPerPort = 0;
  auto predicate = [&](int64_t id, const std::string &op,
                       const std::string &form = "fp32") {
    auto u = compute(id, op, db, form);
    u->writebackBanks = {"predicate"};
    return u;
  };
  for (bool queued : {false, true}) {
    for (bool cross : {false, true}) {
      cfg.enableCrossFuIi = cross;
      EXU exu(cfg, db);
      auto vector = compute(0, "VEXP", db);
      auto pred = predicate(1, "VCMP_GT");
      exu.start(vector, 0, 100, queued);
      require(exu.canIssue(*pred, 0, 110, queued),
              "Vector and predicate completion slots are independent");
      exu.start(pred, 0, 110, queued);
      require(vector->doneCycle == pred->doneCycle && exu.finish(116).size() == 2,
              "One EXU can finish vector and predicate results in the same cycle");
      require(exu.inflight(0) == 0, "Both completions release inflight credit");
      auto both = compute(2, "VADD", db);
      both->writebackBanks = {"vector", "predicate"};
      require(exu.nextCompletionIssueCycle(*both, 0, 109) == 109,
              "Finishing both results releases both completion slots");
    }
  }
  cfg.enableCrossFuIi = true;
  EXU predOnly(cfg, db);
  predOnly.start(predicate(0, "PAND", "b32"), 0, 100, true);
  auto pred = predicate(1, "VCMP_GT");
  require(!predOnly.canIssue(*pred, 0, 103, true) &&
              predOnly.canIssue(*pred, 0, 104, true),
          "Two predicate-producing instructions need separate completion cycles");
  for (const auto &busyBank : {"vector", "predicate"}) {
    EXU exu(cfg, db);
    auto first = compute(0, "VEXP", db);
    first->writebackBanks = {busyBank};
    exu.start(first, 0, 100, true);
    auto both = compute(1, "VADD", db);
    both->writebackBanks = {"vector", "predicate"};
    require(exu.nextIssueCycle(*both, 0, false, 109) == 110,
            "An instruction writing both banks requires both completion slots");
    bool rejected = false;
    try {
      exu.start(both, 0, 109, true);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    auto other = compute(2, "VADD", db);
    other->writebackBanks = {
        std::string(busyBank) == "vector" ? "predicate" : "vector"};
    require(rejected && !both->startCycle && exu.canIssue(*other, 0, 109, true),
            "A rejected reservation leaves both banks and the candidate unchanged");
    exu.start(both, 0, 110, true);
    for (const auto &bank : {"vector", "predicate"}) {
      other->writebackBanks = {bank};
      require(exu.nextCompletionIssueCycle(*other, 0, 110) == 111,
              "A dual-bank instruction reserves each bank once");
    }
    require(exu.finish(117).size() == 2 && exu.inflight(0) == 0,
            "Dual-bank completion releases reservations and inflight credit");
  }
  EXU noOutput(cfg, db);
  noOutput.start(compute(0, "VEXP", db), 0, 100, true);
  auto empty = compute(1, "VADD", db);
  empty->writebackBanks.clear();
  require(noOutput.canIssue(*empty, 0, 109, true),
          "Instructions without register outputs consume no writeback slot");
  noOutput.start(empty, 0, 109, true);
  require(noOutput.finish(116).size() == 2,
          "An instruction without outputs can finish beside a vector result");

  std::unordered_map<std::string, ValueInfo> values;
  Registers regs(cfg, db, values);
  for (bool greedy : {false, true}) {
    for (bool pending : {false, true}) {
      cfg.enableExqGreedyBalance = greedy;
      EXU exu(cfg, db);
      exu.start(compute(0, "VEXP", db), 0, 100, true);
      EXQ queue(cfg, db, regs, exu);
      if (pending)
        queue.enqueue(compute(2, "VADD", db), 0, 107, 107);
      auto pred = predicate(1, "VCMP_GT");
      require(queue.predictIssueCycle(0, *pred, 110) == 110 &&
                  exu.pickPort(*pred, 110, {false, true}) == 0,
              "EXQ prediction and direct port selection use the destination bank");
      if (pending)
        require(queue.popReady(0, 107) != nullptr, "The pending tail can issue");
      queue.enqueue(pred, 0, 110, 110);
      require(queue.popReady(0, 110) == pred,
              "EXQ can issue a predicate result beside a reserved vector completion");
    }
  }
}
void testRenameWritebackBanks(const ParamDB &db) {
  auto cfg = db.uarch();
  std::unordered_map<std::string, ValueInfo> values{
      {"predicate_like_vector", {"predicate_like_vector", ValueStorageKind::Register, "fp32", {}}},
      {"another_vector", {"another_vector", ValueStorageKind::Register, "fp32", {}}},
      {"V_like_predicate", {"V_like_predicate", ValueStorageKind::PredicateRegister, "b32", {}}},
      {"scalar", {"scalar", ValueStorageKind::Scalar, "int32", {}}}};
  for (bool theoretical : {false, true}) {
    cfg.theoreticalLimitMode = theoretical;
    Registers regs(cfg, db, values);
    DynamicInst inst;
    inst.op = "VADD";
    inst.form = "fp32";
    inst.dst = {"predicate_like_vector", "another_vector"};
    require(regs.rename(inst, 100)->writebackBanks == std::set<std::string>{"vector"},
            "Multiple vector destinations use one completion slot");
    inst.dst = {"predicate_like_vector", "V_like_predicate"};
    require(regs.rename(inst, 100)->writebackBanks ==
                std::set<std::string>({"vector", "predicate"}),
            "Rename records declared destination banks for opaque identifiers");
    inst.dst = {"scalar"};
    require(regs.rename(inst, 100)->writebackBanks.empty(),
            "Scalar destinations use neither register writeback bank");
    inst.src = {"V_like_predicate"};
    inst.dst = {"another_vector"};
    require(regs.rename(inst, 100)->writebackBanks == std::set<std::string>{"vector"},
            "Predicate sources do not reserve predicate writeback slots");
  }
}
} // namespace
int main() {
  ParamDB db(VFSIM_SOURCE_ROOT);
  testIssueIntervals(db);
  testCompletionBandwidth(db);
  testSeparateWritebackBanks(db);
  testRenameWritebackBanks(db);
  auto cfg = db.uarch();
  cfg.exqIssueInflightCapPerPort = 1;
  cfg.exqDepth = 1;
  PhysicalRegisterBank bank(1, "r");
  const auto physical = bank.allocate();
  require(bank.freeCount() == 0, "Register allocation consumes bank credit");
  bank.release(physical);
  require(bank.freeCount() == 1, "Release returns bank credit");
  bool duplicate = false;
  try {
    bank.release(physical);
  } catch (const std::runtime_error &) {
    duplicate = true;
  }
  require(duplicate, "Duplicate physical release must fail");
  std::unordered_map<std::string, ValueInfo> values{
      {"mem_like_vector",
       {"mem_like_vector", ValueStorageKind::Register, "fp32", {}}},
      {"V_like_memory", {"V_like_memory", ValueStorageKind::UB, "fp32", {}}}};
  ValueStorageLookup storage(values);
  require(storage.isRegister("mem_like_vector") &&
              storage.isUB("V_like_memory"),
          "Storage uses declared semantics");
  bool undeclared = false;
  try {
    storage.storageOf("V_old_guess");
  } catch (const std::runtime_error &) {
    undeclared = true;
  }
  require(undeclared, "Undeclared storage must fail");
  Registers regs(cfg, db, values);
  DynamicInst instruction;
  instruction.op = "VADD";
  instruction.form = "fp32";
  instruction.opClass = "COMPUTE";
  instruction.dst = {"mem_like_vector"};
  auto renamed = regs.rename(instruction, 0);
  const auto *profile = &db.inst("VADD", "fp32", "COMPUTE");
  require(renamed->profile == profile,
          "Renamed instructions reference the database profile");
  require(profile->allowedExuPorts == std::vector<int>({0, 1}),
          "Profiles resolve allowed execution ports");
  require(db.inst("UNKNOWN", "fp32", "LOAD").opClass == "LOAD" &&
              db.inst("UNKNOWN", "fp32", "COMPUTE").opClass == "COMPUTE",
          "Declared instruction classes have independent profiles");
  require(renamed->profile == profile && profile->latency > 0,
          "Profile references survive further parameter lookups");
  EXU exu(cfg, db);
  EXQ exq(cfg, db, regs, exu);
  auto first = compute(0, "VADD", db);
  auto second = compute(1, "VEXP", db);
  exq.enqueue(first, 0, 30, 30);
  require(!exq.canAccept(0), "EXQ admission consumes queue capacity");
  require(!exq.popReady(0, 29), "EXQ cannot issue before reception");
  require(exq.popReady(0, 30) == first, "EXQ returns admitted object");
  exu.start(first, 0, 30, true);
  require(exq.canAccept(0), "Popped instruction frees waiting capacity");
  require(!exu.canAccept(0, true), "EXU enforces queued inflight cap");
  require(exu.finish(30 + first->profile->latency - 1).empty(),
          "EXU does not finish early");
  require(exu.finish(30 + first->profile->latency).size() == 1,
          "EXU completes at latency boundary");
  require(exu.canAccept(0, true), "Completion returns inflight credit");
  auto restrictedProfile = *second->profile;
  restrictedProfile.dispatchExu = "EXU0_ONLY";
  restrictedProfile.allowedExuPorts = {0};
  second->profile = &restrictedProfile;
  require(!exu.canIssue(*second, 1, 100, true),
          "EXU0-only instruction cannot use port 1");
  cfg.exqCapacityCountsInflight = true;
  exu.start(second, 0, 100, true);
  require(!exq.canAccept(0), "EXQ optionally counts running instructions");
  SHQ shq(cfg);
  auto tracked = compute(2, "VADD", db);
  shq.reserve(*tracked);
  shq.release(*tracked, 100);
  shq.release(*tracked, 100);
  require(shq.freeCredit() == cfg.shqDepth - 1, "SHQ reserves shared credit");
  shq.runReleaseEvents(100 + cfg.shqReleaseDelay);
  require(shq.updateVisibility(100 + cfg.shqReleaseDelay) == 1,
          "SHQ releases credit exactly once");
  require(shq.freeCredit() == cfg.shqDepth, "SHQ release restores credit");
  auto a = &db.inst("VADD", "fp32");
  db.inst("UNMODELED", "fp32");
  require(&db.inst("VADD", "fp32") == a,
          "Cached profiles have stable lifetime");
  auto theory = resolveUarch(cfg, {}, true, true);
  require(theory.theoreticalLimitMode && !theory.enableIsuQueueModel,
          "Direct theoretical mode resolves component behavior");
  std::cout << "Component capacity, timing, identity and credit tests passed\n";
}
