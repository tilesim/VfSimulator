#include "native/AddressState.h"
#include "native/CanonicalProgramLowering.h"
#include "native/SimulatorRunner.h"
#include <iostream>
#include <stdexcept>

using namespace vfsim;

static void require(bool condition) {
  if (!condition) throw std::runtime_error("Address state assertion failed");
}

static CanonicalMemoryAccess access(const std::string &state, bool update) {
  CanonicalMemoryAccess memory;
  memory.baseObjectId = "same_allocation";
  memory.accessKind = CanonicalAccessKind::Read;
  memory.addressStateId = state;
  if (update) {
    memory.updateMode = "post_update";
    memory.postUpdateDeltaBytes = CanonicalAffineExpression{256, {}};
  }
  return memory;
}

int main() {
  CanonicalVfInfo vf;
  vf.storageObjects.emplace("same_allocation", CanonicalStorageObject{
      "same_allocation", CanonicalStorageKind::UB});
  CanonicalValue memoryValue;
  memoryValue.definitionId = "mem";
  memoryValue.logicalId = "mem";
  memoryValue.storage = CanonicalStorageKind::UB;
  memoryValue.storageObjectId = "same_allocation";
  memoryValue.dtype = "fp32";
  vf.values.emplace("mem", memoryValue);
  for (int i = 0; i < 3; ++i) {
    const auto id = "load" + std::to_string(i);
    const auto def = "value" + std::to_string(i);
    CanonicalValue value;
    value.definitionId = def;
    value.logicalId = def;
    value.storage = CanonicalStorageKind::Register;
    value.dtype = "fp32";
    value.producerNodeId = id;
    vf.values.emplace(def, value);
    CanonicalInstruction inst;
    inst.instructionId = id;
    inst.opcode = "VLDS";
    inst.form = "fp32";
    inst.instructionClass = CanonicalInstructionClass::Load;
    inst.inputs.push_back({"mem", CanonicalOperandRole::Memory, "fp32", access("p", true)});
    inst.outputs.push_back({def, CanonicalOperandRole::Destination, "fp32"});
    vf.context.push_back(CanonicalNode::makeInstruction(std::move(inst)));
  }
  require(validateCanonicalVfInfo(vf).ok());
  const auto lowered = lowerCanonicalProgram(vf);
  require(lowered.instructions.size() == 3);
  require(lowered.instructions[1].memoryAccesses[0].addressStateId == "p");
  const ParamDB db(VFSIM_SOURCE_ROOT);
  require(runCanonicalVfInfo(vf, db).vfEndCycle > 0);
  std::get<CanonicalInstruction>(vf.context[0].payload).inputs[0].memoryAccess->addressStateId.reset();
  require(!validateCanonicalVfInfo(vf).ok());

  AddressStateTracker tracker(3);
  tracker.notifyDispatch(0, {access("p", true)}, 100);
  require(!tracker.canDispatch({access("p", false)}, 102));
  require(tracker.canDispatch({access("p", false)}, 103));
  require(tracker.canDispatch({access("q", true)}, 100));
  tracker.notifyDispatch(1, {access("p", false)}, 103);
  require(tracker.canDispatch({access("p", true)}, 103));
  tracker.notifyDispatch(2, {access("p", true)}, 103);
  require(!tracker.canDispatch({access("p", true)}, 105));
  require(tracker.canDispatch({access("p", true)}, 106));
  require(tracker.dependencies({access("p", false)})[0].producerInstId == 2);
  require(AddressStateTracker().dependencies({access("p", true)}).empty());

  auto uarch = db.uarch();
  uarch.iduPostUpdateReadyLatency = 3;
  IDU idu(uarch, db, {}, {}, 1, {}, "fp32", lowered.values);
  for (const auto &inst : lowered.instructions) idu.accept(inst);
  IDUDispatchBudget budget{68, 58, 24, 58, 3};
  auto noCredits = budget;
  noCredits.freePreg = 0;
  require(idu.dispatch(99, noCredits).empty());
  require(idu.dispatch(100, budget).size() == 1);
  require(idu.dispatch(100, budget).empty());
  require(idu.dispatch(102, budget).empty());
  require(idu.dispatch(103, budget).size() == 1);
  require(idu.dispatch(106, budget).size() == 1);
  require(idu.empty());
  require(idu.dispatchLog()[1].addressDependencies[0].producerDispatchCycle == 100);
  require(idu.addressBlockLog()[0].addressDependencies[0].readyCycle == 103);

  for (bool update : {false, true}) {
    IDU independent(uarch, db, {}, {}, 1, {}, "fp32", lowered.values);
    auto first = lowered.instructions[0];
    auto second = lowered.instructions[1];
    first.memoryAccesses = {access("p", update)};
    second.memoryAccesses = {access(update ? "q" : "p", update)};
    independent.accept(first);
    independent.accept(second);
    require(independent.dispatch(100, budget).size() == 2);
  }
  std::cout << "IDU address forwarding and head-of-line tests passed\n";
}
