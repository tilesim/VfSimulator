#include "native/CanonicalProgramLowering.h"
#include "native/SimulatorRunner.h"
#include <iostream>
#include <stdexcept>

using namespace vfsim;

static void require(bool ok, const char *message) {
  if (!ok) throw std::runtime_error(message);
}

int main() {
  ParamDB db(VFSIM_SOURCE_ROOT);
  for (const auto &form : {"b8", "b16", "b32"}) {
    const std::string op = std::string("PSET_B") + (std::string(form).substr(1));
    require(db.inst(op, form).latency == 6, "PSET assumed latency");
    require(db.forwardingCycles(op, form, "VADD", "fp32") == 2, "PSET forwarding default");
    require(db.initiationInterval(op, form, op, form) == 1, "PSET self II approximation");
    require(db.initiationInterval("VADD", "fp32", op, form) == 2, "Assumed vector/predicate writeback collision");
  }
  CanonicalVfInfo vf;
  vf.schemaVersion = 2;
  vf.uarch["physical_predicate_registers"] = int64_t{2};
  auto value = [&](const std::string &id, CanonicalStorageKind storage,
                   const std::string &dtype, std::optional<std::string> producer) {
    CanonicalValue v;
    v.definitionId = id; v.logicalId = id; v.storage = storage;
    v.dtype = dtype; v.producerNodeId = producer;
    vf.values.emplace(id, v);
  };
  value("x", CanonicalStorageKind::Register, "fp32", std::nullopt);
  value("mask", CanonicalStorageKind::PredicateRegister, "bool", "pset");
  CanonicalInstruction pset;
  pset.instructionId = "pset"; pset.opcode = "PSET_B32"; pset.form = "b32";
  pset.instructionClass = CanonicalInstructionClass::Compute;
  pset.outputs = {{"mask", CanonicalOperandRole::Destination, "bool"}};
  vf.context.push_back(CanonicalNode::makeInstruction(pset));
  for (int i = 0; i < 80; ++i) {
    const auto suffix = std::to_string(i);
    const auto cmp = "cmp" + suffix, sel = "sel" + suffix;
    const auto pred = "pred" + suffix, dst = "dst" + suffix;
    value(pred, CanonicalStorageKind::PredicateRegister, "bool", cmp);
    value(dst, CanonicalStorageKind::Register, "fp32", sel);
    CanonicalInstruction c;
    c.instructionId = cmp; c.opcode = "VCMP_GT"; c.form = "fp32";
    c.instructionClass = CanonicalInstructionClass::Compute;
    c.inputs = {{"x", CanonicalOperandRole::Source, "fp32"},
                {"x", CanonicalOperandRole::Source, "fp32"},
                {"mask", CanonicalOperandRole::Predicate, "bool"}};
    c.outputs = {{pred, CanonicalOperandRole::Destination, "bool"}};
    vf.context.push_back(CanonicalNode::makeInstruction(c));
    c.instructionId = sel; c.opcode = "VSEL";
    c.inputs.back().valueId = pred;
    c.outputs = {{dst, CanonicalOperandRole::Destination, "fp32"}};
    vf.context.push_back(CanonicalNode::makeInstruction(c));
  }
  require(validateCanonicalVfInfo(vf).ok(), "predicate canonical validation");
  auto result = runCanonicalVfInfo(vf, db, "", 10000);
  require(result.vfEndCycle > 0 && result.cyclesExecuted < 10000, "predicate pool must recycle");
  auto lowered = lowerCanonicalProgram(vf);
  auto uarch = db.uarch();
  uarch.physicalPredicateRegisters = 1;
  OoOCoreMainline core(uarch, db, "fp32", lowered.values);
  core.accept(lowered.instructions[0]);
  require(core.getFreePreg() == uarch.vregNum, "PSET must not allocate vector credit");
  require(core.getFreePredicate() == 0, "PSET must allocate predicate credit");
  bool rejected = false;
  try { core.accept(lowered.instructions[1]); }
  catch (const std::runtime_error &) { rejected = true; }
  require(rejected, "rename must reject exhausted predicate bank");
  require(core.getFreePreg() == uarch.vregNum, "failed rename must be atomic");
  auto exitProgram = vf;
  auto exitValue = vf.values.at("mask");
  exitValue.definitionId = "mask.exit";
  exitValue.producerNodeId = "loop.pset";
  exitProgram.values.emplace("mask.exit", exitValue);
  CanonicalLoop loop;
  loop.loopId = "loop.pset";
  loop.induction.variableId = "i";
  loop.count = int64_t{2};
  loop.unroll = int64_t{1};
  loop.body.push_back(vf.context.front());
  loop.carriedValues.push_back({"mask", std::nullopt, "mask", "mask.exit"});
  exitProgram.context.front() = CanonicalNode::makeLoop(loop);
  for (size_t i = 1; i < exitProgram.context.size(); ++i)
    for (auto &operand : std::get<CanonicalInstruction>(exitProgram.context[i].payload).inputs)
      if (operand.valueId == "mask") operand.valueId = "mask.exit";
  require(validateCanonicalVfInfo(exitProgram).ok(), "first-definition loop exit must validate");
  require(runCanonicalVfInfo(exitProgram, db, "", 10000).vfEndCycle > 0, "loop exit must execute");
  loop.count = int64_t{0};
  exitProgram.context.front() = CanonicalNode::makeLoop(loop);
  require(!validateCanonicalVfInfo(exitProgram).ok(), "empty loop cannot produce an exit-only definition");
  vf.values.at("mask").producerNodeId.reset();
  require(!validateCanonicalVfInfo(vf).ok(), "predicate live-in must fail closed");
  std::cout << "predicate register test passed\n";
}
