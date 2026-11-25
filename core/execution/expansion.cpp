// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/expansion.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <sstream>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

#ifndef VFSIM_LIFETIME_FLAT_LIMIT
#define VFSIM_LIFETIME_FLAT_LIMIT (1 << 20)
#endif

namespace vfsim {
namespace {

int64_t resolveInteger(const CanonicalIntegerExpression &expression,
                       const RuntimeParamMap &params,
                       const std::string &field) {
  if (const auto *value = std::get_if<int64_t>(&expression))
    return *value;
  const std::string &text = std::get<std::string>(expression);
  const auto parameter = params.find(text);
  if (parameter != params.end())
    return parameter->second;
  try {
    size_t consumed = 0;
    const int64_t value = std::stoll(text, &consumed, 10);
    if (consumed == text.size())
      return value;
  } catch (...) {
  }
  throw std::runtime_error("Cannot resolve canonical " + field + ": " + text);
}

struct Frame {
  std::string loopId;
  int64_t iteration = 0;
  int64_t scheduledIteration = 0;
  int64_t topBlockId = 0;
  std::string variable;
  int64_t start = 0, step = 1;
};

class Expander {
public:
  explicit Expander(const CanonicalVfInfo &vfInfo,
                    const std::vector<CanonicalNode> &context,
                    const ParamDB &db, const UarchConfig &cfg,
                    std::unordered_map<std::string, ValueInfo> values,
                    bool retainDiagnostics)
      : vfInfo_(vfInfo), context_(context), db_(db), cfg_(cfg) {
    runtime_.retainsDiagnostics = retainDiagnostics;
    runtime_.params = vfInfo.params;
    runtime_.values = std::move(values);
    indexLoops(context_, 0);
    buildPlanRoot(context_);
    slotValue_.resize(slotStatic_.size());
    slotBound_.assign(slotStatic_.size(), 0);
    slotOwner_.assign(slotStatic_.size(), -1);
    if (!retainDiagnostics)
      return;
    std::set<std::string> occupied;
    for (const auto &[id, value] : vfInfo.values)
      occupied.insert(id);
    std::map<std::string, std::string> ubNames;
    for (const auto &[id, object] : vfInfo.storageObjects) {
      std::string name = "__canonical_ub__" + id;
      while (occupied.count(name))
        name += ":ub";
      occupied.insert(name);
      ubNames.emplace(id, name);
    }
    for (const auto &[id, value] : vfInfo.values) {
      const auto name = value.storage == CanonicalStorageKind::UB
                            ? ubNames.at(*value.storageObjectId)
                            : id;
      if (value.storage == CanonicalStorageKind::UB)
        ubAliases_[name].push_back(id);
      staticNames_.emplace(id, name);
      instances_.emplace(id, ValueInstance{name, {}});
    }
  }

  PreparedProgram run() {
    runtime_.instructions.reserve(estimateInstructions(context_));
    expandTopLevelNodes(context_);
    annotateLifetimes();
    annotateSchedulingBoundaries();
    runtime_.totalTopBlocks = std::max<int64_t>(1, nextTopBlockId_);
    std::unordered_set<int64_t> nonemptyTopBlocks;
    for (const auto &instruction : runtime_.instructions) {
      if (instruction.type == "inst")
        nonemptyTopBlocks.insert(instruction.topBlockId);
    }
    for (int64_t topBlockId = 0; topBlockId < runtime_.totalTopBlocks;
         ++topBlockId) {
      if (!nonemptyTopBlocks.count(topBlockId))
        runtime_.emptyTopBlocks.insert(topBlockId);
    }
    if (runtime_.topBlockLoopBounds.empty())
      runtime_.topBlockLoopBounds.emplace(0, std::vector<int64_t>{});
    return std::move(runtime_);
  }

private:
  const CanonicalVfInfo &vfInfo_;
  const std::vector<CanonicalNode> &context_;
  const ParamDB &db_;
  const UarchConfig &cfg_;
  int64_t dynamicCount_ = 0;
  void countInstruction() {
    if (cfg_.canonicalDynamicInstructionLimit > 0 &&
        ++dynamicCount_ > cfg_.canonicalDynamicInstructionLimit)
      throw std::runtime_error("Canonical dynamic instruction limit exceeded");
  }
  PreparedProgram runtime_;

