// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/execution/expansion.h"

#include <algorithm>
#include <cmath>
#include <map>
#include <set>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <unordered_set>

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
  explicit Expander(const CanonicalVfInfo &vfInfo, const ParamDB &db,
                    const UarchConfig &cfg,
                    std::unordered_map<std::string, ValueInfo> values)
      : vfInfo_(vfInfo), db_(db), cfg_(cfg) {
    runtime_.params = vfInfo.params;
    runtime_.values = std::move(values);
    indexLoops(vfInfo.context, 0);
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
      staticNames_.emplace(id, name);
      instances_.emplace(id, ValueInstance{name, {}});
    }
  }

  PreparedProgram run() {
    expandTopLevelNodes(vfInfo_.context);
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
  const ParamDB &db_;
  const UarchConfig &cfg_;
  int64_t dynamicCount_ = 0;
  void countInstruction() {
    if (cfg_.canonicalDynamicInstructionLimit > 0 &&
        ++dynamicCount_ > cfg_.canonicalDynamicInstructionLimit)
      throw std::runtime_error("Canonical dynamic instruction limit exceeded");
  }
  PreparedProgram runtime_;
  std::unordered_map<std::string, std::string> bindings_;
  std::unordered_map<std::string, int64_t> loopTopBlockIds_;
  std::vector<Frame> frames_;
  std::unordered_map<std::string, ValueInstance> instances_;
  std::unordered_map<std::string, std::string> staticNames_;
  std::unordered_map<std::string, int64_t> staticLoopIds_, staticPc_;
  int64_t nextPc_ = 0;
  int64_t nextTopBlockId_ = 0;
  int64_t activeTopBlockId_ = 0;

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

  std::string resolve(const std::string &definitionId) const {
    const auto found = bindings_.find(definitionId);
    return found == bindings_.end() ? definitionId : found->second;
  }

  std::string dynamicName(const std::string &definitionId) {
    if (frames_.empty())
      return definitionId;
    std::ostringstream name;
    name << definitionId;
    for (const auto &frame : frames_)
      name << "@" << frame.loopId << "=" << frame.iteration;
    const std::string identity = name.str();
    const auto source = vfInfo_.values.find(definitionId);
    if (source != vfInfo_.values.end()) {
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
    for (const auto &frame : frames_) {
      inst.iterStack.push_back(frame.scheduledIteration);
      inst.iterationPath.push_back(
          {frame.loopId, frame.iteration, frame.variable,
           frame.start + frame.iteration * frame.step});
    }
  }

  void expandInstruction(const CanonicalInstruction &instruction) {
    countInstruction();
    DynamicInst dynamic;
    dynamic.type = "inst";
    dynamic.op = instruction.opcode;
    dynamic.opClass =
        instruction.instructionClass == CanonicalInstructionClass::Load ? "LOAD"
        : instruction.instructionClass == CanonicalInstructionClass::Store
            ? "STORE"
            : "COMPUTE";
    dynamic.form = instruction.form;
    if (auto mode = instruction.attributes.find("catalog_mode");
        mode != instruction.attributes.end())
      dynamic.catalogMode = std::get<std::string>(mode->second);
    dynamic.staticInstructionId = instruction.instructionId;
    dynamic.pc = staticPc_.at(instruction.instructionId);
    for (const auto &operand : instruction.inputs)
      if (operand.memoryAccess)
        dynamic.memoryAccesses.push_back(*operand.memoryAccess);
    for (const auto &operand : instruction.outputs)
      if (operand.memoryAccess)
        dynamic.memoryAccesses.push_back(*operand.memoryAccess);
    const auto alignOperation =
        instruction.attributes.find("align_state_operation");
    if (alignOperation != instruction.attributes.end()) {
      const auto *value = std::get_if<std::string>(&alignOperation->second);
      if (value == nullptr)
        throw std::runtime_error("align_state_operation must be a string: " +
                                 instruction.instructionId);
      dynamic.alignStateOperation = *value;
    }
    const auto alignState = instruction.attributes.find("align_state_id");
    if (alignState != instruction.attributes.end()) {
      const auto *value = std::get_if<std::string>(&alignState->second);
      if (value == nullptr)
        throw std::runtime_error("align_state_id must be a string: " +
                                 instruction.instructionId);
      dynamic.alignStateId = *value;
    }
    fillDynamicMetadata(dynamic);
    for (const auto &operand : instruction.inputs) {
      const auto identity = resolve(operand.valueId);
      dynamic.src.push_back(identity);
      dynamic.srcValueInstances.push_back(instances_.at(identity));
    }
    for (const auto &operand : instruction.outputs) {
      const std::string identity = dynamicName(operand.valueId);
      dynamic.dst.push_back(identity);
      ValueInstance instance{staticNames_.at(operand.valueId),
                             dynamic.iterationPath};
      instances_[identity] = instance;
      if (vfInfo_.values.at(operand.valueId).storage ==
          CanonicalStorageKind::UB) {
        for (const auto &[id, value] : vfInfo_.values)
          if (value.storage == CanonicalStorageKind::UB &&
              staticNames_.at(id) == instance.definitionId)
            instances_[id] = instance;
      }
      dynamic.dstValueInstances.push_back(std::move(instance));
      bindings_[operand.valueId] = identity;
    }
    runtime_.instructions.push_back(std::move(dynamic));
  }

  void expandMembar(const CanonicalMembar &membar) {
    countInstruction();
    DynamicInst dynamic;
    dynamic.type = "membar";
    dynamic.barrier = membar.barrier;
    dynamic.staticInstructionId = membar.instructionId;
    dynamic.pc = staticPc_.at(membar.instructionId);
    fillDynamicMetadata(dynamic);
    runtime_.instructions.push_back(std::move(dynamic));
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

  void expandLoop(const CanonicalLoop &loop, int depth) {
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

    const auto outerBindings = bindings_;
    std::vector<std::string> currentValues;
    currentValues.reserve(loop.carriedValues.size());
    for (const auto &carried : loop.carriedValues)
      currentValues.push_back(carried.entryValueId
                                  ? resolve(*carried.entryValueId)
                                  : std::string{});

    for (int64_t base = 0; base < count; base += unroll) {
      std::vector<std::vector<DynamicInst>> lanes;
      for (int64_t lane = 0; lane < unroll; ++lane) {
        const int64_t iteration = base + lane;
        for (size_t index = 0; index < loop.carriedValues.size(); ++index)
          if (loop.carriedValues[index].entryValueId)
            bindings_[*loop.carriedValues[index].entryValueId] =
                currentValues[index];
        frames_.push_back(
            Frame{loop.loopId, iteration, base / unroll, topBlock,
                  loop.induction.variableId,
                  resolveInteger(loop.induction.start, vfInfo_.params,
                                 "induction start"),
                  resolveInteger(loop.induction.step, vfInfo_.params,
                                 "induction step")});
        const size_t start = runtime_.instructions.size();
        expandNodes(loop.body, depth + 1);
        std::vector<DynamicInst> laneInstructions(
            std::make_move_iterator(runtime_.instructions.begin() +
                                    static_cast<std::ptrdiff_t>(start)),
            std::make_move_iterator(runtime_.instructions.end()));
        runtime_.instructions.erase(runtime_.instructions.begin() +
                                        static_cast<std::ptrdiff_t>(start),
                                    runtime_.instructions.end());
        frames_.pop_back();
        for (size_t index = 0; index < loop.carriedValues.size(); ++index)
          currentValues[index] =
              resolve(loop.carriedValues[index].backEdgeValueId);
        lanes.push_back(std::move(laneInstructions));
      }
      if (unroll == 1) {
        for (auto &instruction : lanes.front())
          runtime_.instructions.push_back(std::move(instruction));
      } else {
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

    bindings_ = outerBindings;
    for (size_t index = 0; index < loop.carriedValues.size(); ++index) {
      const auto &carried = loop.carriedValues[index];
      bindings_[carried.exitValueId] =
          count == 0 ? resolve(carried.entryValueId.value())
                     : currentValues[index];
    }
  }

  void expandNodes(const std::vector<CanonicalNode> &nodes, int depth) {
    for (const auto &node : nodes) {
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        expandInstruction(*instruction);
      } else if (const auto *membar =
                     std::get_if<CanonicalMembar>(&node.payload)) {
        expandMembar(*membar);
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        expandLoop(*loop, depth);
      }
    }
  }

  void expandTopLevelNodes(const std::vector<CanonicalNode> &nodes) {
    for (const auto &node : nodes) {
      const auto *loop =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loop != nullptr) {
        if (!*loop)
          throw std::runtime_error("Canonical loop payload is null");
        activeTopBlockId_ = loopTopBlockIds_.at((*loop)->loopId);
        expandLoop(**loop, 0);
        continue;
      }

      // Top-level straight-line nodes after a loop belong to that loop's
      // scheduling block until the next top-level loop starts. This keeps a
      // loop epilogue (for example VSTAS + Membar) ahead of the next VLOOP.
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload))
        expandInstruction(*instruction);
      else
        expandMembar(std::get<CanonicalMembar>(node.payload));
    }
  }

  void annotateLifetimes() {
    std::unordered_map<std::string, int64_t> remainingUses;
    for (const auto &instruction : runtime_.instructions)
      for (const auto &source : instruction.src)
        ++remainingUses[source];

    int64_t instructionId = 0;
    int64_t streamSequence = 0;
    for (auto &instruction : runtime_.instructions) {
      instruction.streamSeq = streamSequence++;
      instruction.instId = instruction.type == "membar" ? -1 : instructionId++;
      for (const auto &destination : instruction.dst)
        instruction.dstValueKeep.push_back(remainingUses[destination] > 0);
      for (const auto &source : instruction.src) {
        const int64_t remaining = --remainingUses[source];
        instruction.srcValueRelease.push_back(remaining == 0);
      }
    }
  }

  void annotateSchedulingBoundaries() {
    std::unordered_map<std::string, size_t> lastByBlock;
    std::unordered_map<int64_t, size_t> lastByTopBlock;
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      lastByTopBlock[instruction.topBlockId] = index;
      for (size_t level = 0; level < instruction.iterationPath.size();
           ++level) {
        std::vector<int64_t> prefix(instruction.iterStack.begin(),
                                    instruction.iterStack.begin() +
                                        static_cast<std::ptrdiff_t>(level));
        instruction.blockKeyByLevel.emplace_back("loop" + std::to_string(level),
                                                 prefix);
        std::ostringstream key;
        key << instruction.topBlockId << ":" << level;
        for (const auto value : prefix)
          key << ":" << value;
        lastByBlock[key.str()] = index;
      }
    }
    for (size_t index = 0; index < runtime_.instructions.size(); ++index) {
      auto &instruction = runtime_.instructions[index];
      if (instruction.type != "inst")
        continue;
      for (int64_t level =
               static_cast<int64_t>(instruction.iterationPath.size()) - 1;
           level >= 0; --level) {
        std::ostringstream key;
        key << instruction.topBlockId << ":" << level;
        for (int64_t prefix = 0; prefix < level; ++prefix)
          key << ":" << instruction.iterStack[static_cast<size_t>(prefix)];
        if (lastByBlock[key.str()] == index)
          instruction.blockEndLevels.push_back(level);
      }
      instruction.isLastInTopBlock =
          lastByTopBlock[instruction.topBlockId] == index;
    }
  }
};

} // namespace
PreparedProgram
expandCanonicalProgram(const CanonicalVfInfo &vf, const ParamDB &db,
                       const UarchConfig &cfg,
                       std::unordered_map<std::string, ValueInfo> values) {
  return Expander(vf, db, cfg, std::move(values)).run();
}
} // namespace vfsim
