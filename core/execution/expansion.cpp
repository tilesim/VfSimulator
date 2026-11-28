#include <cstdio>
#include "core/execution/cursor.h"
#include "core/execution/optimization.h"
#include "core/execution/cursor_lifetime.h"
// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/execution/expansion.h"

#include <algorithm>
#include <cmath>
#include <deque>
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

namespace vfsim { class Cursor; }
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

// Numeric value tokens for the streamed scoring path. A static token is the
// dense static operand slot (stable per declared value); a dynamic token is
// that flag plus the cursor's monotonic identity counter. Tokens are lookup
// keys only: no table is indexed by them, so nothing grows with the identity
// value, the trip count or the dynamic event count.
constexpr uint64_t kDynamicValueToken = uint64_t{1} << 63;
inline uint64_t staticValueToken(int32_t slot) {
  return static_cast<uint64_t>(static_cast<uint32_t>(slot));
}
inline bool isDynamicValueToken(uint64_t token) {
  return (token & kDynamicValueToken) != 0;
}

// Stable spellings of the instruction classes. The streamed lowering borrows a
// pointer to one of these instead of assigning a std::string per event.
const std::string kOpClassCompute = "COMPUTE";
const std::string kOpClassLoad = "LOAD";
const std::string kOpClassStore = "STORE";

// Borrowed view over one half-open range of a flat plan store. Plan stores are
// append-only while the tree is built, so the views are materialised once, after
// the build, and never move again.
template <typename T> struct PlanSpan {
  const T *ptr = nullptr;
  size_t count = 0;
  // Named ptr, with a data() accessor: the lowering calls .data() on the spans.
  const T *data() const { return ptr; }
  const T *begin() const { return ptr; }
  const T *end() const { return ptr + count; }
  size_t size() const { return count; }
  bool empty() const { return count == 0; }
  const T &operator[](size_t index) const { return ptr[index]; }
};

// Index range inside a flat plan store. Ranges, not pointers: the stores grow
// while the plan tree is built.
struct StaticPlanRange {
  size_t base = 0;
  size_t count = 0;
};

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
                    bool retainDiagnostics, bool keepInstanceMetadata,
                    bool numericTokens = false)
      : vfInfo_(vfInfo), context_(context), db_(db), cfg_(cfg),
        keepInstanceMetadata_(keepInstanceMetadata) {
    runtime_.retainsDiagnostics = retainDiagnostics;
    // Numeric tokens are a property of the lowering (the streamed scoring
    // cursor), not of "no diagnostics": the eager cost-only preparation also
    // drops diagnostics but keeps its identity strings.
    tokens_ = numericTokens;
    runtime_.params = vfInfo.params;
    runtime_.values = std::move(values);
    indexLoops(context_, 0);
    buildPlanRoot(context_);
    // The plan stores no longer grow past this point, so the borrowed views are
    // materialised once here.
    finalizePlanViews(planRoot_);
    if (tokens_) {
      // The scored lowering keys every operand by dense slot index. The name
      // interning table is a build-time aid only, so release it (with its bucket
      // array) now instead of holding it until the cursor is destroyed.
      std::unordered_map<std::string_view, int32_t>().swap(slotViewOf_);
    } else {
      slotValue_.resize(slotStatic_.size());
    }
    slotToken_.assign(slotCount_, 0);
    slotBound_.assign(slotCount_, 0);
    slotOwner_.assign(slotCount_, -1);
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

  // Static metadata for the streamed scoring path: exactly the fields the
  // Engine's static consumers read (LoopControl: totalTopBlocks /
  // topBlockLoopBounds / emptyTopBlocks; Registers/IDU: declared values;
  // Engine::run: params), built from one O(static) walk. Nothing here grows
  // with the dynamic instruction count: no DynamicInst, no operand id table,
  // no per-instance value identity.
  PreparedProgram runStaticMetadata(int64_t linearInstCount) {
    runtime_.linearInstCount = linearInstCount;
    int64_t events = 0;
    runtime_.dynamicEventCount = countDynamicEvents(context_, events) ? events : -1;
    runtime_.totalTopBlocks = std::max<int64_t>(1, nextTopBlockId_);
    std::unordered_set<int64_t> liveTopBlocks;
    int64_t currentTopBlock = 0;
    markLiveTopBlocks(context_, true, currentTopBlock, liveTopBlocks);
    for (int64_t topBlockId = 0; topBlockId < runtime_.totalTopBlocks;
         ++topBlockId) {
      if (!liveTopBlocks.count(topBlockId))
        runtime_.emptyTopBlocks.insert(topBlockId);
    }
    for (const auto &node : context_) {
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr)
        continue;
      runtime_.topBlockLoopBounds[static_cast<int>(
          loopTopBlockIds_.at((*loopPtr)->loopId))] = nestedBounds(**loopPtr);
    }
    if (runtime_.topBlockLoopBounds.empty())
      runtime_.topBlockLoopBounds.emplace(0, std::vector<int64_t>{});
    return std::move(runtime_);
  }

  // Optional diagnostic sink for the per-event lowering split.
  LoweringProfile *profileTarget_ = nullptr;

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

  friend class Cursor;