  // ---- static lowering: one plan tree built from the post-LICM canonical
  // nodes.  Every static operand is interned to a dense slot once; the hot
  // expansion path then walks the plan tree by index and reads/writes
  // slotValue_ instead of hashing value-id strings.
  struct StaticOperandPlan {
    int32_t slot = -1;
    ValueStorageKind storage = ValueStorageKind::Register;
  };
  struct StaticInstructionPlan {
    std::vector<StaticOperandPlan> inputs;
    std::vector<StaticOperandPlan> outputs;
    // S1: attributes and scalar plan data are static. Resolve them once while
    // building the plan; expansion only dereferences the results so that the
    // three std::map<string, CanonicalScalar> lookups and the staticPc_ hash
    // lookup are not repeated for every dynamic instance of an instruction.
    // The pointers stay valid because vfInfo_ (and its canonical nodes) is
    // immutable and outlives the plan.
    int64_t pc = -1;
    const CanonicalScalar *catalogMode = nullptr;
    const CanonicalScalar *alignStateOperation = nullptr;
    const CanonicalScalar *alignStateId = nullptr;
    std::vector<const CanonicalMemoryAccess *> memoryAccesses;
    const char *opClass = "COMPUTE";
  };
  struct StaticLoopPlan;
  struct StaticNodePlan {
    const StaticInstructionPlan *instruction = nullptr;
    const StaticLoopPlan *loop = nullptr;
  };
  struct StaticLoopPlan {
    std::vector<int32_t> carriedEntry;
    std::vector<int32_t> carriedBackEdge;
    std::vector<int32_t> carriedExit;
    bool entryPresent = false;
    int32_t entrySlot = -1;
    std::vector<StaticNodePlan> body;
    std::vector<StaticInstructionPlan> bodyInstructions;
    std::vector<StaticLoopPlan> bodyLoops;
  };
  std::vector<StaticNodePlan> planRoot_;
  std::vector<StaticInstructionPlan> rootInstructions_;
  std::vector<StaticLoopPlan> rootLoops_;
  std::unordered_map<std::string, int32_t> slotOf_;
  std::vector<std::string> slotStatic_;
  std::vector<std::string> slotValue_;
  std::vector<uint8_t> slotBound_;
  std::vector<int32_t> slotOwner_;
  struct SlotUndo {
    int32_t slot;
    std::string previous;
    uint8_t wasBound;
    int32_t previousOwner;
  };
  std::vector<SlotUndo> slotUndo_;
  std::vector<size_t> scopeMarks_;
  std::unordered_map<std::string, int64_t> loopTopBlockIds_;
  std::vector<Frame> frames_;
  std::unordered_map<std::string, ValueInstance> instances_;
  std::unordered_map<std::string, std::string> staticNames_;
  // UB versions share one storage object. Index aliases once instead of
  // rescanning all register/scalar definitions at every dynamic store.
  std::unordered_map<std::string, std::vector<std::string>> ubAliases_;
  std::unordered_map<std::string, int64_t> staticLoopIds_, staticPc_;
  int64_t nextValueId_ = 0;
  int64_t nextPc_ = 0;
  int64_t nextTopBlockId_ = 0;
  int64_t activeTopBlockId_ = 0;

  size_t estimateInstructions(const std::vector<CanonicalNode> &nodes) const {
    const uint64_t cap =
        cfg_.canonicalDynamicInstructionLimit > 0
            ? std::min<int64_t>(cfg_.canonicalDynamicInstructionLimit, 100000) +
                  1
            : 100000;
    uint64_t result = 0;
    for (const auto &node : nodes) {
      uint64_t count = 1;
      if (const auto *loop = std::get_if<std::shared_ptr<const CanonicalLoop>>(
              &node.payload)) {
        if (!*loop)
          continue;
        const auto trips =
            resolveInteger((*loop)->count, vfInfo_.params, "loop count");
        count = trips <= 0 ? 0
                           : std::min<uint64_t>(
                                 cap, std::min<uint64_t>(cap, trips) *
                                          estimateInstructions((*loop)->body));
      }
      result = std::min(cap, result + count);
    }
    return result;
  }

