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

  AddressStateTracker tracker;
  tracker.updateLatency = 3;
  auto first = tracker.bind(0, {access("p", true)});
  auto second = tracker.bind(1, {access("p", false)});
  auto independent = tracker.bind(2, {access("q", true)});
  require(first[0].canIssue(100));
  require(independent[0].canIssue(100));
  require(!second[0].canIssue(100));
  auto queueCopy = first;
  tracker.notifyStart(queueCopy, 100);
  first.clear();
  queueCopy.clear();
  require(!second[0].canIssue(102));
  require(second[0].canIssue(103));
  auto reader = tracker.bind(3, {access("p", false)});
  require(reader[0].canIssue(103));
  auto update = tracker.bind(4, {access("p", true)});
  require(!update[0].canIssue(103));
  tracker.notifyStart(second, 103);
  require(!update[0].canIssue(103));
  tracker.notifyStart(reader, 104);
  require(update[0].canIssue(104));
  tracker.notifyStart(update, 104);
  auto next = tracker.bind(5, {access("p", true)});
  require(!next[0].canIssue(106));
  require(next[0].canIssue(107));
  require(tracker.bind(6, {CanonicalMemoryAccess{}}).empty());
  std::cout << "Address state RAW/WAR and shared event tests passed\n";
}