public:
  const CanonicalVfInfo &vfInfo_;
  const std::vector<CanonicalNode> &context_;
  const ParamDB &db_;
  const UarchConfig &cfg_;
  bool keepInstanceMetadata_ = true;
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
    // Ranges recorded while the tree is built; the borrowed views below are
    // materialised from them once the flat stores are complete.
    StaticPlanRange inputRange, outputRange;
    StaticPlanRange inputStorageRange, outputStorageRange;
    StaticPlanRange accessRange, accessCopyRange;
    PlanSpan<StaticOperandPlan> inputs;
    PlanSpan<StaticOperandPlan> outputs;
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
    PlanSpan<const CanonicalMemoryAccess *> memoryAccesses;
    const char *opClass = "COMPUTE";
    // ---- static halves the streamed lowering borrows per event -----------
    // The canonical instruction is immutable and outlives the run, so opcode and
    // form are read straight out of it. Storage classes are interned once as one
    // contiguous array per side; the memory-access list is copied once here
    // because the canonical accesses hang off individual operands rather than
    // one contiguous array. Destination counts are static too.
    const std::string *opClassRef = &kOpClassCompute;
    PlanSpan<ValueStorageKind> inputStorage, outputStorage;
    PlanSpan<CanonicalMemoryAccess> memoryAccessCopies;
    int32_t vectorDstCount = 0, predicateDstCount = 0;
  };
  struct StaticLoopPlan;
  struct StaticNodePlan {
    const Expander::StaticInstructionPlan *instruction = nullptr;
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
  // Flat, append-only plan stores. One buffer per kind replaces the six small
  // vectors every static instruction used to allocate for itself, which on a
  // planar model with thousands of instructions was tens of thousands of heap
  // objects to allocate during the preparation and to release at teardown.
  std::vector<StaticOperandPlan> operandPlans_;
  std::vector<ValueStorageKind> storagePlans_;
  std::vector<const CanonicalMemoryAccess *> accessPointerPlans_;
  std::vector<CanonicalMemoryAccess> accessCopyPlans_;
  std::unordered_map<std::string, int32_t> slotOf_;
  // Scored lowering: operands are interned through borrowed string_views (the
  // canonical value ids live in the context, which outlives the plan), so no
  // operand name is copied or retained.
  std::unordered_map<std::string_view, int32_t> slotViewOf_;
  int32_t slotCount_ = 0;
  std::vector<std::string> slotStatic_;
  // Diagnostic bindings are identity strings; scoring bindings are tokens.
  // Exactly one array is live for a run (tokens_ selects which).
  std::vector<std::string> slotValue_;
  std::vector<uint64_t> slotToken_;
  std::vector<uint8_t> slotBound_;
  std::vector<int32_t> slotOwner_;
  // True for the streamed scoring path: bindings are numeric tokens, no
  // diagnostic identity string is ever built per event.
  bool tokens_ = false;
  uint64_t nextDynamicToken_ = 0;
  struct SlotUndo {
    int32_t slot;
    std::string previous;
    uint64_t previousToken = 0;
    uint8_t wasBound;
    int32_t previousOwner;
  };
  std::vector<SlotUndo> slotUndo_;
  std::vector<size_t> scopeMarks_;
  // Keys are ids of canonical nodes owned (or borrowed) by the plan context,
  // which outlives the Expander, so the maps borrow views instead of copying
  // every loop/instruction id (12,274 allocations + 1.03 MB on the 1,351-node
  // planar RoPE model).
  std::unordered_map<std::string_view, int64_t> loopTopBlockIds_;
  std::vector<Frame> frames_;
  std::unordered_map<std::string, ValueInstance> instances_;
  std::unordered_map<std::string, std::string> staticNames_;
  // UB versions share one storage object. Index aliases once instead of
  // rescanning all register/scalar definitions at every dynamic store.
  std::unordered_map<std::string, std::vector<std::string>> ubAliases_;
  std::unordered_map<std::string_view, int64_t> staticLoopIds_, staticPc_;
  int64_t nextValueId_ = 0;
  int64_t nextPc_ = 0;
  int64_t nextTopBlockId_ = 0;
  int64_t activeTopBlockId_ = 0;

  // Exact dynamic event total from the static graph and its trip counts.
  // Saturates to -1 on overflow so callers fall back to a probe-based stream.
  bool countDynamicEvents(const std::vector<CanonicalNode> &nodes,
                          int64_t &total) const {
    for (const auto &node : nodes) {
      if (const auto *loop = std::get_if<std::shared_ptr<const CanonicalLoop>>(
              &node.payload)) {
        if (!*loop)
          continue;
        const int64_t trips =
            resolveInteger((*loop)->count, vfInfo_.params, "loop count");
        if (trips <= 0)
          continue;
        int64_t body = 0;
        if (!countDynamicEvents((*loop)->body, body))
          return false;
        if (body != 0 &&
            trips > std::numeric_limits<int64_t>::max() / body)
          return false;
        const int64_t span = trips * body;
        if (total > std::numeric_limits<int64_t>::max() - span)
          return false;
        total += span;
        continue;
      }
      if (total == std::numeric_limits<int64_t>::max())
        return false;
      ++total;
    }
    return true;
  }

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
    // The scored lowering never resolves a slot back to its name, so it interns
    // through a borrowed view of the canonical id and keeps no copy.
    if (tokens_) {
      const auto [position, inserted] =
          slotViewOf_.try_emplace(std::string_view(valueId), slotCount_);
      if (inserted)
        ++slotCount_;
      return position->second;
    }
    const auto [position, inserted] =
        slotOf_.try_emplace(valueId, static_cast<int32_t>(slotStatic_.size()));
    if (inserted) {
      slotStatic_.push_back(valueId);
      ++slotCount_;
    }
    return position->second;
  }

  void buildPlanOperands(const CanonicalInstruction &instruction,
                         StaticInstructionPlan &plan) {
    plan.inputRange.base = operandPlans_.size();
    for (const auto &operand : instruction.inputs)
      operandPlans_.push_back(
          {slotFor(operand.valueId), vfInfo_.values.at(operand.valueId).storage});
    plan.inputRange.count = operandPlans_.size() - plan.inputRange.base;
    plan.outputRange.base = operandPlans_.size();
    for (const auto &operand : instruction.outputs)
      operandPlans_.push_back(
          {slotFor(operand.valueId), vfInfo_.values.at(operand.valueId).storage});
    plan.outputRange.count = operandPlans_.size() - plan.outputRange.base;
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
    plan.accessRange.base = accessPointerPlans_.size();
    for (const auto &operand : instruction.inputs)
      if (operand.memoryAccess)
        accessPointerPlans_.push_back(&*operand.memoryAccess);
    for (const auto &operand : instruction.outputs)
      if (operand.memoryAccess)
        accessPointerPlans_.push_back(&*operand.memoryAccess);
    plan.accessRange.count = accessPointerPlans_.size() - plan.accessRange.base;
    plan.opClassRef =
        instruction.instructionClass == CanonicalInstructionClass::Load
            ? &kOpClassLoad
            : instruction.instructionClass == CanonicalInstructionClass::Store
                  ? &kOpClassStore
                  : &kOpClassCompute;
    // The borrowed halves are consumed only by the scored (numeric-token)
    // lowering. Guarding them here keeps the eager Expander's prepare cost and
    // memory exactly as before, so an eager run is not charged for them.
    if (tokens_) {
      plan.inputStorageRange.base = storagePlans_.size();
      for (size_t index = plan.inputRange.base;
           index < plan.inputRange.base + plan.inputRange.count; ++index)
        storagePlans_.push_back(operandPlans_[index].storage);
      plan.inputStorageRange.count =
          storagePlans_.size() - plan.inputStorageRange.base;
      plan.outputStorageRange.base = storagePlans_.size();
      plan.vectorDstCount = plan.predicateDstCount = 0;
      for (size_t index = plan.outputRange.base;
           index < plan.outputRange.base + plan.outputRange.count; ++index) {
        const auto storage = operandPlans_[index].storage;
        storagePlans_.push_back(storage);
        plan.vectorDstCount += storage == ValueStorageKind::Register;
        plan.predicateDstCount += storage == ValueStorageKind::PredicateRegister;
      }
      plan.outputStorageRange.count =
          storagePlans_.size() - plan.outputStorageRange.base;
      plan.accessCopyRange.base = accessCopyPlans_.size();
      for (size_t index = plan.accessRange.base;
           index < plan.accessRange.base + plan.accessRange.count; ++index)
        accessCopyPlans_.push_back(*accessPointerPlans_[index]);
      plan.accessCopyRange.count =
          accessCopyPlans_.size() - plan.accessCopyRange.base;
    }
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

  // Borrowed view of one range. An empty range yields an empty span with no
  // pointer: the stores may not have been allocated at all for that kind, and
  // "data() + 0" on a null data pointer is not a defined value.
  template <typename T>
  static PlanSpan<T> viewOf(const std::vector<T> &store,
                            const StaticPlanRange &range) {
    if (range.count == 0)
      return PlanSpan<T>{};
    return PlanSpan<T>{store.data() + range.base, range.count};
  }

  // Materialise the borrowed plan views from the recorded ranges once the flat
  // stores are complete. Walks the same tree preorder the lowering walks.
  void finalizePlanViews(std::vector<StaticNodePlan> &nodes) {
    for (auto &planNode : nodes) {
      if (planNode.instruction != nullptr) {
        auto &plan = *const_cast<StaticInstructionPlan *>(planNode.instruction);
        plan.inputs = viewOf(operandPlans_, plan.inputRange);
        plan.outputs = viewOf(operandPlans_, plan.outputRange);
        plan.inputStorage = viewOf(storagePlans_, plan.inputStorageRange);
        plan.outputStorage = viewOf(storagePlans_, plan.outputStorageRange);
        plan.memoryAccesses = viewOf(accessPointerPlans_, plan.accessRange);
        plan.memoryAccessCopies = viewOf(accessCopyPlans_, plan.accessCopyRange);
      }
      if (planNode.loop != nullptr) {
        auto &loop = *const_cast<StaticLoopPlan *>(planNode.loop);
        finalizePlanViews(loop.body);
      }
    }
  }

  void buildPlanRoot(const std::vector<CanonicalNode> &nodes) {
    // Children reserve too; reserving here keeps the flat stores from being
    // regrown per body for the common case of one big planar body.
    const size_t estimate = std::max<size_t>(nodes.size(), 1);
    operandPlans_.reserve(estimate * 3);
    storagePlans_.reserve(estimate * 2);
    accessPointerPlans_.reserve(estimate);
    accessCopyPlans_.reserve(estimate);
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

  // Diagnostic name of a slot. The scored lowering keys operands by dense slot
  // index and keeps no name store at all (slotValue_/slotStatic_ are not sized
  // for it), so reaching this function there means wrong dispatch: refuse before
  // touching either store instead of indexing a buffer that was never built.
  const std::string &resolveSlot(int32_t slot) const {
    if (tokens_)
      throw std::runtime_error(
          "Canonical slot name is not retained on the scored lowering");
    if (slotBound_[slot])
      return slotValue_[slot];
    return slotStatic_[slot];
  }

  // Scoring counterpart of resolveSlot: an unbound slot resolves to its static
  // token, which is exactly the value a root/static definition would have been
  // bound to (the string path returns the declared value id in that case).
  uint64_t resolveSlotToken(int32_t slot) const {
    if (slotBound_[slot])
      return slotToken_[slot];
    return staticValueToken(slot);
  }

  void pushScope() { scopeMarks_.push_back(slotUndo_.size()); }

  void popScope() {
    const size_t mark = scopeMarks_.back();
    scopeMarks_.pop_back();
    while (slotUndo_.size() > mark) {
      SlotUndo &undo = slotUndo_.back();
      if (tokens_)
        slotToken_[undo.slot] = undo.previousToken;
      else
        slotValue_[undo.slot] = std::move(undo.previous);
      slotBound_[undo.slot] = undo.wasBound;
      slotOwner_[undo.slot] = undo.previousOwner;
      slotUndo_.pop_back();
    }
  }

  // Same fail-closed guard on the writing side: the scored lowering binds
  // numeric tokens through bindSlotToken, so a diagnostic identity binding here
  // would otherwise write into the unsized string store.
  void bindSlot(int32_t slot, const std::string &name) {
    if (tokens_)
      throw std::runtime_error(
          "Canonical identity binding used on the scored lowering");
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
    slotUndo_.push_back({slot, slotValue_[slot], 0, slotBound_[slot],
                         slotOwner_[slot]});
    slotOwner_[slot] = static_cast<int32_t>(slotUndo_.size() - 1);
    slotValue_[slot] = name;
    slotBound_[slot] = 1;
  }

  // Numeric binding restore used by the scoring lowering: same scope/undo rules
  // as bindSlot, but the payload is a uint64 token instead of an identity
  // string, so no allocation happens on the hot path.
  void bindSlotToken(int32_t slot, uint64_t token) {
    if (slotBound_[slot] && slotToken_[slot] == token)
      return;
    if (scopeMarks_.empty()) {
      slotToken_[slot] = token;
      slotBound_[slot] = 1;
      return;
    }
    const size_t mark = scopeMarks_.back();
    if (slotOwner_[slot] >= 0 &&
        static_cast<size_t>(slotOwner_[slot]) >= mark) {
      slotToken_[slot] = token;
      slotBound_[slot] = 1;
      return;
    }
    slotUndo_.push_back({slot, std::string{}, slotToken_[slot], slotBound_[slot],
                         slotOwner_[slot]});
    slotOwner_[slot] = static_cast<int32_t>(slotUndo_.size() - 1);
    slotToken_[slot] = token;
    slotBound_[slot] = 1;
  }

  // Scoring counterpart of dynamicName: root/static definitions keep their
  // stable static slot token (one token for the whole run, so a definition that
  // lives across iterations/top blocks stays the same identity), while every
  // materialised loop instance gets a fresh monotonic token. Aliases of one
  // static operand share that operand's slot token by construction.
  uint64_t outputToken(uint64_t staticSlotToken) {
    if (frames_.empty())
      return staticSlotToken;
    if (nextDynamicToken_ == kDynamicValueToken)
      throw std::runtime_error("Canonical dynamic value identity overflow");
    return kDynamicValueToken | nextDynamicToken_++;
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
      value.streamLoop =
          frames_.empty() ? -1 : static_cast<int32_t>(frames_.front().topBlockId);
      runtime_.values.emplace(identity, std::move(value));
    }
    return identity;
  }

  void fillDynamicMetadata(DynamicInst &inst) const {
    inst.topBlockId =
        frames_.empty() ? activeTopBlockId_ : frames_.front().topBlockId;
    inst.iterStack.reserve(frames_.size());
    if (runtime_.retainsDiagnostics && keepInstanceMetadata_)
      inst.iterationPath.reserve(frames_.size());
    for (const auto &frame : frames_) {
      inst.iterStack.push_back(frame.scheduledIteration);
      if (runtime_.retainsDiagnostics && keepInstanceMetadata_)
        inst.iterationPath.push_back(
            {frame.loopId, frame.iteration, frame.variable,
             frame.start + frame.iteration * frame.step});
    }
  }

  void expandInstruction(const CanonicalInstruction &instruction,
                         const Expander::StaticInstructionPlan &plan,
                         DynamicInst &dynamic) {
    countInstruction();
    dynamic.type = "inst";
#ifdef VFSIM_ABLATE_INVARIANT
    dynamic.op = "VADD";
    dynamic.opClass = "COMPUTE";
#else
    dynamic.op = instruction.opcode;
    dynamic.opClass = plan.opClass;
    dynamic.form = instruction.form;
    if (plan.catalogMode != nullptr)
      dynamic.catalogMode = std::get<std::string>(*plan.catalogMode);
#endif
    if (runtime_.retainsDiagnostics)
      dynamic.staticInstructionId = instruction.instructionId;
    if (tokens_) {
      dynamic.srcTokens.reserve(plan.inputs.size());
      dynamic.dstTokens.reserve(plan.outputs.size());
    } else {
      dynamic.src.reserve(plan.inputs.size());
      dynamic.dst.reserve(plan.outputs.size());
    }
    dynamic.srcStorage.reserve(plan.inputs.size());
    dynamic.dstStorage.reserve(plan.outputs.size());
    dynamic.pc = plan.pc;
#ifndef VFSIM_ABLATE_INVARIANT
    for (const auto *access : plan.memoryAccesses)
      dynamic.memoryAccesses.push_back(*access);
#endif
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
    if (tokens_) {
      // Scoring lowering: numeric tokens only. No identity string is built, so
      // the per-event cost cannot depend on a name length and the Engine RAT is
      // keyed by a uint64 token instead of a hashed string.
      for (const auto &operandPlan : plan.inputs) {
        dynamic.srcStorage.push_back(operandPlan.storage);
        dynamic.srcTokens.push_back(resolveSlotToken(operandPlan.slot));
      }
      for (size_t index = 0; index < plan.outputs.size(); ++index) {
        const auto &operandPlan = plan.outputs[index];
        const uint64_t token = outputToken(staticValueToken(operandPlan.slot));
        dynamic.dstTokens.push_back(token);
        const auto storage = operandPlan.storage;
        dynamic.dstStorage.push_back(storage);
        dynamic.vectorDestCount += storage == ValueStorageKind::Register;
        dynamic.predicateDestCount +=
            storage == ValueStorageKind::PredicateRegister;
        bindSlotToken(operandPlan.slot, token);
      }
      return;
    }
    for (size_t index = 0; index < plan.inputs.size(); ++index) {
      const auto &operandPlan = plan.inputs[index];
      dynamic.srcStorage.push_back(operandPlan.storage);
      const std::string &identity = resolveSlot(operandPlan.slot);
      dynamic.src.push_back(identity);
      if (runtime_.retainsDiagnostics && keepInstanceMetadata_)
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
      if (runtime_.retainsDiagnostics && keepInstanceMetadata_) {
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

  void expandMembar(const CanonicalMembar &membar, DynamicInst &dynamic) {
    countInstruction();
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
                  const Expander::StaticLoopPlan &plan) {
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
        {
          auto &dynamic = runtime_.instructions.emplace_back();
          expandInstruction(*instruction, *planNode.instruction, dynamic);
        }
      } else if (const auto *membar =
                     std::get_if<CanonicalMembar>(&node.payload)) {
        {
          auto &dynamic = runtime_.instructions.emplace_back();
          expandMembar(*membar, dynamic);
        }
      } else {
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop)
          throw std::runtime_error("Canonical loop payload is null");
        expandLoop(*loop, depth, *planNode.loop);
      }
    }
  }

  // Static equivalent of the "did anything get emitted here" test that run()
  // derives from the materialised stream (emptyTopBlocks). Attribution matches
  // expandTopLevelNodes: a root-level straight-line node belongs to the top
  // block of the most recent root-level loop.
  //
  // Two rules are load-bearing and match the eager exactly:
  //   * only type=="inst" nodes make a top block non-empty (a MEMBAR-only block
  //     emits no instruction, so the eager marks it empty);
  //   * liveness propagates through each loop's own trip count (not through a
  //     nested chain), so a zero-count child does not kill an active sibling.
  void markLiveTopBlocks(const std::vector<CanonicalNode> &nodes, bool live,
                         int64_t &currentTopBlock,
                         std::unordered_set<int64_t> &out) const {
    for (const auto &node : nodes) {
      if (const auto *loopPtr =
              std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload)) {
        if (loopPtr == nullptr || !*loopPtr)
          continue;
        const CanonicalLoop &loop = **loopPtr;
        const int64_t trips =
            resolveInteger(loop.count, vfInfo_.params, "loop count");
        const bool loopLive = live && trips > 0;
        const auto block = loopTopBlockIds_.find(loop.loopId);
        if (block != loopTopBlockIds_.end()) // nested loops share the top block
          currentTopBlock = block->second;
        markLiveTopBlocks(loop.body, loopLive, currentTopBlock, out);
        continue;
      }
      if (!std::holds_alternative<CanonicalInstruction>(node.payload))
        continue; // membars are not instructions in the eager's non-empty test
      if (live)
        out.insert(currentTopBlock);
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
        {
          auto &dynamic = runtime_.instructions.emplace_back();
          expandInstruction(*instruction, *planNode.instruction, dynamic);
        }
      else {
        auto &dynamic = runtime_.instructions.emplace_back();
        expandMembar(std::get<CanonicalMembar>(node.payload), dynamic);
      }
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
    auto ownerOf = [&](const std::string &name) -> int32_t {
      auto it = runtime_.values.find(name);
      return it == runtime_.values.end() ? -1 : it->second.streamLoop;
    };
    auto recordOwner = [&](int32_t id, const std::string &name) {
      if (!idsEnabled || id < 0)
        return;
      if (runtime_.operandOwner.size() <= static_cast<size_t>(id))
        runtime_.operandOwner.resize(static_cast<size_t>(id) + 1, -1);
      runtime_.operandOwner[static_cast<size_t>(id)] = ownerOf(name);
    };
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
          if (idsEnabled) {
            runtime_.operandIds[static_cast<size_t>(srcBase) +
                                (srcIndex - 1)] = entry->id;
            recordOwner(entry->id, source);
          }
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
          if (idsEnabled) {
            runtime_.operandIds[static_cast<size_t>(dstBase) + dstIndex] =
                entry->id;
            recordOwner(entry->id, instruction.dst[dstIndex]);
          }
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

// The one interning of the static graph used by the streamed scoring path: the
// post-LICM context it owns, plus the Expander holding the loop topology, static
// pc map, lowering plan and binding slots. Metadata and cursor both read it, so
// nothing in the static graph is built twice.
struct CanonicalStaticLayout {
  // The context the plan and the cursor lower from. A LICM rewrite is owned
  // here; when LICM moved nothing the layout borrows the caller's immutable root
  // context, so the streamed preparation copies no canonical node.
  std::vector<CanonicalNode> ownedContext;
  const std::vector<CanonicalNode> *context = nullptr;
  std::unique_ptr<Expander> expander;
  // Exact dynamic event total the static metadata pass already derived from the
  // graph and its trip counts. The cursor's dynamic-instruction gate reuses it
  // instead of counting the same static graph a second time; -1 means "not
  // derived" (the standalone cursor, which counts for itself).
  int64_t dynamicEventCount = -1;
};

// Out-of-line deleter: keeps the layout's definition (and the Expander type)
// private to this translation unit while let the public header name the handle.
void CanonicalStaticLayoutDeleter::operator()(CanonicalStaticLayout *layout) const {
  delete layout;
}

class Cursor : public CanonicalCursor {
public:
  // Shares the layout the streaming preparation already built: the same
  // Expander (loops, pc map, plan, slots) serves metadata and lowering. The
  // layout is single-consumer mutable state, so it is consumed here, and it is
  // always the scoring-lowered one.
  Cursor(CanonicalLayoutHandle layout, const CanonicalVfInfo &vf,
         const ParamDB &db, const UarchConfig &cfg)
      : layout_(std::move(layout)), e_(*layout_->expander) {
    initialize(vf, db, cfg);
  }

private:
  void initialize(const CanonicalVfInfo &vf, const ParamDB &db,
                  const UarchConfig &cfg) {
    // The scored lowering is exactly the numeric-token lowering: it is the only
    // one the Engine consumes through borrowed per-event views.
    scored_ = e_.tokens_;
    const char *profileSwitch = std::getenv("VFSIM_PROFILE_LOWERING");
    profile_ = profileSwitch != nullptr && *profileSwitch != '\0' &&
               std::string(profileSwitch) != "0";
    // The plan is built from the very context this cursor expands (the same
    // post-LICM rewrite), so LICM runs once per program. buildPlan() keeps its
    // own rewrite for standalone validation; both share this builder.
    // Diagnostic ablation (env gated, never enabled by default): skip the
    // lifetime plan build so its cost can be measured in isolation.
    if (const char *skip = std::getenv("VFSIM_SKIP_LIFEPLAN");
        skip != nullptr && *skip != '\0' && std::string(skip) != "0") {
      lifeWhy_ = "ablation-skip-lifetime-plan";
      life_.reset();
    } else {
      const bool timePlan = std::getenv("VFSIM_PROFILE_LOWERING") != nullptr;
      const auto planStart = std::chrono::steady_clock::now();
      life_ = cursor_lifetime::buildPlanForContext(vf, context(), lifeWhy_);
      if (timePlan) {
        const auto planEnd = std::chrono::steady_clock::now();
        std::fprintf(stderr, "[lifeplan_ns=%lld]\n",
                     static_cast<long long>(
                         std::chrono::duration_cast<std::chrono::nanoseconds>(
                             planEnd - planStart)
                             .count()));
      }
    }
    if (life_) {
      // Scoring mode does not carry per-instruction identifier strings, so the
      // lifetime node is resolved once per static program, keyed by the static
      // pc the lowering already wrote, instead of hashing a name per event.
      // PC space is sparse: indexLoops advances nextPc_ once more per loop
      // (virtual loop end) without inserting into staticPc_, so the largest
      // valid pc is nextPc_ - 1 and sizing by staticPc_.size() silently drops
      // every instruction above it (missing lifetime bits -> tail stalls).
      lifeNodeByPc_.assign(static_cast<size_t>(std::max<int64_t>(e_.nextPc_, 0)),
                           -1);
      // Static support gate and pc map in one dense pass: the lowering's plan
      // tree enumerates exactly the instructions the stream can emit, in the
      // same preorder the lifetime plan uses, so the two are paired by position
      // and no id is hashed or rebuilt. A length mismatch means the lifetime
      // plan does not cover the emitted stream: decline before any event
      // (fail closed) instead of running to max_cycles with missing bits.
      if (declineText_.empty()) {
        size_t node = 0;
        std::string uncovered;
        const bool covered =
            fillLifeNodeByPc(e_.planRoot_, context(), node, uncovered);
        if (!covered || node != life_->instructions().size()) {
          declineCoverage_ = "lifetime-coverage-incomplete:" +
                             (uncovered.empty() ? std::string("preorder-mismatch")
                                                : uncovered);
          declineText_ = declineCoverage_;
        }
      }
    }
    if (!life_)
      declineText_ = "lifetime-plan-unavailable:" + lifeWhy_;
    else if (!validateUnrollShapes(context()))
      life_.reset();  // declineText_ already set; fail closed before any event
    else if (exceedsDynamicInstructionLimit(context(), cfg.canonicalDynamicInstructionLimit)) {
      // The eager throws inside expansion, i.e. it never yields a stream; the
      // cursor rejects the whole program before the first event instead of
      // handing out a partial stream and failing later.
      declineText_ = "dynamic-instruction-limit-exceeded";
      life_.reset();
    }
  }

public:
  bool pullRaw(DynamicInst &out) {
    if (!life_ || lifetimeFailed_)
      return false;
    while (true) {
      if (!ready_.empty()) {
        out = std::move(ready_.front());
        ready_.pop_front();
        return true;
      }
      if (stack_.empty()) {
        if (rootIndex_ >= e_.context_.size())
          return false;
        const size_t index = rootIndex_++;
        const auto &node = e_.context_[index];
        const auto &planNode = e_.planRoot_[index];
        if (const auto *inst =
                std::get_if<CanonicalInstruction>(&node.payload)) {
          lower(*inst, *planNode.instruction, out);
          return true;
        }
        if (const auto *membar = std::get_if<CanonicalMembar>(&node.payload)) {
          lowerMembar(*membar, out);
          return true;
        }
        const auto &loop =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!loop || !planNode.loop) {
          decline_ = "null-loop";
          return false;
        }
        if (!pushLoop(*loop, *planNode.loop, 0))
          return false;
        continue;
      }
      Item &item = stack_.back();
      if (item.nodeIndex < item.loop->body.size()) {
        const size_t index = item.nodeIndex++;
        const auto &node = item.loop->body[index];
        const auto &planNode = item.plan->body[index];
        if (const auto *inst =
                std::get_if<CanonicalInstruction>(&node.payload)) {
          if (item.unroll <= 1) {
            // unroll <= 1 has no lane interleave: lower straight into the Engine
            // slot so its vectors are reused instead of freed per event.
            lower(*inst, *planNode.instruction, out);
            return true;
          }
          DynamicInst lowered;
          lower(*inst, *planNode.instruction, lowered);
          if (deliver(std::move(lowered), item, out))
            return true;
          continue;
        }
        if (const auto *membar = std::get_if<CanonicalMembar>(&node.payload)) {
          if (item.unroll <= 1) {
            lowerMembar(*membar, out);
            return true;
          }
          DynamicInst lowered;
          lowerMembar(*membar, lowered);
          if (deliver(std::move(lowered), item, out))
            return true;
          continue;
        }
        const auto &nested =
            std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
        if (!nested || !planNode.loop) {
          decline_ = "null-nested-loop";
          return false;
        }
        if (!pushLoop(*nested, *planNode.loop,
                      static_cast<int>(stack_.size())))
          return false;
        continue;
      }
      // The current iteration (or unroll lane) finished its body.
      e_.frames_.pop_back();
      if (!frameCoords_.empty())
        frameCoords_.pop_back();
      for (size_t i = 0; i < item.plan->carriedBackEdge.size(); ++i)
        if (item.plan->carriedBackEdge[i] >= 0) {
          if (e_.tokens_)
            item.currentTokens[i] =
                e_.resolveSlotToken(item.plan->carriedBackEdge[i]);
          else
            item.currentValues[i] =
                e_.resolveSlot(item.plan->carriedBackEdge[i]);
        }
      const int64_t nextIteration = item.iteration + 1;
      // Node-major lane interleave: the eager expands every lane of a group and
      // then emits instructionIndex-major across the lanes, so the captured
      // lanes are flushed exactly when the group completes.
      if (item.unroll > 1 && nextIteration % item.unroll == 0)
        interleaveLanes(item.unroll);
      if (nextIteration < item.count) {
        for (size_t i = 0; i < item.plan->carriedEntry.size(); ++i)
          if (item.plan->carriedEntry[i] >= 0) {
            if (e_.tokens_)
              e_.bindSlotToken(item.plan->carriedEntry[i],
                               item.currentTokens[i]);
            else
              e_.bindSlot(item.plan->carriedEntry[i], item.currentValues[i]);
          }
        item.iteration = nextIteration;
        e_.frames_.push_back(
            Frame{item.loop->loopId, item.iteration,
                  item.iteration / item.unroll, item.topBlock,
                  item.loop->induction.variableId,
                  resolveInteger(item.loop->induction.start, e_.vfInfo_.params,
                                    "induction start"),
                  resolveInteger(item.loop->induction.step, e_.vfInfo_.params,
                                    "induction step")});
        // One coordinate update per iteration/group advance, not per event.
        pushFrameCoordinate(item, item.iteration);
        item.nodeIndex = 0;
        continue;
      }
      e_.popScope();
      for (size_t i = 0; i < item.plan->carriedExit.size(); ++i)
        if (item.plan->carriedExit[i] >= 0) {
          if (e_.tokens_)
            e_.bindSlotToken(item.plan->carriedExit[i],
                             item.count == 0 ? item.entryTokens[i]
                                             : item.currentTokens[i]);
          else
            e_.bindSlot(item.plan->carriedExit[i],
                        item.count == 0 ? item.entryValues[i]
                                        : item.currentValues[i]);
        }
      stack_.pop_back();
    }
  }
  // Block-boundary markers (blockEndLevels, isLastInTopBlock) are decided when
  // the instruction is lowered, from the live frame stack plus static suffix
  // summaries, so next() needs no lookahead and no buffered events.
  bool next(DynamicInst &out) override {
    if (!pullRaw(out))
      return false;
    // A lifetime lookup that failed while lowering this event leaves it without
    // release/keep bits, i.e. the Engine would score a different program. Fail
    // closed before the event is consumed; declineReason() carries the cause, and
    // the scoring provider turns it into an error instead of a short stream.
    if (lifetimeFailed_)
      return false;
    // Emission-order identity: the eager numbers the materialised stream once,
    // after any lane interleave. streamSeq orders the pipeline, instId is the
    // stable per-instruction handle the Engine sorts completed uops by, and a
    // membar carries -1 (it has no instruction slot).
    out.streamSeq = seq_++;
    out.instId = out.type == "membar" ? -1 : instId_++;
    return true;
  }
  bool exhausted() const override {
    return ready_.empty() && stack_.empty() &&
           rootIndex_ >= e_.context_.size();
  }
  const LoweringProfile *loweringProfile() const override {
    return profile_ ? &profileStorage_ : nullptr;
  }
  const char *declineReason() const override {
    return declineText_.empty() ? decline_ : declineText_.c_str();
  }

private:
  struct BodySummary;
  struct Item {
    const CanonicalLoop *loop = nullptr;
    const Expander::StaticLoopPlan *plan = nullptr;
    // Static companions of this frame, resolved once when the frame is pushed:
    // the lifetime plan's loop index (coordinate mapping) and the body's suffix
    // summary (block-boundary predicate). Both are consumed per event, so the
    // lookups must not run per event.
    int32_t lifeLoop = -1;
    const BodySummary *summary = nullptr;
    size_t nodeIndex = 0;
    int64_t count = 0;
    int64_t iteration = 0;
    int64_t unroll = 1;
    int32_t topBlock = 0;
    std::vector<std::string> currentValues;
    std::vector<std::string> entryValues;
    // Scoring lowering carries numeric tokens instead of identity strings.
    std::vector<uint64_t> currentTokens;
    std::vector<uint64_t> entryTokens;
  };

  // Operand counts are taken from whichever representation this lowering
  // produced: the lifetime plan's per-operand bits are index-aligned with the
  // operand order, not with the identity spelling.
  size_t sourceOperands(const DynamicInst &d) const {
    return e_.tokens_ ? d.srcTokens.size() : d.src.size();
  }
  size_t destOperands(const DynamicInst &d) const {
    return e_.tokens_ ? d.dstTokens.size() : d.dst.size();
  }
  // Lifetime fill for the non-streamed consumer (standalone/diagnostic lowering,
  // which owns its per-operand bit vectors). The streamed scoring path uses
  // fillLifetimeScored, which keeps the same exact query but borrows the flags.
  //
  // Both paths query by the cached per-frame representative coordinate. There is
  // no one-entry cache: the key is (static instruction, coordinates), and within
  // one iteration a static instruction is emitted exactly once, so consecutive
  // events never repeat a key. Caching it only bought a per-event key copy and
  // comparison, which is what this removes.
  void fillLifetime(DynamicInst &d) {
    if (!life_)
      return;
    const size_t srcCount = sourceOperands(d);
    const size_t dstCount = destOperands(d);
    const int32_t node =
        (d.pc >= 0 && static_cast<size_t>(d.pc) < lifeNodeByPc_.size())
            ? lifeNodeByPc_[static_cast<size_t>(d.pc)]
            : -1;
    if (node < 0) {
      // No lifetime coverage for this static instruction: emitting empty bit
      // vectors would silently change producer release/keep semantics, so the
      // stream declines and the caller runs the eager preparation instead.
      if (srcCount != 0 || dstCount != 0) {
        lifetimeFailed_ = true;
        declineText_ = "lifetime-coverage-missing:" +
                       std::to_string(static_cast<long long>(d.pc));
      }
      return;
    }
    const uint8_t *release = nullptr;
    const uint8_t *keep = nullptr;
    size_t releaseCount = 0, keepCount = 0;
    if (!life_->eventFlagsByCoord(node, frameCoords_.data(),
                                  static_cast<uint8_t>(frameCoords_.size()),
                                  &release, &releaseCount, &keep,
                                  &keepCount)) {
      // Same rule as above: no coverage means decline, never "no release".
      if (srcCount != 0 || dstCount != 0) {
        lifetimeFailed_ = true;
        declineText_ = "lifetime-event-missing:" + std::to_string(node);
      }
      return;
    }
    d.srcValueRelease.assign(srcCount, false);
    const size_t srcBits = releaseCount < srcCount ? releaseCount : srcCount;
    for (size_t i = 0; i < srcBits; ++i)
      d.srcValueRelease[i] = release[i] != 0;
    d.dstValueKeep.assign(dstCount, false);
    const size_t dstBits = keepCount < dstCount ? keepCount : dstCount;
    for (size_t i = 0; i < dstBits; ++i)
      d.dstValueKeep[i] = keep[i] != 0;
  }
  // ---- static block-boundary analysis -------------------------------------
  // A scheduling block at level L is keyed by (topBlockId, L, iter[0..L-1]): only
  // the first L iteration counters participate, and MEMBAR nodes carry no key at
  // all. The frozen eager closes such a block on the last instruction holding
  // the key, so for every lowered instruction the cursor only has to ask whether
  // the live frame stack can still lower a member of the block before iteration
  // [L-1] advances. Only static per-body suffix summaries are consulted, hence
  // O(depth) work per event and memory proportional to the static program, never
  // to the dynamic iteration count (no pre-scan of the dynamic stream, no table
  // that grows with trip counts, no lookahead over buffered events).
  struct BodySummary {
    std::vector<uint8_t> instFrom;      // a = nodes[a..] lower an instruction
    std::vector<uint8_t> loopInstFrom;  // a = nodes[a..] lower one inside a loop
    bool anyInst = false;
  };
  const BodySummary &bodySummary(const CanonicalLoop &loop) const {
    const auto found = summaries_.find(&loop.body);
    if (found != summaries_.end())
      return found->second;
    const size_t size = loop.body.size();
    BodySummary summary;
    summary.instFrom.assign(size + 1, 0);
    summary.loopInstFrom.assign(size + 1, 0);
    for (size_t i = size; i-- > 0;) {
      const bool inst =
          std::holds_alternative<CanonicalInstruction>(loop.body[i].payload);
      bool inLoop = false;
      if (const auto *nested = std::get_if<std::shared_ptr<const CanonicalLoop>>(
              &loop.body[i].payload))
        if (*nested)
          inLoop = loopEmitsInstructions(**nested);
      summary.instFrom[i] = static_cast<uint8_t>(
          (inst || inLoop) || summary.instFrom[i + 1] != 0);
      summary.loopInstFrom[i] =
          static_cast<uint8_t>(inLoop || summary.loopInstFrom[i + 1] != 0);
    }
    summary.anyInst = summary.instFrom[0] != 0;
    return summaries_.emplace(&loop.body, std::move(summary)).first->second;
  }
  bool loopEmitsInstructions(const CanonicalLoop &loop) const {
    if (resolveInteger(loop.count, e_.vfInfo_.params, "loop count") <= 0)
      return false;
    return bodySummary(loop).anyInst;
  }
  // True when no later instruction can still carry the level-L key.
  bool blockClosedAt(const DynamicInst &d, size_t level) const {
    // For a lowered instruction the iteration stack mirrors the live frames.
    const size_t depth = std::min(d.iterStack.size(), stack_.size());
    if (level >= depth)
      return true;
    for (size_t a = depth; a-- > level;) {
      const Item &item = stack_[a];
      const BodySummary &summary =
          item.summary != nullptr ? *item.summary : bodySummary(*item.loop);
      // Instances left in this frame are at depth a+1 > level: they are members.
      if (summary.instFrom[item.nodeIndex] != 0)
        return false;
      if (item.iteration + 1 < item.count && summary.anyInst)
        return false;
      // An unrolled group re-emits this same static node in every later lane with
      // the same scheduling key (lanes share one group index and the innermost
      // iteration is not part of this level's key), so a later lane is a member.
      if (item.unroll > 1 && item.iteration % item.unroll + 1 < item.unroll)
        return false;
    }
    // The key only fixes frames 0..level-1, so the block stays open only while
    // frame level-1 still lowers an instruction nested deeper than that level.
    if (level > 0) {
      const Item &item = stack_[level - 1];
      if (bodySummary(*item.loop).loopInstFrom[item.nodeIndex] != 0)
        return false;
    }
    return true;
  }
  // Straight-line root nodes after a loop stay in that loop's top block until
  // the next top-level loop starts (Expander::expandTopLevelNodes).
  bool rootHasLaterInstruction(int64_t topBlockId) const {
    for (size_t i = rootIndex_; i < e_.context_.size(); ++i) {
      if (std::holds_alternative<CanonicalInstruction>(e_.context_[i].payload))
        return true;
      const auto *loop = std::get_if<std::shared_ptr<const CanonicalLoop>>(
          &e_.context_[i].payload);
      if (loop == nullptr || !*loop)
        continue;
      const auto found = e_.loopTopBlockIds_.find((*loop)->loopId);
      const int64_t loopBlock =
          found == e_.loopTopBlockIds_.end() ? -1 : found->second;
      // Every later top block owns a different id: nothing after it can match.
      if (loopBlock != topBlockId)
        return false;
      if (loopEmitsInstructions(**loop))
        return true;
    }
    return false;
  }
  void fillBlockBoundaries(DynamicInst &d) {
    d.blockEndLevels.clear();
    d.isLastInTopBlock = false;
    if (d.type != "inst")
      return;
    for (size_t level = d.schedulingKeys.size(); level-- > 0;)
      if (blockClosedAt(d, level))
        d.blockEndLevels.push_back(static_cast<int64_t>(level));
    bool later = d.iterStack.empty() ? false : !blockClosedAt(d, 0);
    if (!later)
      later = rootHasLaterInstruction(d.topBlockId);
    d.isLastInTopBlock = !later;
  }
  void fillScheduling(DynamicInst &d) {
    d.schedulingKeys.clear();
    d.normalizedBlockKeys.clear();
    d.blockKeyByLevel.clear();
    if (d.type != "inst")
      return;
    const size_t depth = d.iterStack.size();
    d.schedulingKeys.reserve(depth);
    for (size_t level = 0; level < depth; ++level) {
      SchedulingKey typed;
      typed.topBlockId = d.topBlockId;
      typed.depth = static_cast<int32_t>(level);
      if (level <= 2) {
        typed.c0 = level > 0 ? d.iterStack[0] : -1;
        typed.c1 = level > 1 ? d.iterStack[1] : -1;
        // Scoring mode consumes the typed key only; the block-key spelling is
        // the diagnostic oracle's, so it is not built here.
        d.schedulingKeys.push_back(std::move(typed));
        continue;
      }
      std::string text = std::to_string(d.topBlockId) + "|loop" +
                         std::to_string(level) + "|";
      for (size_t i = 0; i < level; ++i) {
        if (i)
          text += ",";
        text += std::to_string(d.iterStack[i]);
      }
      typed.slow = text;
      d.schedulingKeys.push_back(std::move(typed));
    }
  }
  void lower(const CanonicalInstruction &inst,
             const Expander::StaticInstructionPlan &plan, DynamicInst &out) {
    if (scored_) {
      lowerScored(inst, plan, out);
      return;
    }
    // Recycle the object instead of replacing it: the provider rotates the
    // arena slot's buffers back here, so the lowering allocates nothing after
    // the first few events.
    out.clearForReuse();
    if (!profile_)
      e_.expandInstruction(inst, plan, out);
    else {
      const auto t0 = std::chrono::steady_clock::now();
      e_.expandInstruction(inst, plan, out);
      profileStorage_.expandNs += std::chrono::duration_cast<std::chrono::nanoseconds>(
                                std::chrono::steady_clock::now() - t0)
                                .count();
    }
    if (!profile_) {
      fillScheduling(out);
      fillBlockBoundaries(out);
      fillLifetime(out);
      return;
    }
    const auto t1 = std::chrono::steady_clock::now();
    fillScheduling(out);
    const auto t2 = std::chrono::steady_clock::now();
    fillBlockBoundaries(out);
    const auto t3 = std::chrono::steady_clock::now();
    fillLifetime(out);
    const auto t4 = std::chrono::steady_clock::now();
    ++profileStorage_.events;
    profileStorage_.schedulingNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t2 - t1).count();
    profileStorage_.blockBoundaryNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t3 - t2).count();
    profileStorage_.lifetimeNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t4 - t3).count();
  }
  // Borrows the per-frame representative coordinate for a frame the cursor just
  // entered (or advanced to). The mapping runs once per iteration/group, so an
  // event only reads the cached (rep, lane) pair.
  void pushFrameCoordinate(const Item &item, int64_t iteration) {
    cursor_lifetime::IterationCoordinate coordinate;
    if (life_ && item.lifeLoop >= 0) {
      const cursor_lifetime::IterationCoordinate mapped =
          life_->coordinateFor(item.lifeLoop, iteration);
      coordinate.rep = mapped.rep;
      coordinate.lane = mapped.lane;
    }
    if (frameCoords_.size() == frameCoords_.capacity())
      frameCoords_.reserve(frameCoords_.size() + cursor_lifetime::Plan::kMaxEventDepth);
    frameCoords_.push_back(coordinate);
  }

  // Final-state lowering of one streamed scoring event. Every static half of the
  // event is borrowed from the plan (opcode, form, class, catalog mode, align
  // attributes, per-operand storage classes, memory accesses, destination
  // counts); only the genuinely dynamic fields are assembled: value tokens, the
  // scheduling keys of the live frame stack, the block-boundary bits and the
  // lifetime release/keep views.
  void lowerScored(const CanonicalInstruction &inst,
                   const Expander::StaticInstructionPlan &plan,
                   DynamicInst &out) {
    out.clearForScoredEvent();
    e_.countInstruction();
    const auto t0 = profile_ ? std::chrono::steady_clock::now()
                             : std::chrono::steady_clock::time_point{};
#ifdef VFSIM_ABLATE_INVARIANT
    // Same diagnostic ablation as expandInstruction: the invariant halves are
    // replaced (or dropped) so the ablation measures the non-invariant cost.
    out.op = "VADD";
    out.opClass = "COMPUTE";
#else
    out.opView = &inst.opcode;
    out.formView = &inst.form;
    out.opClassView = plan.opClassRef;
    if (plan.catalogMode != nullptr)
      out.catalogModeView = &std::get<std::string>(*plan.catalogMode);
#endif
    const auto *alignOperation = plan.alignStateOperation;
    if (alignOperation != nullptr) {
      const auto *value = std::get_if<std::string>(alignOperation);
      if (value == nullptr)
        throw std::runtime_error("align_state_operation must be a string: " +
                                 inst.instructionId);
      out.alignStateOperationView = value;
    }
    const auto *alignState = plan.alignStateId;
    if (alignState != nullptr) {
      const auto *value = std::get_if<std::string>(alignState);
      if (value == nullptr)
        throw std::runtime_error("align_state_id must be a string: " +
                                 inst.instructionId);
      out.alignStateIdView = value;
    }
    out.pc = plan.pc;
#ifndef VFSIM_ABLATE_INVARIANT
    out.memoryAccessView =
        plan.memoryAccessCopies.empty() ? nullptr : plan.memoryAccessCopies.data();
    out.memoryAccessViewCount =
        static_cast<uint32_t>(plan.memoryAccessCopies.size());
#endif
    out.topBlockId = e_.frames_.empty() ? e_.activeTopBlockId_
                                        : e_.frames_.front().topBlockId;
    for (const auto &frame : e_.frames_)
      out.iterStack.push_back(frame.scheduledIteration);
    out.srcStorageView =
        plan.inputStorage.empty() ? nullptr : plan.inputStorage.data();
    out.srcStorageViewCount = static_cast<uint32_t>(plan.inputStorage.size());
    out.dstStorageView =
        plan.outputStorage.empty() ? nullptr : plan.outputStorage.data();
    out.dstStorageViewCount = static_cast<uint32_t>(plan.outputStorage.size());
    out.vectorDestCount = plan.vectorDstCount;
    out.predicateDestCount = plan.predicateDstCount;
    for (const auto &operandPlan : plan.inputs)
      out.srcTokens.push_back(e_.resolveSlotToken(operandPlan.slot));
    for (const auto &operandPlan : plan.outputs) {
      const uint64_t token = e_.outputToken(staticValueToken(operandPlan.slot));
      out.dstTokens.push_back(token);
      e_.bindSlotToken(operandPlan.slot, token);
    }
    const auto t1 = profile_ ? std::chrono::steady_clock::now()
                             : std::chrono::steady_clock::time_point{};
    fillScheduling(out);
    if (!profile_) {
      fillBlockBoundaries(out);
      fillLifetimeScored(out);
      return;
    }
    const auto t2 = std::chrono::steady_clock::now();
    fillBlockBoundaries(out);
    const auto t3 = std::chrono::steady_clock::now();
    fillLifetimeScored(out);
    const auto t4 = std::chrono::steady_clock::now();
    ++profileStorage_.events;
    profileStorage_.expandNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t1 - t0).count();
    profileStorage_.schedulingNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t2 - t1).count();
    profileStorage_.blockBoundaryNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t3 - t2).count();
    profileStorage_.lifetimeNs +=
        std::chrono::duration_cast<std::chrono::nanoseconds>(t4 - t3).count();
  }

  // Scored lifetime fill: cached per-frame representative coordinates, one exact
  // keyed lookup in the plan's contiguous flag store, and the bits stay borrowed
  // (no per-event vector<bool> copy, no one-entry cache and no temporary
  // iteration-number vector).
  void fillLifetimeScored(DynamicInst &d) {
    if (!life_)
      return;
    const size_t srcCount = d.srcTokens.size();
    const size_t dstCount = d.dstTokens.size();
    const int32_t node =
        (d.pc >= 0 && static_cast<size_t>(d.pc) < lifeNodeByPc_.size())
            ? lifeNodeByPc_[static_cast<size_t>(d.pc)]
            : -1;
    // Same fail-closed rules as the eager-serving path: a missing coordinate
    // never silently means "no release".
    if (node < 0) {
      if (srcCount != 0 || dstCount != 0) {
        lifetimeFailed_ = true;
        declineText_ = "lifetime-coverage-missing:" +
                       std::to_string(static_cast<long long>(d.pc));
      }
      return;
    }
    const uint8_t *release = nullptr;
    const uint8_t *keep = nullptr;
    size_t releaseCount = 0, keepCount = 0;
    if (!life_->eventFlagsByCoord(node, frameCoords_.data(),
                                  static_cast<uint8_t>(frameCoords_.size()),
                                  &release, &releaseCount, &keep,
                                  &keepCount)) {
      if (srcCount != 0 || dstCount != 0) {
        lifetimeFailed_ = true;
        declineText_ =
            "lifetime-event-missing:" + std::to_string(static_cast<long long>(node));
      }
      return;
    }
    d.srcReleaseView = release;
    d.srcReleaseViewCount = static_cast<uint32_t>(releaseCount);
    d.dstKeepView = keep;
    d.dstKeepViewCount = static_cast<uint32_t>(keepCount);
  }

  void lowerMembar(const CanonicalMembar &membar, DynamicInst &out) {
    out.clearForReuse();
    e_.expandMembar(membar, out);
  }
  bool pushLoop(const CanonicalLoop &loop, const Expander::StaticLoopPlan &plan,
                int depth) {
    const int64_t count =
        resolveInteger(loop.count, e_.vfInfo_.params, "loop count");
    int64_t unroll =
        resolveInteger(loop.unroll, e_.vfInfo_.params, "loop unroll");
    // Same acceptance rules, in the same order, as Expander::expandLoop: a loop
    // shape the eager rejects can never yield a cursor stream.
    if (count < 0 || unroll <= 0) {
      declineText_ = "canonical-loop-invalid:" + loop.loopId;
      return false;
    }
    if (unroll > 1 && e_.containsLoop(loop)) {
      declineText_ = "unroll-with-nested-loop:" + loop.loopId;
      return false;
    }
    if (count % unroll != 0) {
      declineText_ = "count-not-divisible-by-unroll:" + loop.loopId;
      return false;
    }
    if (unroll > 1 && e_.containsMembar(loop)) {
      // The eager disables unrolling for a body with a direct MEMBAR (and records
      // the same warning); the cursor then emits the unit-unrolled stream.
      recordMembarUnrollDisabled(loop, unroll);
      unroll = 1;
    }
    const int32_t topBlock =
        depth == 0
            ? static_cast<int32_t>(e_.loopTopBlockIds_.at(loop.loopId))
            : static_cast<int32_t>(e_.frames_.front().topBlockId);
    if (depth == 0)
      e_.activeTopBlockId_ = topBlock;
    Item item;
    item.loop = &loop;
    item.plan = &plan;
    item.count = count;
    item.unroll = unroll;
    item.topBlock = topBlock;
    // Static companions resolved once per frame push, never per event.
    item.lifeLoop = life_ ? life_->loopIndex(loop.loopId) : -1;
    item.summary = &bodySummary(loop);
    item.currentValues.reserve(plan.carriedEntry.size());
    item.entryValues.reserve(plan.carriedEntry.size());
    item.currentTokens.reserve(plan.carriedEntry.size());
    item.entryTokens.reserve(plan.carriedEntry.size());
    for (int32_t slot : plan.carriedEntry) {
      if (e_.tokens_) {
        const uint64_t token = slot >= 0 ? e_.resolveSlotToken(slot) : 0;
        item.currentTokens.push_back(token);
        item.entryTokens.push_back(token);
        item.currentValues.emplace_back();
        item.entryValues.emplace_back();
      } else {
        const std::string value =
            slot >= 0 ? e_.resolveSlot(slot) : std::string{};
        item.currentValues.push_back(value);
        item.entryValues.push_back(value);
      }
    }
    e_.pushScope();
    if (count == 0) {
      e_.popScope();
      for (size_t i = 0; i < plan.carriedExit.size(); ++i)
        if (plan.carriedExit[i] >= 0) {
          if (e_.tokens_)
            e_.bindSlotToken(plan.carriedExit[i], item.entryTokens[i]);
          else
            e_.bindSlot(plan.carriedExit[i], item.entryValues[i]);
        }
      return true;
    }
    for (size_t i = 0; i < plan.carriedEntry.size(); ++i)
      if (plan.carriedEntry[i] >= 0) {
        if (e_.tokens_)
          e_.bindSlotToken(plan.carriedEntry[i], item.currentTokens[i]);
        else
          e_.bindSlot(plan.carriedEntry[i], item.currentValues[i]);
      }
    e_.frames_.push_back(
        Frame{loop.loopId, 0, 0, topBlock, loop.induction.variableId,
              resolveInteger(loop.induction.start, e_.vfInfo_.params,
                                "induction start"),
              resolveInteger(loop.induction.step, e_.vfInfo_.params,
                                "induction step")});
    pushFrameCoordinate(item, 0);
    stack_.push_back(std::move(item));
    return true;
  }

  // Hands a lowered event out, or captures it while an unrolled group is being
  // assembled. Memory is bounded by unroll x body size (static), never by the
  // trip count; nothing is buffered across groups.
  bool deliver(DynamicInst &&event, const Item &item, DynamicInst &out) {
    if (item.unroll > 1) {
      lanes_.resize(static_cast<size_t>(item.unroll));
      lanes_[static_cast<size_t>(item.iteration % item.unroll)].push_back(
          std::move(event));
      return false;
    }
    out = std::move(event);
    return true;
  }
  // Eager order: for every static instruction index, lane 0 .. unroll-1.
  void interleaveLanes(int64_t unroll) {
    if (lanes_.empty())
      return;
    const size_t width = lanes_.front().size();
    for (size_t index = 0; index < width; ++index)
      for (int64_t lane = 0; lane < unroll; ++lane) {
        auto &events = lanes_[static_cast<size_t>(lane)];
        if (index < events.size())
          ready_.push_back(std::move(events[index]));
      }
    lanes_.clear();
  }
  // Mirrors the eager's warning payload for the MEMBAR-suppressed unroll case.
  void recordMembarUnrollDisabled(const CanonicalLoop &loop, int64_t requested) {
    const auto membar = std::find_if(
        loop.body.begin(), loop.body.end(), [](const CanonicalNode &node) {
          return std::holds_alternative<CanonicalMembar>(node.payload);
        });
    if (membar == loop.body.end())
      return;
    const auto &barrier = std::get<CanonicalMembar>(membar->payload);
    e_.db_.recordWarning(
        "membar_unroll_disabled",
        {{"pc", e_.staticPc_.at(barrier.instructionId)},
         {"barrier", barrier.barrier},
         {"loop_id", e_.staticLoopIds_.at(loop.loopId)},
         {"requested_unroll", requested},
         {"used_unroll", int64_t{1}},
         {"reason", "membar_in_unrolled_innermost_loop"}});
  }
  // Exact number of dynamic events the static program produces (instructions and
  // membars), saturating so nesting cannot overflow. Mirrors the eager's
  // countInstruction() acceptance test; unrolling does not change the total.
  int64_t countDynamicEvents(const std::vector<CanonicalNode> &nodes) const {
    constexpr int64_t kSaturate = int64_t{1} << 60;
    int64_t total = 0;
    for (const auto &node : nodes) {
      if (std::holds_alternative<CanonicalInstruction>(node.payload) ||
          std::holds_alternative<CanonicalMembar>(node.payload)) {
        ++total;
        continue;
      }
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr)
        continue;
      const int64_t count =
          resolveInteger((*loopPtr)->count, e_.vfInfo_.params, "loop count");
      if (count <= 0)
        continue;
      const int64_t body = countDynamicEvents((*loopPtr)->body);
      if (body > 0 && count > kSaturate / body)
        return kSaturate;
      total += count * body;
      if (total > kSaturate)
        return kSaturate;
    }
    return total;
  }
  bool exceedsDynamicInstructionLimit(const std::vector<CanonicalNode> &nodes,
                                      int64_t limit) const {
    if (limit <= 0)
      return false;
    // Reuse the total the static metadata pass already derived from the graph
    // and its trip counts; only the standalone cursor (no shared layout) counts
    // for itself. A negative total means the count overflowed, which the eager
    // rejects inside expansion, so the streamed path declines here too.
    const int64_t events =
        layout_ ? layout_->dynamicEventCount : countDynamicEvents(nodes);
    return events < 0 || events > limit;
  }
  // Validates the loop shapes the eager would actually expand (a loop inside a
  // zero-trip loop is never reached), so an eager-rejected program declines here
  // before any event is produced.
  bool validateUnrollShapes(const std::vector<CanonicalNode> &nodes) {
    for (const auto &node : nodes) {
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr)
        continue;
      const CanonicalLoop &loop = **loopPtr;
      const int64_t count =
          resolveInteger(loop.count, e_.vfInfo_.params, "loop count");
      const int64_t unroll =
          resolveInteger(loop.unroll, e_.vfInfo_.params, "loop unroll");
      if (count < 0 || unroll <= 0) {
        declineText_ = "canonical-loop-invalid:" + loop.loopId;
        return false;
      }
      if (unroll > 1 && e_.containsLoop(loop)) {
        declineText_ = "unroll-with-nested-loop:" + loop.loopId;
        return false;
      }
      if (count % unroll != 0) {
        declineText_ = "count-not-divisible-by-unroll:" + loop.loopId;
        return false;
      }
      if (count == 0)
        continue;  // the eager never expands an empty body
      if (!validateUnrollShapes(loop.body))
        return false;
    }
    return true;
  }

  // Shared preparation: layout_ owns the post-LICM context and the one
  // Expander.
  CanonicalLayoutHandle layout_;
  Expander &e_;
  const std::vector<CanonicalNode> &context() const { return *layout_->context; }
  // Diagnostic lowering split (env VFSIM_PROFILE_LOWERING=1); off in timed runs.
  bool profile_ = false;
  LoweringProfile profileStorage_;
  std::vector<Item> stack_;
  std::deque<DynamicInst> ready_;
  std::vector<std::vector<DynamicInst>> lanes_;
  size_t rootIndex_ = 0;
  int64_t seq_ = 0;
  int64_t instId_ = 0;
  mutable std::unordered_map<const std::vector<CanonicalNode> *, BodySummary>
      summaries_;
  std::optional<cursor_lifetime::Plan> life_;
  std::vector<int32_t> lifeNodeByPc_;
  // Pairs the lowering's plan nodes with the lifetime plan's instruction nodes
  // in preorder, filling the pc -> lifetime node map. Both enumerations cover the
  // same post-LICM context in the same order, so this is a linear pairing with
  // no hash lookup and no second scan.
  //
  // The pairing is verified per node, not by length: every position must be the
  // same canonical node kind and the same static instruction id, and every pc
  // must land inside the map. Anything else means the lifetime plan does not
  // describe the stream the lowering will emit (the two enumerations diverged),
  // and a positional guess would silently pair events with another
  // instruction's release/keep bits. Such a program declines (fail closed)
  // exactly like a missing instruction node.
  bool fillLifeNodeByPc(const std::vector<Expander::StaticNodePlan> &planNodes,
                        const std::vector<CanonicalNode> &nodes, size_t &node,
                        std::string &uncovered) {
    if (planNodes.size() != nodes.size()) {
      uncovered = "plan-node-count-mismatch";
      return false;
    }
    for (size_t index = 0; index < planNodes.size(); ++index) {
      const Expander::StaticNodePlan &entry = planNodes[index];
      const CanonicalNode &canonical = nodes[index];
      if (entry.instruction != nullptr) {
        const auto *instruction =
            std::get_if<CanonicalInstruction>(&canonical.payload);
        if (instruction == nullptr) {
          uncovered = "plan-node-kind-mismatch";
          return false;
        }
        if (node >= life_->instructions().size()) {
          uncovered = instruction->instructionId;
          return false;
        }
        if (life_->instructions()[node].instructionId !=
            instruction->instructionId) {
          // The two enumerations disagree on the static order.
          uncovered = instruction->instructionId;
          return false;
        }
        const int64_t pc = entry.instruction->pc;
        if (pc < 0 || static_cast<size_t>(pc) >= lifeNodeByPc_.size()) {
          uncovered = instruction->instructionId;
          return false;
        }
        lifeNodeByPc_[static_cast<size_t>(pc)] = static_cast<int32_t>(node);
        ++node;
        continue;
      }
      if (entry.loop == nullptr) {
        if (std::get_if<CanonicalMembar>(&canonical.payload) == nullptr) {
          uncovered = "plan-node-kind-mismatch";
          return false;
        }
        continue;  // membar: no lifetime node and no pc entry
      }
      const auto *loopHolder =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&canonical.payload);
      if (loopHolder == nullptr || !*loopHolder) {
        uncovered = "plan-node-kind-mismatch";
        return false;
      }
      if (!fillLifeNodeByPc(entry.loop->body, (*loopHolder)->body, node,
                            uncovered))
        return false;
    }
    return true;
  }
  // Coverage decline text (owned storage: declineText_ points at it).
  std::string declineCoverage_;
  // Cursor-side mirror of the live frame stack: the representative lifetime
  // coordinate of each enclosing loop, updated once per iteration/group.
  std::vector<cursor_lifetime::IterationCoordinate> frameCoords_;
  // True for the streamed scoring lowering (numeric tokens): the only lowering
  // whose events borrow their static halves.
  bool scored_ = false;
  std::string lifeWhy_;
  std::string declineText_;
  bool lifetimeFailed_ = false;
  const char *decline_ = "";
};

