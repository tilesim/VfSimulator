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
UopPtr compute(int64_t id, const std::string &op, const ParamDB &db) {
  auto u = std::make_shared<Uop>();
  u->instId = id;
  u->op = op;
  u->form = "fp32";
  u->profile = &db.inst(op, u->form, "COMPUTE");
  return u;
}
} // namespace
int main() {
  ParamDB db(VFSIM_SOURCE_ROOT);
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