  void indexLoops(const std::vector<CanonicalNode> &nodes, int depth) {
    for (const auto &node : nodes) {
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr) {
        if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload))
          staticPc_[inst->instructionId] = nextPc_;
        else if (const auto *barrier =
                     std::get_if<CanonicalMembar>(&node.payload))
          staticPc_[barrier->instructionId] = nextPc_;
        ++nextPc_;
        continue;
      }
      const CanonicalLoop &loop = **loopPtr;
      staticLoopIds_[loop.loopId] = staticLoopIds_.size();
      staticPc_[loop.loopId] = nextPc_++;
      if (depth == 0)
        loopTopBlockIds_[loop.loopId] = nextTopBlockId_++;
      indexLoops(loop.body, depth + 1);
      ++nextPc_;
    }
  }

  int32_t slotFor(const std::string &valueId) {
    const auto [position, inserted] =
        slotOf_.try_emplace(valueId, static_cast<int32_t>(slotStatic_.size()));
    if (inserted)
      slotStatic_.push_back(valueId);
    return position->second;
  }

  void buildPlanOperands(const CanonicalInstruction &instruction,
                         StaticInstructionPlan &plan) {
    plan.inputs.reserve(instruction.inputs.size());
    for (const auto &operand : instruction.inputs)
      plan.inputs.push_back(
          {slotFor(operand.valueId), vfInfo_.values.at(operand.valueId).storage});
    plan.outputs.reserve(instruction.outputs.size());
    for (const auto &operand : instruction.outputs)
      plan.outputs.push_back(
          {slotFor(operand.valueId), vfInfo_.values.at(operand.valueId).storage});
    plan.pc = staticPc_.at(instruction.instructionId);
    const auto attribute = [&](const char *name) -> const CanonicalScalar * {
      const auto found = instruction.attributes.find(name);
      return found == instruction.attributes.end() ? nullptr : &found->second;
    };
    plan.catalogMode = attribute("catalog_mode");
    plan.alignStateOperation = attribute("align_state_operation");
    plan.alignStateId = attribute("align_state_id");
    plan.opClass =
        instruction.instructionClass == CanonicalInstructionClass::Load ? "LOAD"
        : instruction.instructionClass == CanonicalInstructionClass::Store
            ? "STORE"
            : "COMPUTE";
    plan.memoryAccesses.clear();
    for (const auto &operand : instruction.inputs)
      if (operand.memoryAccess)
        plan.memoryAccesses.push_back(&*operand.memoryAccess);
    for (const auto &operand : instruction.outputs)
      if (operand.memoryAccess)
        plan.memoryAccesses.push_back(&*operand.memoryAccess);
  }

  void buildPlanBody(const std::vector<CanonicalNode> &nodes,
                     std::vector<StaticNodePlan> &planNodes,
                     std::vector<StaticInstructionPlan> &planInstructions,
                     std::vector<StaticLoopPlan> &planLoops) {
    planInstructions.reserve(nodes.size());
    planLoops.reserve(nodes.size());
    planNodes.reserve(nodes.size());
    for (const auto &node : nodes) {
      StaticNodePlan planNode;
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        planInstructions.emplace_back();
        auto &plan = planInstructions.back();
        buildPlanOperands(*instruction, plan);
        planNode.instruction = &plan;
      } else if (std::get_if<CanonicalMembar>(&node.payload) != nullptr) {
        planNode.instruction = nullptr;
        planNode.loop = nullptr;
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        planLoops.emplace_back();
        auto &plan = planLoops.back();
        for (const auto &carried : loop->carriedValues) {
          plan.carriedEntry.push_back(
              carried.entryValueId ? slotFor(*carried.entryValueId) : -1);
          plan.carriedBackEdge.push_back(slotFor(carried.backEdgeValueId));
          plan.carriedExit.push_back(slotFor(carried.exitValueId));
        }
        plan.bodyInstructions.reserve(loop->body.size());
        plan.bodyLoops.reserve(loop->body.size());
        buildPlanBody(loop->body, plan.body, plan.bodyInstructions,
                      plan.bodyLoops);
        planNode.loop = &plan;
      }
      planNodes.push_back(planNode);
    }
  }

  void buildPlanRoot(const std::vector<CanonicalNode> &nodes) {
    rootInstructions_.reserve(nodes.size());
    rootLoops_.reserve(nodes.size());
    planRoot_.reserve(nodes.size());
    for (const auto &node : nodes) {
      StaticNodePlan planNode;
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        rootInstructions_.emplace_back();
        auto &plan = rootInstructions_.back();
        buildPlanOperands(*instruction, plan);
        planNode.instruction = &plan;
      } else if (std::get_if<CanonicalMembar>(&node.payload) != nullptr) {
        planNode.instruction = nullptr;
        planNode.loop = nullptr;
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        rootLoops_.emplace_back();
        auto &plan = rootLoops_.back();
        for (const auto &carried : loop->carriedValues) {
          plan.carriedEntry.push_back(
              carried.entryValueId ? slotFor(*carried.entryValueId) : -1);
          plan.carriedBackEdge.push_back(slotFor(carried.backEdgeValueId));
          plan.carriedExit.push_back(slotFor(carried.exitValueId));
        }
        plan.bodyInstructions.reserve(loop->body.size());
        plan.bodyLoops.reserve(loop->body.size());
        buildPlanBody(loop->body, plan.body, plan.bodyInstructions,
                      plan.bodyLoops);
        planNode.loop = &plan;
      }
      planRoot_.push_back(planNode);
    }
  }

  const std::string &resolveSlot(int32_t slot) const {
    if (slotBound_[slot])
      return slotValue_[slot];
    return slotStatic_[slot];
  }

  void pushScope() { scopeMarks_.push_back(slotUndo_.size()); }

  void popScope() {
    const size_t mark = scopeMarks_.back();
    scopeMarks_.pop_back();
    while (slotUndo_.size() > mark) {
      SlotUndo &undo = slotUndo_.back();
      slotValue_[undo.slot] = std::move(undo.previous);
      slotBound_[undo.slot] = undo.wasBound;
      slotOwner_[undo.slot] = undo.previousOwner;
      slotUndo_.pop_back();
    }
  }

  void bindSlot(int32_t slot, const std::string &name) {
    if (!slotValue_.empty() && slotBound_[slot] &&
        slotValue_[slot] == name)
      return;
    if (scopeMarks_.empty()) {
      slotValue_[slot] = name;
      slotBound_[slot] = 1;
      return;
    }
    const size_t mark = scopeMarks_.back();
    if (slotOwner_[slot] >= 0 &&
        static_cast<size_t>(slotOwner_[slot]) >= mark) {
      slotValue_[slot] = name;
      slotBound_[slot] = 1;
      return;
    }
    slotUndo_.push_back({slot, slotValue_[slot], slotBound_[slot],
                         slotOwner_[slot]});
    slotOwner_[slot] = static_cast<int32_t>(slotUndo_.size() - 1);
    slotValue_[slot] = name;
    slotBound_[slot] = 1;
  }

  std::string dynamicName(const std::string &definitionId) {
    if (frames_.empty())
      return definitionId;
    std::string identity;
    if (!runtime_.retainsDiagnostics) {
      do {
        identity = "@" + std::to_string(nextValueId_++);
      } while (vfInfo_.values.count(identity));
    } else {
      identity = definitionId;
      for (const auto &frame : frames_) {
        identity += "@";
        identity += frame.loopId;
        identity += "=";
        identity += std::to_string(frame.iteration);
      }
    }
    const auto source = vfInfo_.values.find(definitionId);
    if (runtime_.retainsDiagnostics && source != vfInfo_.values.end()) {
      ValueInfo value;
      value.valueId = identity;
      value.storage = source->second.storage;
      value.dtype = source->second.dtype;
      value.shape = source->second.shape;
      runtime_.values.emplace(identity, std::move(value));
    }
    return identity;
  }

  void fillDynamicMetadata(DynamicInst &inst) const {
    inst.topBlockId =
        frames_.empty() ? activeTopBlockId_ : frames_.front().topBlockId;
    inst.iterStack.reserve(frames_.size());
    if (runtime_.retainsDiagnostics)
      inst.iterationPath.reserve(frames_.size());
    for (const auto &frame : frames_) {
      inst.iterStack.push_back(frame.scheduledIteration);
      if (runtime_.retainsDiagnostics)
        inst.iterationPath.push_back(
            {frame.loopId, frame.iteration, frame.variable,
             frame.start + frame.iteration * frame.step});
    }
  }

  void expandInstruction(const CanonicalInstruction &instruction,
                         const StaticInstructionPlan &plan) {
    countInstruction();
    auto &dynamic = runtime_.instructions.emplace_back();
    dynamic.type = "inst";
    dynamic.op = instruction.opcode;
    dynamic.opClass = plan.opClass;
    dynamic.form = instruction.form;
    if (plan.catalogMode != nullptr)
      dynamic.catalogMode = std::get<std::string>(*plan.catalogMode);
    if (runtime_.retainsDiagnostics)
      dynamic.staticInstructionId = instruction.instructionId;
    dynamic.src.reserve(plan.inputs.size());
    dynamic.srcStorage.reserve(plan.inputs.size());
    dynamic.dst.reserve(plan.outputs.size());
    dynamic.dstStorage.reserve(plan.outputs.size());
    dynamic.pc = plan.pc;
    for (const auto *access : plan.memoryAccesses)
      dynamic.memoryAccesses.push_back(*access);
    // The plan caches the attribute lookup result; the exact type check and
    // throw still happen here, at the point where the instruction is really
    // expanded, in the historical order and with the historical messages.
    const auto *alignOperation = plan.alignStateOperation;
    if (alignOperation != nullptr) {
      const auto *value = std::get_if<std::string>(alignOperation);
      if (value == nullptr)
        throw std::runtime_error("align_state_operation must be a string: " +
                                 instruction.instructionId);
      dynamic.alignStateOperation = *value;
    }
    const auto *alignState = plan.alignStateId;
    if (alignState != nullptr) {
      const auto *value = std::get_if<std::string>(alignState);
      if (value == nullptr)
        throw std::runtime_error("align_state_id must be a string: " +
                                 instruction.instructionId);
      dynamic.alignStateId = *value;
    }
    fillDynamicMetadata(dynamic);
    dynamic.vectorDestCount = dynamic.predicateDestCount = 0;
    for (size_t index = 0; index < plan.inputs.size(); ++index) {
      const auto &operandPlan = plan.inputs[index];
      dynamic.srcStorage.push_back(operandPlan.storage);
      const std::string &identity = resolveSlot(operandPlan.slot);
      dynamic.src.push_back(identity);
      if (runtime_.retainsDiagnostics)
        dynamic.srcValueInstances.push_back(instances_.at(identity));
    }
    for (size_t index = 0; index < plan.outputs.size(); ++index) {
      const auto &operandPlan = plan.outputs[index];
      const std::string identity = dynamicName(instruction.outputs[index].valueId);
      dynamic.dst.push_back(identity);
      const auto storage = operandPlan.storage;
      dynamic.dstStorage.push_back(storage);
      dynamic.vectorDestCount += storage == ValueStorageKind::Register;
      dynamic.predicateDestCount +=
          storage == ValueStorageKind::PredicateRegister;
      if (runtime_.retainsDiagnostics) {
        ValueInstance instance{
            staticNames_.at(instruction.outputs[index].valueId),
            dynamic.iterationPath};
        instances_[identity] = instance;
        if (static_cast<CanonicalStorageKind>(storage) ==
            CanonicalStorageKind::UB) {
          for (const auto &id : ubAliases_.at(instance.definitionId))
            instances_[id] = instance;
        }
        dynamic.dstValueInstances.push_back(std::move(instance));
      }
      bindSlot(operandPlan.slot, identity);
    }
  }

  void expandMembar(const CanonicalMembar &membar) {
    countInstruction();
    auto &dynamic = runtime_.instructions.emplace_back();
    dynamic.type = "membar";
    dynamic.barrier = membar.barrier;
    if (runtime_.retainsDiagnostics)
      dynamic.staticInstructionId = membar.instructionId;
    dynamic.pc = staticPc_.at(membar.instructionId);
    fillDynamicMetadata(dynamic);
  }

  bool containsLoop(const CanonicalLoop &loop) const {
    return std::any_of(
        loop.body.begin(), loop.body.end(), [](const auto &node) {
          return std::holds_alternative<std::shared_ptr<const CanonicalLoop>>(
              node.payload);
        });
  }

  bool containsMembar(const CanonicalLoop &loop) const {
    return std::any_of(
        loop.body.begin(), loop.body.end(), [](const auto &node) {
          return std::holds_alternative<CanonicalMembar>(node.payload);
        });
  }

  std::vector<int64_t> nestedBounds(const CanonicalLoop &loop) const {
    std::vector<int64_t> bounds{
        resolveInteger(loop.count, vfInfo_.params, "loop count")};
    for (const auto &node : loop.body) {
      const auto *nested =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (nested != nullptr && *nested) {
        auto child = nestedBounds(**nested);
        bounds.insert(bounds.end(), child.begin(), child.end());
        break;
      }
    }
    return bounds;
  }

  void expandLoop(const CanonicalLoop &loop, int depth,
                  const StaticLoopPlan &plan) {
    const int64_t count =
        resolveInteger(loop.count, vfInfo_.params, "loop count");
    int64_t unroll = resolveInteger(loop.unroll, vfInfo_.params, "loop unroll");
    if (count < 0 || unroll <= 0)
      throw std::runtime_error("Invalid canonical loop count/unroll: " +
                               loop.loopId);
    const bool innermost = !containsLoop(loop);
    if (!innermost && unroll > 1)
      throw std::runtime_error(
          "Canonical non-innermost loop cannot use unroll > 1: " + loop.loopId);
    if (count % unroll != 0)
      throw std::runtime_error(
          "Canonical loop count is not divisible by unroll: " + loop.loopId);
    const bool hasMembar = containsMembar(loop);
    if (hasMembar && unroll > 1) {
      const auto membar = std::find_if(
          loop.body.begin(), loop.body.end(), [](const auto &node) {
            return std::holds_alternative<CanonicalMembar>(node.payload);
          });
      db_.recordWarning(
          "membar_unroll_disabled",
          {
              {"pc",
               staticPc_.at(
                   std::get<CanonicalMembar>(membar->payload).instructionId)},
              {"barrier", std::get<CanonicalMembar>(membar->payload).barrier},
              {"loop_id", staticLoopIds_.at(loop.loopId)},
              {"requested_unroll", unroll},
              {"used_unroll", int64_t{1}},
              {"reason", "membar_in_unrolled_innermost_loop"},
          });
      unroll = 1;
    }
    if (count > 0 && count % unroll != 0)
      throw std::runtime_error(
          "Canonical loop count is not divisible by unroll: " + loop.loopId);

    const int64_t topBlock = depth == 0 ? loopTopBlockIds_.at(loop.loopId)
                                        : frames_.front().topBlockId;
    if (depth == 0)
      runtime_.topBlockLoopBounds[static_cast<int>(topBlock)] =
          nestedBounds(loop);

    std::vector<std::string> currentValues;
    currentValues.reserve(plan.carriedEntry.size());
    for (size_t index = 0; index < plan.carriedEntry.size(); ++index)
      currentValues.push_back(plan.carriedEntry[index] >= 0
                                  ? resolveSlot(plan.carriedEntry[index])
                                  : std::string{});
    pushScope();

    for (int64_t base = 0; base < count; base += unroll) {
      std::vector<std::vector<DynamicInst>> lanes;
      for (int64_t lane = 0; lane < unroll; ++lane) {
        const int64_t iteration = base + lane;
        for (size_t index = 0; index < plan.carriedEntry.size(); ++index)
          if (plan.carriedEntry[index] >= 0)
            bindSlot(plan.carriedEntry[index], currentValues[index]);
        frames_.push_back(
            Frame{loop.loopId, iteration, base / unroll, topBlock,
                  loop.induction.variableId,
                  resolveInteger(loop.induction.start, vfInfo_.params,
                                 "induction start"),
                  resolveInteger(loop.induction.step, vfInfo_.params,
                                 "induction step")});
        const size_t start = runtime_.instructions.size();
        expandNodes(loop.body, depth + 1, plan.body);
        if (unroll > 1) {
          std::vector<DynamicInst> laneInstructions(
              std::make_move_iterator(runtime_.instructions.begin() +
                                      static_cast<std::ptrdiff_t>(start)),
              std::make_move_iterator(runtime_.instructions.end()));
          runtime_.instructions.erase(runtime_.instructions.begin() +
                                          static_cast<std::ptrdiff_t>(start),
                                      runtime_.instructions.end());
          lanes.push_back(std::move(laneInstructions));
        }
        frames_.pop_back();
        for (size_t index = 0; index < plan.carriedBackEdge.size(); ++index)
          currentValues[index] = resolveSlot(plan.carriedBackEdge[index]);
      }
      if (unroll > 1) {
        const size_t width = lanes.front().size();
        if (std::any_of(lanes.begin(), lanes.end(), [width](const auto &lane) {
              return lane.size() != width;
            }))
          throw std::runtime_error("Canonical unroll lanes produced "
                                   "inconsistent instruction counts");
        for (size_t instructionIndex = 0; instructionIndex < width;
             ++instructionIndex)
          for (auto &lane : lanes)
            runtime_.instructions.push_back(std::move(lane[instructionIndex]));
      }
    }

    popScope();
    for (size_t index = 0; index < plan.carriedExit.size(); ++index)
      bindSlot(plan.carriedExit[index],
               count == 0 ? resolveSlot(plan.carriedEntry[index])
                          : currentValues[index]);
  }

  void expandNodes(const std::vector<CanonicalNode> &nodes, int depth,
                   const std::vector<StaticNodePlan> &planNodes) {
    for (size_t index = 0; index < nodes.size(); ++index) {
      const auto &node = nodes[index];
      const auto &planNode = planNodes[index];
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        expandInstruction(*instruction, *planNode.instruction);
      } else if (const auto *membar =
                     std::get_if<CanonicalMembar>(&node.payload)) {
        expandMembar(*membar);
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        expandLoop(*loop, depth, *planNode.loop);
      }
    }
  }

  void expandTopLevelNodes(const std::vector<CanonicalNode> &nodes) {
    for (size_t nodeIndex = 0; nodeIndex < nodes.size(); ++nodeIndex) {
      const auto &node = nodes[nodeIndex];
      const auto &planNode = planRoot_[nodeIndex];
      const auto *loop =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loop != nullptr) {
        if (!*loop)
          throw std::runtime_error("Canonical loop payload is null");
        activeTopBlockId_ = loopTopBlockIds_.at((*loop)->loopId);
        expandLoop(**loop, 0, *planNode.loop);
        continue;
      }

      // Top-level straight-line nodes after a loop belong to that loop's
      // scheduling block until the next top-level loop starts. This keeps a
      // loop epilogue (for example VSTAS + Membar) ahead of the next VLOOP.
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload))
        expandInstruction(*instruction, *planNode.instruction);
      else
        expandMembar(std::get<CanonicalMembar>(node.payload));
    }
  }

  void annotateLifetimes() {
    // Reverse scan over the final emission order. One fused identity table
    // yields both the dense logical id and the lifetime bit, so each operand
    // needs a single hash probe and the integers are laid out in the same
    // order the renamer consumes them (sources right-to-left, then dsts).
    //   * srcValueRelease == first reverse occurrence of the identity as a
    //     source, i.e. its last use in the stream;
    //   * dstValueKeep == the identity already occurred as a source at or
    //     after this instruction.
    int64_t sourceCount = 0;
    int64_t destinationCount = 0;
    for (const auto &instruction : runtime_.instructions) {
      sourceCount += static_cast<int64_t>(instruction.src.size());
      destinationCount += static_cast<int64_t>(instruction.dst.size());
    }
    struct FusedEntry {
      int32_t id;
      bool seenAsSource;
    };
    // L1: scoped contiguous open-addressing table. Slots carry an explicit
    // occupied flag so the empty key is a valid identity; equality is always a
    // byte-wise string_view comparison (never hash equality). The table only
    // replaces the container: insertion order, id assignment, flags and all
    // fallback decisions are unchanged.
    struct FlatIdentityTable {
      struct Slot {
        std::string_view key;
        FusedEntry value{-1, false};
        bool occupied = false;
      };
      static size_t capacityFor(size_t expected) {
        size_t capacity = 16;
        while (capacity * 7 < (expected + 1) * 10)
          capacity <<= 1;
        return capacity;
      }
      static void place(std::vector<Slot> &slots, std::string_view key,
                        FusedEntry value) {
        const size_t mask = slots.size() - 1;
        size_t index = std::hash<std::string_view>{}(key) & mask;
        while (slots[index].occupied) {
          if (slots[index].key == key)
            return;
          index = (index + 1) & mask;
        }
        slots[index].key = key;
        slots[index].value = value;
        slots[index].occupied = true;
      }
      explicit FlatIdentityTable(size_t expected) {
        const size_t capacity = capacityFor(expected);
        slots_.resize(capacity);
      }
      void reserve(size_t expected) { (void)expected; }
      std::pair<FusedEntry *, bool> try_emplace(std::string_view key,
                                                FusedEntry value) {
        if ((size_ + 1) * 10 > slots_.size() * 7)
          grow();
        const size_t mask = slots_.size() - 1;
        size_t index = std::hash<std::string_view>{}(key) & mask;
        while (slots_[index].occupied) {
          if (slots_[index].key == key)
            return {&slots_[index].value, false};
          index = (index + 1) & mask;
        }
        slots_[index].key = key;
        slots_[index].value = value;
        slots_[index].occupied = true;
        ++size_;
        return {&slots_[index].value, true};
      }
      size_t size() const { return size_; }
      size_t bucket_count() const { return slots_.size(); }

    private:
      void grow() {
        std::vector<Slot> grown(slots_.size() * 2);
        for (const auto &slot : slots_)
          if (slot.occupied)
            place(grown, slot.key, slot.value);
        slots_.swap(grown);
      }
      std::vector<Slot> slots_;
      size_t size_ = 0;
    };
    // Fallback container: the historical std::unordered_map path, used when
    // the operand count is above the scoped flat-table limit.
    struct MapIdentityTable {
      void reserve(size_t expected) { map_.reserve(expected); }
      std::pair<FusedEntry *, bool> try_emplace(std::string_view key,
                                                FusedEntry value) {
        const auto result = map_.try_emplace(key, value);
        return {&result.first->second, result.second};
      }
      size_t size() const { return map_.size(); }
      size_t bucket_count() const { return map_.bucket_count(); }

    private:
      std::unordered_map<std::string_view, FusedEntry> map_;
    };
    int32_t nextValueId = 0;
    const int64_t totalOperandCount = sourceCount + destinationCount;
    bool idsEnabled = totalOperandCount <=
                      static_cast<int64_t>(std::numeric_limits<int32_t>::max());
    runtime_.operandIds.clear();
    if (idsEnabled)
      runtime_.operandIds.resize(static_cast<size_t>(totalOperandCount));
    int32_t nextOperandSlot = 0;
    int64_t instructionId = 0;
    int64_t streamSequence = 0;
    for (auto &instruction : runtime_.instructions) {
      instruction.streamSeq = streamSequence++;
      instruction.instId = instruction.type == "membar" ? -1 : instructionId++;
      instruction.srcValueRelease.assign(instruction.src.size(), false);
      instruction.dstValueKeep.assign(instruction.dst.size(), false);
    }
    auto annotateIdentities = [&](auto &identities) {
      for (size_t index = runtime_.instructions.size(); index > 0; --index) {
        auto &instruction = runtime_.instructions[index - 1];
        int32_t srcBase = -1;
        int32_t dstBase = -1;
        if (idsEnabled) {
          srcBase = nextOperandSlot;
          instruction.srcIdBase = srcBase;
          nextOperandSlot += static_cast<int32_t>(instruction.src.size());
          dstBase = nextOperandSlot;
          instruction.dstIdBase = dstBase;
          nextOperandSlot += static_cast<int32_t>(instruction.dst.size());
        }
        for (size_t srcIndex = instruction.src.size(); srcIndex > 0;
             --srcIndex) {
          const std::string &source = instruction.src[srcIndex - 1];
          const auto entryAndInsert = identities.try_emplace(
              source, FusedEntry{nextValueId, false});
          FusedEntry *entry = entryAndInsert.first;
          if (entryAndInsert.second) {
            if (nextValueId == std::numeric_limits<int32_t>::max())
              idsEnabled = false;
            else
              ++nextValueId;
          }
          if (idsEnabled)
            runtime_.operandIds[static_cast<size_t>(srcBase) +
                                (srcIndex - 1)] = entry->id;
          instruction.srcValueRelease[srcIndex - 1] = !entry->seenAsSource;
          entry->seenAsSource = true;
        }
        for (size_t dstIndex = 0; dstIndex < instruction.dst.size();
             ++dstIndex) {
          const auto entryAndInsert = identities.try_emplace(
              instruction.dst[dstIndex], FusedEntry{nextValueId, false});
          FusedEntry *entry = entryAndInsert.first;
          if (entryAndInsert.second)
            ++nextValueId;
          if (idsEnabled)
            runtime_.operandIds[static_cast<size_t>(dstBase) + dstIndex] =
                entry->id;
          instruction.dstValueKeep[dstIndex] = entry->seenAsSource;
        }
      }
    };
    if (totalOperandCount <= static_cast<int64_t>(
                                  VFSIM_LIFETIME_FLAT_LIMIT)) {
      FlatIdentityTable identities(static_cast<size_t>(sourceCount) * 3 / 4 + 1);
      annotateIdentities(identities);
    } else {
      MapIdentityTable identities;
      identities.reserve(static_cast<size_t>(sourceCount) + 1);
      annotateIdentities(identities);
    }
    if (!idsEnabled) {
      runtime_.operandIds.clear();
      for (auto &instruction : runtime_.instructions) {
        instruction.srcIdBase = -1;
        instruction.dstIdBase = -1;
      }
    }
  }

  void annotateSchedulingBoundaries() {
    // Typed-only in compact mode: identities are (topBlockId, depth, i, j) for
    // depth <= 2 and the historical text key for deeper shapes, so no strings
    // or prefix vectors are built on the hot path. Diagnostics keep the exact
    // historical text plus blockKeyByLevel.
    std::unordered_map<SchedulingKey, size_t, SchedulingKeyHash> lastByBlock;
    std::unordered_map<int64_t, size_t> lastByTopBlock;
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      lastByTopBlock[instruction.topBlockId] = index;
      const size_t depth = instruction.iterStack.size();
      instruction.schedulingKeys.clear();
      instruction.schedulingKeys.reserve(depth);
      if (runtime_.retainsDiagnostics) {
        instruction.normalizedBlockKeys.clear();
        instruction.normalizedBlockKeys.reserve(depth);
      }
      for (size_t level = 0; level < depth; ++level) {
        SchedulingKey typed;
        typed.topBlockId = instruction.topBlockId;
        typed.depth = static_cast<int32_t>(level);
        std::string text;
        if (level <= 2) {
          typed.c0 = level > 0 ? instruction.iterStack[0] : -1;
          typed.c1 = level > 1 ? instruction.iterStack[1] : -1;
        } else {
          text = std::to_string(instruction.topBlockId) + "|loop" +
                 std::to_string(level) + "|";
          for (size_t i = 0; i < level; ++i) {
            if (i)
              text += ",";
            text += std::to_string(instruction.iterStack[i]);
          }
          typed.slow = text;
        }
        if (runtime_.retainsDiagnostics) {
          if (text.empty()) {
            text = std::to_string(instruction.topBlockId) + "|loop" +
                   std::to_string(level) + "|";
            for (size_t i = 0; i < level; ++i) {
              if (i)
                text += ",";
              text += std::to_string(instruction.iterStack[i]);
            }
          }
          std::vector<int64_t> prefix(instruction.iterStack.begin(),
                                      instruction.iterStack.begin() +
                                          static_cast<std::ptrdiff_t>(level));
          instruction.blockKeyByLevel.emplace_back(
              "loop" + std::to_string(level), prefix);
          instruction.normalizedBlockKeys.push_back(text);
        }
        lastByBlock[typed] = index;
        instruction.schedulingKeys.push_back(std::move(typed));
      }
    }
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      for (int64_t level =
               static_cast<int64_t>(instruction.schedulingKeys.size()) - 1;
           level >= 0; --level) {
        if (lastByBlock[instruction.schedulingKeys[static_cast<size_t>(level)]] ==
            index)
          instruction.blockEndLevels.push_back(level);
      }
      instruction.isLastInTopBlock =
          lastByTopBlock[instruction.topBlockId] == index;
    }
  }
};

} // namespace
PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &vf, const ParamDB &db, const UarchConfig &cfg,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics) {
  return expandCanonicalProgram(vf, vf.context, db, cfg, std::move(values),
                                retainDiagnostics);
}

PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &vf, const std::vector<CanonicalNode> &context,
    const ParamDB &db, const UarchConfig &cfg,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics) {
  return Expander(vf, context, db, cfg, std::move(values), retainDiagnostics)
      .run();
}
} // namespace vfsim