PreparedStreamingLayout expandCanonicalStreamingLayout(
    const CanonicalVfInfo &vf,
    std::optional<std::vector<CanonicalNode>> postLicmContext, const ParamDB &db,
    const UarchConfig &cfg) {
  // One Expander for the whole scope: the same layout the cursor lowers from.
  CanonicalLayoutHandle layout(new CanonicalStaticLayout());
  if (postLicmContext)
    layout->ownedContext = std::move(*postLicmContext);
  layout->context = postLicmContext ? &layout->ownedContext : &vf.context;
  // No value identity table: this is the no-trace scoring layout, and the
  // eager no-trace preparation keeps an empty value map for the same reason.
  layout->expander = std::make_unique<Expander>(
      vf, *layout->context, db, cfg,
      std::unordered_map<std::string, ValueInfo>{},
      /*retainDiagnostics=*/false, /*keepInstanceMetadata=*/false,
      /*numericTokens=*/true);
  PreparedStreamingLayout prepared;
  prepared.program = layout->expander->runStaticMetadata(0);
  layout->dynamicEventCount = prepared.program.dynamicEventCount;
  prepared.layout = std::move(layout);
  return prepared;
}

std::unique_ptr<CanonicalCursor> makeCanonicalCursorForLayout(
    const CanonicalVfInfo &input, CanonicalLayoutHandle layout,
    const ParamDB &db, const UarchConfig &cfg) {
  return std::unique_ptr<CanonicalCursor>(
      new Cursor(std::move(layout), input, db, cfg));
}

PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &vf, const ParamDB &db, const UarchConfig &cfg,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics,
    bool keepInstanceMetadata) {
  return expandCanonicalProgram(vf, vf.context, db, cfg, std::move(values),
                                retainDiagnostics, keepInstanceMetadata);
}

PreparedProgram expandCanonicalProgram(
    const CanonicalVfInfo &vf, const std::vector<CanonicalNode> &context,
    const ParamDB &db, const UarchConfig &cfg,
    std::unordered_map<std::string, ValueInfo> values, bool retainDiagnostics,
    bool keepInstanceMetadata) {
  return Expander(vf, context, db, cfg, std::move(values), retainDiagnostics,
                  keepInstanceMetadata)
      .run();
}
} // namespace vfsim
