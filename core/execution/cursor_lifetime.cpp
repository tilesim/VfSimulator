// Static cursor-lifetime analysis (task-167 prototype, task-2 generalisation).
//
// The eager Expander materialises every dynamic instruction and then reverse
// scans the whole stream to set srcValueRelease (last source use) and
// dstValueKeep (identity already used as a source later in the stream). This
// analysis computes the same flags from the post-LICM canonical structure.
//
// Iteration classes are derived from the structure, not from a trip-count
// threshold. For every loop the analysis computes the maximum static binding
// distance (hop) between a use site and the definition that serves it:
//
//   * hop = 0 - every read binds inside its own iteration (no carried state,
//     no not-yet-defined body-local operand): iterations are independent, so
//     first / interior / last (3 classes) are sufficient;
//   * hop = 1 - a read is served by the previous iteration (carried entry or a
//     body-local definition that appears later in the body): each class needs
//     its own successor, so first, second, two interior copies, penultimate
//     and last (6 classes) are sufficient.
//
// The class count is 3 * (hop + 1) and is independent of the trip count, so a
// 9000-trip carried loop costs the same as a 7-trip one. A loop is represented
// exactly (all groups enumerated) only when its group count fits inside that
// class set; otherwise the interior groups share the last interior class, which
// is exact because the binding chain of an interior iteration is identical to
// that of its successor (see cursor_lifetime.h). A binding distance above 1
// cannot arise from a straight-line canonical body and is rejected fail-closed.
//
// Cost model (why the shape walk is allocation free):
//
//   * every static instruction publishes its operands as dense value slots into
//     the plan's two contiguous stores and its loop path inline, so building the
//     shape performs no per-instruction heap allocation and publishing the plan
//     copies nothing;
//   * the binding distance is derived with generation-stamped dense arrays over
//     the same slots instead of one hash set per loop;
//   * identity equality is integer equality over interned ids, and a program
//     with no loops at all (the straight-line family) needs no interning and no
//     event index: the abstract stream is one depth-0 event per instruction, so
//     the reverse scan runs directly over the dense slots.
#include "cursor_lifetime.h"

#include "core/execution/optimization.h"

#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <deque>
#include <limits>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

namespace vfsim {
namespace cursor_lifetime {
namespace {

constexpr int kMaxLoopDepth =
    static_cast<int>(StaticInstructionInfo::kMaxLoopDepth);
constexpr size_t kMaxAbstractEvents = 200000;
// Largest binding distance a straight-line canonical body can produce; a larger
// value means the analysis cannot derive the serving definition and must
// decline instead of guessing.
constexpr int32_t kMaxBindingHop = 1;

bool parseInteger(const CanonicalIntegerExpression &expression,
                  const std::unordered_map<std::string, int64_t> &params,
                  const std::string &field, int64_t &out,
                  std::string &why) {
  if (const auto *value = std::get_if<int64_t>(&expression)) {
    out = *value;
    return true;
  }
  const std::string &text = std::get<std::string>(expression);
  const auto parameter = params.find(text);
  if (parameter != params.end()) {
    out = parameter->second;
    return true;
  }
  try {
    size_t consumed = 0;
    const int64_t value = std::stoll(text, &consumed, 10);
    if (consumed == text.size()) {
      out = value;
      return true;
    }
  } catch (...) {
  }
  why = "cannot-resolve-" + field;
  return false;
}

struct NodeShape {
  enum Kind { Instruction, Loop } kind = Instruction;
  int32_t index = -1;
};

struct LoopShape {
  std::string loopId;
  int32_t index = 0;
  int32_t depth = 0;
  int64_t count = 0;
  int64_t unroll = 1;
  int64_t groups = 0;
  // Maximum static binding distance of a use site to the definition serving it,
  // measured in loop iterations. Derived from the body's use sites.
  int32_t hop = 0;
  bool exact = false;
  std::vector<int64_t> repGroups;
  std::vector<NodeShape> body;
  struct Carried {
    bool hasEntry = false;
    // Static value slots resolved once while building the shape.
    int32_t entrySlot = -1, backSlot = -1, exitSlot = -1;
    // Exact exit value name, materialised only when the name-injectivity guard
    // fails and the exact string interner is in use.
    std::string exitName;
  };
  std::vector<Carried> carried;
  bool hasMembar = false;
  bool hasNestedLoop = false;
};

struct FrameKey {
  int32_t loop = -1;
  int32_t rep = 0;
  int32_t lane = 0;
};

// Dense identity id assigned by the exact string interner (same name -> same id).
using IdentityId = uint32_t;
// "No carried identity yet" marker for the loop-carried walk state only; every
// value slot itself always holds a real root identity id.
constexpr IdentityId kNoIdentity = std::numeric_limits<IdentityId>::max();

// Exact identity under the name-injectivity guard: two identities are equal iff
// their names would be equal. A name is a value id followed, per enclosing loop
// frame, by "@<loopId>=<tag>". No value id or loop id contains '@' and loop ids
// are unique (checked once per plan), so (value slot, frame stack) is a faithful
// stand-in for the exact name string: the string is never built.
struct IdentityKey {
  int32_t slot = -1;
  uint8_t depth = 0;
  int32_t loop[kMaxLoopDepth];
  int32_t rep[kMaxLoopDepth];
  int32_t lane[kMaxLoopDepth];
};

struct IdentityKeyHash {
  size_t operator()(const IdentityKey &key) const {
    size_t hash = 1469598103934665603ull;
    auto mix = [&hash](int64_t value) {
      hash ^= static_cast<uint64_t>(value);
      hash *= 1099511628211ull;
    };
    mix(key.slot);
    mix(key.depth);
    for (uint8_t index = 0; index < key.depth; ++index) {
      mix(key.loop[index]);
      mix(key.rep[index]);
      mix(key.lane[index]);
    }
    return hash;
  }
};

struct IdentityKeyEq {
  bool operator()(const IdentityKey &a, const IdentityKey &b) const {
    if (a.slot != b.slot || a.depth != b.depth)
      return false;
    for (uint8_t index = 0; index < a.depth; ++index)
      if (a.loop[index] != b.loop[index] || a.rep[index] != b.rep[index] ||
          a.lane[index] != b.lane[index])
        return false;
    return true;
  }
};

// One abstract event of the walk: inline frame stack plus offsets into the
// builder's contiguous operand store. No per-event container is allocated.
struct EventRecord {
  int32_t instruction = -1;
  uint8_t depth = 0;
  FrameKey frames[kMaxLoopDepth];
  uint32_t srcOffset = 0;
  uint32_t dstOffset = 0;
};

void appendInt(std::string &out, int64_t value) {
  char buffer[24];
  const int written = std::snprintf(buffer, sizeof(buffer), "%lld",
                                    static_cast<long long>(value));
  if (written > 0)
    out.append(buffer, static_cast<size_t>(written));
}

void appendFrameTag(std::string &out, const LoopShape &loop, int32_t rep,
                    int32_t lane) {
  if (loop.exact) {
    const int64_t iteration =
        loop.repGroups[static_cast<size_t>(rep)] * loop.unroll + lane;
    appendInt(out, iteration);
    return;
  }
  out += "R";
  appendInt(out, rep);
  out += "L";
  appendInt(out, lane);
}

// Diagnostic spelling of an exact event key, identical to the historical
// eventKeyString format. Only eventOrder() (diagnostics) calls this; the normal
// scoring path never formats an event key.
void appendKeyString(std::string &out, const Plan::EventKey &key) {
  out += std::to_string(key.instruction);
  out += "|";
  for (size_t index = 0; index < key.depth; ++index) {
    out += std::to_string(key.frames[index].rep);
    out += ":";
    out += std::to_string(key.frames[index].lane);
    out += ";";
  }
}

// Representative iteration classes derived from the loop's static binding
// distance. A use site that binds inside its own iteration needs no successor;
// a use site that binds the previous iteration needs the class of that
// successor. Hence layers = hop + 1 classes are needed on each boundary and
// the same number of adjacent interior classes, for 3 * layers classes total:
// 3 for hop 0 (first / interior / last) and 6 for hop 1 (first, second, two
// interior copies, penultimate, last). Groups are enumerated individually only
// when they fit inside that class set, so the analysis never depends on (or
// enumerates) the trip count.
std::vector<int64_t> representativeGroups(int64_t groups, int32_t hop) {
  std::vector<int64_t> reps;
  if (groups <= 0)
    return reps;
  const int64_t layers = static_cast<int64_t>(hop) + 1;
  // Widest class set any straight-line canonical body can need. A loop whose
  // groups fit inside it is simply enumerated: exact, cheap, and its digest is
  // comparable with the eager stream. Longer loops use the derived class set.
  const int64_t enumerationCap = 3 * (kMaxBindingHop + 1);
  if (groups <= enumerationCap) {
    for (int64_t group = 0; group < groups; ++group)
      reps.push_back(group);
    return reps;
  }
  for (int64_t layer = 0; layer < layers; ++layer)
    reps.push_back(layer);
  for (int64_t layer = 0; layer < layers; ++layer)
    reps.push_back(layers + layer);
  for (int64_t layer = 0; layer < layers; ++layer)
    reps.push_back(groups - layers + layer);
  return reps;
}

class Builder {
 public:
  // The context is always post-LICM: either built here by buildPlan's own
  // rewrite, or handed in by a caller that already rewrote it (the cursor).
  Builder(const CanonicalVfInfo &vf, const std::vector<CanonicalNode> &context)
      : vf_(vf), context_(context) {}

  std::optional<Plan> run(std::string &why) {
    // The tuple identity encoding is exact when no name can be mis-split. The
    // guard is a property of the declared names, so it is checked over the
    // unique value ids and loop ids (never per operand).
    useTupleIds_ = valueNamesInjectable();
    const size_t declaredValues =
        static_cast<size_t>(vf_.values.size()) * 2 + 64;
    slotByValue_.reserve(declaredValues);
    bindings_.reserve(declaredValues);
    plan_.instructions_.reserve(declaredValues / 2 + 8);
    plan_.srcSlots_.reserve(declaredValues * 3);
    plan_.dstSlots_.reserve(declaredValues);
    std::vector<NodeShape> root;
    if (!buildSequence(context_, 0, root, why))
      return std::nullopt;
    plan_.valueSlotCount_ = bindings_.size();
    plan_.allExact_ = allLoopsExact_;
    for (size_t index = 0; index < plan_.instructions_.size(); ++index)
      plan_.instructionById_.emplace(
          plan_.instructions_[index].instructionId, static_cast<int32_t>(index));
    for (size_t index = 0; index < loops_.size(); ++index) {
      LoopInfo info;
      info.loopId = loops_[index].loopId;
      info.index = loops_[index].index;
      info.count = loops_[index].count;
      info.unroll = loops_[index].unroll;
      info.groups = loops_[index].groups;
      info.hop = loops_[index].hop;
      info.representativeGroup = loops_[index].repGroups;
      info.exactRepresentatives = loops_[index].exact;
      plan_.loops_.push_back(std::move(info));
      plan_.loopById_.emplace(loops_[index].loopId, loops_[index].index);
    }
    // Straight-line program: no loop is ever entered, so every event is a
    // depth-0 root event and identity is the dense value slot itself. The
    // reverse scan then runs directly over the plan's operand stores: no
    // interner, no event key, no per-event frame stack.
    if (loops_.empty())
      return straightLineScan(why) ? std::optional<Plan>(std::move(plan_))
                                  : std::nullopt;
    if (!walkSequence(root, why))
      return std::nullopt;
    if (!reverseScan(why))
      return std::nullopt;
    return std::move(plan_);
  }

 private:
  // --- name-injectivity guard ------------------------------------------------

  bool loopsInjectable(const std::vector<CanonicalNode> &nodes,
                       std::unordered_set<std::string> &loopIds) const {
    for (const auto &node : nodes) {
      const auto *loopHolder =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopHolder == nullptr || *loopHolder == nullptr)
        continue;
      const CanonicalLoop &loop = **loopHolder;
      if (loop.loopId.find('@') != std::string::npos)
        return false;
      if (!loopIds.insert(loop.loopId).second)
        return false;
      for (const auto &carried : loop.carriedValues) {
        if (carried.entryValueId.has_value() &&
            carried.entryValueId->find('@') != std::string::npos)
          return false;
        if (carried.backEdgeValueId.find('@') != std::string::npos ||
            carried.exitValueId.find('@') != std::string::npos)
          return false;
      }
      if (!loopsInjectable(loop.body, loopIds))
        return false;
    }
    return true;
  }

  // Declared names are injective when no value id and no loop id carries the
  // '@' separator and loop ids are unique. Operand value ids are a subset of the
  // declared values (the lowering indexes vf.values for every operand), so this
  // is one walk over the unique names, not one per operand.
  bool valueNamesInjectable() const {
    for (const auto &value : vf_.values)
      if (value.first.find('@') != std::string::npos)
        return false;
    std::unordered_set<std::string> loopIds;
    return loopsInjectable(context_, loopIds);
  }

  // --- shape walk ------------------------------------------------------------

  bool buildSequence(const std::vector<CanonicalNode> &nodes, int32_t depth,
                     std::vector<NodeShape> &out, std::string &why) {
    out.reserve(nodes.size());
    for (const auto &node : nodes) {
      if (const auto *instruction =
              std::get_if<CanonicalInstruction>(&node.payload)) {
        if (instructionIds_.count(instruction->instructionId) != 0) {
          why = "duplicate-instruction-id:" + instruction->instructionId;
          return false;
        }
        instructionIds_.insert(instruction->instructionId);
        const int32_t index = static_cast<int32_t>(plan_.instructions_.size());
        plan_.instructions_.emplace_back();
        StaticInstructionInfo &info = plan_.instructions_.back();
        info.instructionId = instruction->instructionId;
        info.loopPathDepth = static_cast<uint8_t>(loopPath_.size());
        for (size_t frame = 0; frame < loopPath_.size(); ++frame)
          info.loopPath[frame] = loopPath_[frame];
        info.srcOffset = static_cast<uint32_t>(plan_.srcSlots_.size());
        for (const auto &operand : instruction->inputs)
          plan_.srcSlots_.push_back(slotOf(operand.valueId));
        info.srcCount =
            static_cast<uint32_t>(plan_.srcSlots_.size()) - info.srcOffset;
        info.dstOffset = static_cast<uint32_t>(plan_.dstSlots_.size());
        for (const auto &operand : instruction->outputs)
          plan_.dstSlots_.push_back(slotOf(operand.valueId));
        info.dstCount =
            static_cast<uint32_t>(plan_.dstSlots_.size()) - info.dstOffset;
        out.push_back(NodeShape{NodeShape::Instruction, index});
        continue;
      }
      if (std::get_if<CanonicalMembar>(&node.payload) != nullptr) {
        if (!loopPath_.empty())
          loops_[static_cast<size_t>(loopPath_.back())].hasMembar = true;
        continue;
      }
      const auto *loopPtr =
          std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (loopPtr == nullptr || !*loopPtr) {
        why = "null-loop-payload";
        return false;
      }
      if (depth >= kMaxLoopDepth) {
        why = "loop-depth-exceeds-" + std::to_string(kMaxLoopDepth);
        return false;
      }
      const CanonicalLoop &loop = **loopPtr;
      LoopShape shape;
      shape.loopId = loop.loopId;
      shape.index = static_cast<int32_t>(loops_.size());
      shape.depth = depth;
      loops_.push_back(shape);
      LoopShape &current = loops_.back();
      if (!loopPath_.empty())
        loops_[static_cast<size_t>(loopPath_.back())].hasNestedLoop = true;
      int64_t count = 0, unroll = 1;
      if (!parseInteger(loop.count, vf_.params, "loop count", count, why) ||
          !parseInteger(loop.unroll, vf_.params, "loop unroll", unroll, why))
        return false;
      if (count < 0 || unroll <= 0) {
        why = "invalid-count-or-unroll:" + loop.loopId;
        return false;
      }
      if (count > 0 && count % unroll != 0) {
        why = "count-not-divisible-by-unroll:" + loop.loopId;
        return false;
      }
      current.count = count;
      current.unroll = unroll;
      loopPath_.push_back(current.index);
      if (!buildSequence(loop.body, depth + 1, current.body, why)) {
        loopPath_.pop_back();
        return false;
      }
      loopPath_.pop_back();
      if (current.hasMembar && unroll > 1) {
        // Expander disables unroll when the direct body has a membar.
        unroll = 1;
        current.unroll = 1;
      }
      if (count > 0 && count % unroll != 0) {
        why = "count-not-divisible-by-unroll:" + loop.loopId;
        return false;
      }
      if (unroll > 1 && current.hasNestedLoop) {
        why = "unroll-with-nested-loop:" + loop.loopId;
        return false;
      }
      current.groups = unroll > 0 ? count / unroll : 0;
      for (const auto &carried : loop.carriedValues) {
        LoopShape::Carried item;
        item.hasEntry = carried.entryValueId.has_value();
        if (item.hasEntry)
          item.entrySlot = slotOf(*carried.entryValueId);
        item.backSlot = slotOf(carried.backEdgeValueId);
        item.exitSlot = slotOf(carried.exitValueId);
        if (!useTupleIds_)
          item.exitName = carried.exitValueId;
        current.carried.push_back(std::move(item));
      }
      const int32_t hop = deriveLoopHop(current);
      current.hop = hop;
      if (hop > kMaxBindingHop) {
        // Fail-closed: no canonical straight-line body can serve a read from
        // more than one iteration back, so this means the binding model would
        // have to guess. Legacy substring kept for existing fallbacks.
        why = "carried-interior-abstraction:" + current.loopId + ":binding-hop-" +
              std::to_string(hop);
        return false;
      }
      current.repGroups = representativeGroups(current.groups, current.hop);
      current.exact = current.groups <=
                      static_cast<int64_t>(current.repGroups.size());
      allLoopsExact_ = allLoopsExact_ && current.exact;
      out.push_back(NodeShape{NodeShape::Loop, current.index});
    }
    return true;
  }

  // --- static binding distance (dense, allocation free) ----------------------

  // Derives a loop's maximum static binding distance from its use sites.
  //
  // A canonical body is a straight-line sequence of instructions, membars and
  // nested loops, so a read that no instruction of the same iteration defines
  // is always served by the immediately preceding iteration: the distance is 0
  // or 1, never more. Carried entries are rebound at every iteration and
  // therefore couple consecutive iterations by construction.
  //
  // Both marker sets are generation-stamped dense arrays over the value slots:
  // two passes over the loop subtree with no container, so the derivation cost
  // is a small multiple of the body size instead of one hash set per loop.
  void ensureHopCapacity() {
    const size_t slots = bindings_.size();
    if (bodyStamp_.size() < slots) {
      bodyStamp_.resize(slots, 0);
      boundStamp_.resize(slots, 0);
    }
  }

  void collectBodyDefinitions(const std::vector<NodeShape> &nodes,
                              uint32_t generation) {
    for (const auto &node : nodes) {
      if (node.kind == NodeShape::Instruction) {
        const StaticInstructionInfo &info =
            plan_.instructions_[static_cast<size_t>(node.index)];
        for (uint32_t position = 0; position < info.dstCount; ++position)
          bodyStamp_[static_cast<size_t>(
              plan_.dstSlots_[info.dstOffset + position])] = generation;
        continue;
      }
      const LoopShape &inner = loops_[static_cast<size_t>(node.index)];
      collectBodyDefinitions(inner.body, generation);
      // A nested loop's carried exits are rebound in the enclosing scope after
      // the loop, so later reads of them bind inside the same iteration.
      for (const auto &carried : inner.carried)
        bodyStamp_[static_cast<size_t>(carried.exitSlot)] = generation;
    }
  }

  void bindHopSlot(int32_t slot) {
    if (boundStamp_[static_cast<size_t>(slot)] == boundGeneration_)
      return;
    boundStamp_[static_cast<size_t>(slot)] = boundGeneration_;
    if (!hopScopes_.empty())
      hopUndo_.push_back(slot);
  }

  void analyzeBindingDistance(const std::vector<NodeShape> &nodes,
                              uint32_t bodyGeneration, int32_t &hop) {
    for (const auto &node : nodes) {
      if (node.kind == NodeShape::Instruction) {
        const StaticInstructionInfo &info =
            plan_.instructions_[static_cast<size_t>(node.index)];
        for (uint32_t position = 0; position < info.srcCount; ++position) {
          const int32_t slot =
              plan_.srcSlots_[info.srcOffset + position];
          if (boundStamp_[static_cast<size_t>(slot)] != boundGeneration_ &&
              bodyStamp_[static_cast<size_t>(slot)] == bodyGeneration)
            hop = 1;  // served by the previous iteration
        }
        for (uint32_t position = 0; position < info.dstCount; ++position)
          bindHopSlot(plan_.dstSlots_[info.dstOffset + position]);
        continue;
      }
      const LoopShape &inner = loops_[static_cast<size_t>(node.index)];
      // Reads inside the nested loop see the enclosing iteration's bindings;
      // definitions inside it stay in its own scope.
      const size_t mark = hopUndo_.size();
      hopScopes_.push_back(mark);
      analyzeBindingDistance(inner.body, bodyGeneration, hop);
      hopScopes_.pop_back();
      while (hopUndo_.size() > mark) {
        boundStamp_[static_cast<size_t>(hopUndo_.back())] = 0;
        hopUndo_.pop_back();
      }
      for (const auto &carried : inner.carried)
        bindHopSlot(carried.exitSlot);
    }
  }

  int32_t deriveLoopHop(const LoopShape &loop) {
    ensureHopCapacity();
    int32_t hop = 0;
    for (const auto &carried : loop.carried)
      if (carried.hasEntry) {
        hop = 1;
        break;
      }
    if (++bodyGeneration_ == 0) {
      std::fill(bodyStamp_.begin(), bodyStamp_.end(), 0);
      bodyGeneration_ = 1;
    }
    collectBodyDefinitions(loop.body, bodyGeneration_);
    for (const auto &carried : loop.carried)
      bodyStamp_[static_cast<size_t>(carried.backSlot)] = bodyGeneration_;
    if (++boundGeneration_ == 0) {
      std::fill(boundStamp_.begin(), boundStamp_.end(), 0);
      boundGeneration_ = 1;
    }
    hopUndo_.clear();
    hopScopes_.clear();
    const size_t mark = hopUndo_.size();
    hopScopes_.push_back(mark);
    analyzeBindingDistance(loop.body, bodyGeneration_, hop);
    hopScopes_.pop_back();
    while (hopUndo_.size() > mark) {
      boundStamp_[static_cast<size_t>(hopUndo_.back())] = 0;
      hopUndo_.pop_back();
    }
    return hop;
  }

  // --- dense identities ------------------------------------------------------

  // Exact interner: identical strings map to one dense id. This is the only
  // string-keyed structure in the walk; nothing else copies or hashes names.
  IdentityId internString(const std::string &name) {
    const auto found = intern_.find(name);
    if (found != intern_.end())
      return found->second;
    const IdentityId id = static_cast<IdentityId>(intern_.size());
    intern_.emplace(name, id);
    return id;
  }

  // Static value slot: one int32 per value id, assigned when the shape is built.
  // The slot's root identity (the exact value name) is interned here once, so a
  // slot always holds a valid id and resolving it later is a plain array load.
  int32_t slotOf(const std::string &valueId) {
    const auto found = slotByValue_.find(valueId);
    if (found != slotByValue_.end())
      return found->second;
    const int32_t slot = static_cast<int32_t>(bindings_.size());
    slotByValue_.emplace(valueId, slot);
    // Tuple mode: the root identity id is the slot itself (ids are opaque, and
    // the guard makes (slot, frames) equality match name equality exactly).
    bindings_.push_back(useTupleIds_ ? static_cast<IdentityId>(slot)
                                     : internString(valueId));
    // The exact-name fallback needs the value id per slot; tuple mode never
    // materialises a name, so the store stays empty on the scoring path.
    if (!useTupleIds_)
      valueNames_.push_back(valueId);
    return slot;
  }

  IdentityId resolveSlot(int32_t slot) const {
    return bindings_[static_cast<size_t>(slot)];
  }

  void bindSlot(int32_t slot, IdentityId id) {
    IdentityId &binding = bindings_[static_cast<size_t>(slot)];
    if (binding == id)
      return;
    if (!scopes_.empty())
      undo_.push_back({slot, binding});
    binding = id;
  }

  void pushScope() { scopes_.push_back(undo_.size()); }
  void popScope() {
    const size_t mark = scopes_.back();
    scopes_.pop_back();
    while (undo_.size() > mark) {
      const Undo &undo = undo_.back();
      bindings_[static_cast<size_t>(undo.slot)] = undo.value;
      undo_.pop_back();
    }
  }

  // Tuple-mode interner: path identities are keyed by (value slot, frame stack)
  // and get ids after the root-slot range. Nothing is formatted or hashed as a
  // string here.
  IdentityId internIdentityTuple(int32_t slot) {
    IdentityKey key;
    key.slot = slot;
    key.depth = frameDepth_;
    for (uint8_t index = 0; index < frameDepth_; ++index) {
      const FrameKey &frame = frames_[index];
      key.loop[index] = frame.loop;
      key.rep[index] = frame.rep;
      key.lane[index] = frame.lane;
    }
    const auto found = tupleIds_.find(key);
    if (found != tupleIds_.end())
      return found->second;
    const IdentityId id =
        static_cast<IdentityId>(bindings_.size() + tupleIds_.size());
    tupleIds_.emplace(key, id);
    return id;
  }

  // The identity name is generated exactly once into a reused scratch buffer and
  // interned. The interner compares the exact string, so a path-expanded name can
  // never alias a root value name (the old string semantics are preserved).
  IdentityId internIdentityName(int32_t slot) {
    scratch_.clear();
    scratch_.append(valueNames_[static_cast<size_t>(slot)]);
    for (uint8_t index = 0; index < frameDepth_; ++index) {
      const FrameKey &frame = frames_[index];
      const LoopShape &loop = loops_[static_cast<size_t>(frame.loop)];
      scratch_ += "@";
      scratch_ += loop.loopId;
      scratch_ += "=";
      appendFrameTag(scratch_, loop, frame.rep, frame.lane);
    }
    return internString(scratch_);
  }

  // --- abstract walk ---------------------------------------------------------

  bool walkSequence(const std::vector<NodeShape> &nodes, std::string &why) {
    for (const auto &node : nodes) {
      if (node.kind == NodeShape::Instruction) {
        if (!emitInstruction(node.index, why))
          return false;
        continue;
      }
      if (!walkLoop(loops_[static_cast<size_t>(node.index)], why))
        return false;
    }
    return true;
  }

  bool emitInstruction(int32_t index, std::string &why) {
    const StaticInstructionInfo &info =
        plan_.instructions_[static_cast<size_t>(index)];
    EventRecord event;
    event.instruction = index;
    event.depth = frameDepth_;
    for (uint8_t frame = 0; frame < frameDepth_; ++frame)
      event.frames[frame] = frames_[frame];
    event.srcOffset = static_cast<uint32_t>(operands_.size());
    for (uint32_t position = 0; position < info.srcCount; ++position)
      operands_.push_back(
          resolveSlot(plan_.srcSlots_[info.srcOffset + position]));
    event.dstOffset = static_cast<uint32_t>(operands_.size());
    for (uint32_t position = 0; position < info.dstCount; ++position) {
      const int32_t dstSlot = plan_.dstSlots_[info.dstOffset + position];
      const IdentityId identity =
          useTupleIds_ ? internIdentityTuple(dstSlot)
                       : internIdentityName(dstSlot);
      operands_.push_back(identity);
      bindSlot(dstSlot, identity);
    }
    events_.push_back(event);
    if (events_.size() > kMaxAbstractEvents) {
      why = "abstract-event-cap";
      return false;
    }
    return true;
  }

  void bindCarriedEntry(const LoopShape &loop,
                        const std::vector<IdentityId> &values) {
    for (size_t index = 0; index < loop.carried.size(); ++index)
      if (loop.carried[index].hasEntry && values[index] != kNoIdentity)
        bindSlot(loop.carried[index].entrySlot, values[index]);
  }

  // Root identity of a carried exit value: in tuple mode that is the value slot
  // itself; in the exact-name fallback it is the interned declared name (no
  // frame suffix), which is what the historical string builder bound.
  IdentityId rootIdentity(const LoopShape::Carried &carried) {
    return useTupleIds_ ? static_cast<IdentityId>(carried.exitSlot)
                        : internString(carried.exitName);
  }

  bool walkLoop(LoopShape &loop, std::string &why) {
    std::vector<IdentityId> carriedCurrent(loop.carried.size(), kNoIdentity);
    for (size_t index = 0; index < loop.carried.size(); ++index)
      if (loop.carried[index].hasEntry)
        carriedCurrent[index] = resolveSlot(loop.carried[index].entrySlot);
    if (loop.count == 0) {
      for (const auto &carried : loop.carried)
        bindSlot(carried.exitSlot,
                 carried.hasEntry ? resolveSlot(carried.entrySlot)
                                  : rootIdentity(carried));
      return true;
    }
    if (loop.unroll == 1) {
      // One scope per loop like the Expander: per-iteration bindings overwrite
      // inside it, so the back-edge resolves to this iteration's definition and
      // the entry/back-edge slots are restored when the loop scope pops.
      pushScope();
      for (size_t rep = 0; rep < loop.repGroups.size(); ++rep) {
        bindCarriedEntry(loop, carriedCurrent);
        frames_[frameDepth_++] =
            FrameKey{loop.index, static_cast<int32_t>(rep), 0};
        const bool ok = walkSequence(loop.body, why);
        --frameDepth_;
        if (!ok) {
          popScope();
          return false;
        }
        for (size_t index = 0; index < loop.carried.size(); ++index)
          carriedCurrent[index] = resolveSlot(loop.carried[index].backSlot);
      }
      popScope();
      for (size_t index = 0; index < loop.carried.size(); ++index)
        bindSlot(loop.carried[index].exitSlot, carriedCurrent[index]);
      return true;
    }
    // Unrolled loop: bodies contain instructions only (checked while building).
    // Bindings are lane-sequential; the emitted stream is node-priority
    // (position-major, lane-minor) exactly like the eager Expander.
    std::vector<std::vector<std::vector<EventRecord>>> lanes(
        loop.repGroups.size(),
        std::vector<std::vector<EventRecord>>(static_cast<size_t>(loop.unroll)));
    pushScope();
    for (size_t rep = 0; rep < loop.repGroups.size(); ++rep) {
      for (int64_t lane = 0; lane < loop.unroll; ++lane) {
        bindCarriedEntry(loop, carriedCurrent);
        frames_[frameDepth_++] =
            FrameKey{loop.index, static_cast<int32_t>(rep),
                     static_cast<int32_t>(lane)};
        std::vector<EventRecord> bodyEvents;
        for (const auto &node : loop.body) {
          if (node.kind != NodeShape::Instruction) {
            --frameDepth_;
            popScope();
            why = "unroll-body-not-flat:" + loop.loopId;
            return false;
          }
          if (!emitInstruction(node.index, why)) {
            --frameDepth_;
            popScope();
            return false;
          }
          bodyEvents.push_back(events_.back());
          events_.pop_back();
        }
        lanes[rep][static_cast<size_t>(lane)] = std::move(bodyEvents);
        --frameDepth_;
        for (size_t index = 0; index < loop.carried.size(); ++index)
          carriedCurrent[index] = resolveSlot(loop.carried[index].backSlot);
      }
    }
    popScope();
    for (size_t rep = 0; rep < lanes.size(); ++rep) {
      for (size_t position = 0; position < loop.body.size(); ++position) {
        for (size_t lane = 0; lane < lanes[rep].size(); ++lane) {
          events_.push_back(std::move(lanes[rep][lane][position]));
        }
      }
    }
    for (size_t index = 0; index < loop.carried.size(); ++index)
      bindSlot(loop.carried[index].exitSlot, carriedCurrent[index]);
    return true;
  }

  // Straight-line program (no loop is ever entered): the abstract stream is one
  // depth-0 event per static instruction, in static order, and the identity of
  // an operand is its dense value slot. Publishing the records and running the
  // historical reverse release/keep scan therefore needs neither the identity
  // interner nor the exact-key index: the event index is the instruction index.
  bool straightLineScan(std::string &why) {
    const size_t count = plan_.instructions_.size();
    if (count > kMaxAbstractEvents) {
      why = "abstract-event-cap";
      return false;
    }
    plan_.events_.resize(count);
    size_t flagTotal = 0;
    for (size_t index = 0; index < count; ++index) {
      const StaticInstructionInfo &info = plan_.instructions_[index];
      Plan::EventEntry &entry = plan_.events_[index];
      entry.key.instruction = static_cast<int32_t>(index);
      entry.key.depth = 0;
      entry.flagOffset = static_cast<uint32_t>(flagTotal);
      entry.srcCount = info.srcCount;
      entry.dstCount = info.dstCount;
      flagTotal += static_cast<size_t>(info.srcCount) + info.dstCount;
      if (flagTotal > std::numeric_limits<uint32_t>::max()) {
        why = "abstract-flag-store-overflow";
        return false;
      }
    }
    plan_.flags_.assign(flagTotal, 0);
    // Dense reverse-seen table over value slots: one byte test/set per operand.
    std::vector<uint8_t> seen(bindings_.size(), 0);
    for (size_t reverse = count; reverse > 0; --reverse) {
      const size_t index = reverse - 1;
      const StaticInstructionInfo &info = plan_.instructions_[index];
      const Plan::EventEntry &entry = plan_.events_[index];
      uint8_t *release = plan_.flags_.data() + entry.flagOffset;
      uint8_t *keep = release + entry.srcCount;
      for (size_t operand = entry.srcCount; operand > 0; --operand) {
        const int32_t slot = plan_.srcSlots_[info.srcOffset + operand - 1];
        release[operand - 1] = seen[static_cast<size_t>(slot)] == 0 ? 1 : 0;
        seen[static_cast<size_t>(slot)] = 1;
      }
      for (size_t operand = 0; operand < entry.dstCount; ++operand) {
        const int32_t slot = plan_.dstSlots_[info.dstOffset + operand];
        keep[operand] = seen[static_cast<size_t>(slot)] != 0 ? 1 : 0;
        seen[static_cast<size_t>(slot)] = 1;
      }
    }
    plan_.denseEventIndex_ = true;
    plan_.abstractEventCount_ = count;
    return true;
  }

  bool reverseScan(std::string &why) {
    // Exact keys and contiguous storage first (emission order = forward order),
    // then the historical reverse scan over value identities. Per event the flag
    // slice is srcRelease bits followed by dstKeep bits, both sized by the static
    // instruction. No string key and no per-event container is built here.
    size_t flagTotal = 0;
    plan_.events_.resize(events_.size());
    plan_.eventByKey_.reserve(events_.size());
    for (size_t index = 0; index < events_.size(); ++index) {
      const EventRecord &event = events_[index];
      const StaticInstructionInfo &info =
          plan_.instructions_[static_cast<size_t>(event.instruction)];
      Plan::EventEntry entry;
      entry.key.instruction = event.instruction;
      entry.key.depth = event.depth;
      for (uint8_t frame = 0; frame < event.depth; ++frame) {
        entry.key.frames[frame].rep = event.frames[frame].rep;
        entry.key.frames[frame].lane = event.frames[frame].lane;
      }
      entry.flagOffset = static_cast<uint32_t>(flagTotal);
      entry.srcCount = info.srcCount;
      entry.dstCount = info.dstCount;
      flagTotal += static_cast<size_t>(entry.srcCount) + entry.dstCount;
      if (flagTotal > std::numeric_limits<uint32_t>::max()) {
        why = "abstract-flag-store-overflow";
        return false;
      }
      plan_.events_[index] = entry;
      if (!plan_.eventByKey_
               .emplace(entry.key, static_cast<uint32_t>(index))
               .second) {
        // Historical failure: two abstract events share one coordinate key.
        why = "duplicate-abstract-event";
        return false;
      }
    }
    plan_.flags_.assign(flagTotal, 0);
    // Dense reverse-seen table over interned identity ids: one byte test/set per
    // operand instead of one string hash lookup plus set-node insertion.
    const size_t seenSize = useTupleIds_
                               ? bindings_.size() + tupleIds_.size()
                               : intern_.size();
    std::vector<uint8_t> seen(seenSize, 0);
    for (size_t reverse = events_.size(); reverse > 0; --reverse) {
      const size_t index = reverse - 1;
      const EventRecord &event = events_[index];
      const Plan::EventEntry &entry = plan_.events_[index];
      uint8_t *release = plan_.flags_.data() + entry.flagOffset;
      uint8_t *keep = release + entry.srcCount;
      for (size_t operand = entry.srcCount; operand > 0; --operand) {
        const IdentityId identity = operands_[event.srcOffset + operand - 1];
        release[operand - 1] = seen[identity] == 0 ? 1 : 0;
        seen[identity] = 1;
      }
      for (size_t operand = 0; operand < entry.dstCount; ++operand) {
        const IdentityId identity = operands_[event.dstOffset + operand];
        keep[operand] = seen[identity] != 0 ? 1 : 0;
        seen[identity] = 1;
      }
    }
    plan_.abstractEventCount_ = events_.size();
    return true;
  }

  const CanonicalVfInfo &vf_;
  const std::vector<CanonicalNode> &context_;
  std::deque<LoopShape> loops_;
  std::vector<int32_t> loopPath_;
  FrameKey frames_[kMaxLoopDepth];
  uint8_t frameDepth_ = 0;
  bool allLoopsExact_ = true;
  // Generation-stamped dense marks for the binding-distance derivation.
  std::vector<uint32_t> bodyStamp_;
  std::vector<uint32_t> boundStamp_;
  uint32_t bodyGeneration_ = 0;
  uint32_t boundGeneration_ = 0;
  std::vector<int32_t> hopUndo_;
  std::vector<size_t> hopScopes_;
  std::vector<EventRecord> events_;
  // Contiguous identity ids of every event: src operands, then dst operands.
  std::vector<IdentityId> operands_;
  // Exclusive: either the tuple interner (guard holds: no name string is ever
  // built) or the exact string interner (guard fails: old string semantics).
  bool useTupleIds_ = false;
  std::unordered_map<IdentityKey, IdentityId, IdentityKeyHash, IdentityKeyEq>
      tupleIds_;
  std::unordered_map<std::string, IdentityId> intern_;
  // Static value slots and their current binding: flat array, no hashing.
  std::unordered_map<std::string, int32_t> slotByValue_;
  std::vector<IdentityId> bindings_;
  // Exact-name fallback only (empty in tuple mode).
  std::vector<std::string> valueNames_;
  struct Undo {
    int32_t slot = -1;
    IdentityId value = kNoIdentity;
  };
  std::vector<Undo> undo_;
  std::vector<size_t> scopes_;
  // Reused identity-name buffer: names are generated once and interned.
  std::string scratch_;
  std::unordered_set<std::string> instructionIds_;
  Plan plan_;
};

}  // namespace

int32_t Plan::instructionIndex(const std::string &staticInstructionId) const {
  const auto found = instructionById_.find(staticInstructionId);
  return found == instructionById_.end() ? -1 : found->second;
}

int32_t Plan::loopIndex(const std::string &loopId) const {
  const auto found = loopById_.find(loopId);
  return found == loopById_.end() ? -1 : found->second;
}

IterationCoordinate Plan::coordinateFor(int32_t loopIndex,
                                        int64_t iteration) const {
  const LoopInfo &loop = loops_[static_cast<size_t>(loopIndex)];
  IterationCoordinate coordinate;
  if (loop.unroll <= 0 || loop.groups <= 0)
    return coordinate;
  const int64_t group = iteration / loop.unroll;
  coordinate.lane = static_cast<int32_t>(iteration % loop.unroll);
  if (loop.exactRepresentatives) {
    coordinate.rep = static_cast<int32_t>(group);
    return coordinate;
  }
  // Classes were built as first layer(s), interior copy/copies, last layer(s)
  // with layers = hop + 1; interior classes all share the last interior copy,
  // whose abstract successor is the exit layer. That is exact for every
  // interior group: its binding chain to the following iteration is the same
  // and the successor class only affects that successor's own flags.
  const int64_t layers = static_cast<int64_t>(loop.hop) + 1;
  if (group < layers) {
    coordinate.rep = static_cast<int32_t>(group);
    return coordinate;
  }
  if (group >= loop.groups - layers) {
    coordinate.rep = static_cast<int32_t>(3 * layers - 1 -
                                         (loop.groups - 1 - group));
    return coordinate;
  }
  if (group == layers) {
    // The first interior group has the second entry layer as its predecessor and
    // one interior copy as its successor; it is represented by that first copy.
    coordinate.rep = static_cast<int32_t>(layers);
    return coordinate;
  }
  // Remaining interior groups share the last interior copy, whose abstract
  // successor is the exit layer: their binding chain to the following
  // iteration is identical to that of their real successor.
  coordinate.rep = static_cast<int32_t>(2 * layers - 1);
  return coordinate;
}

bool Plan::iterationKeyFor(int32_t instructionIndex,
                           const std::vector<int64_t> &iterations,
                           IterationKey &out) const {
  if (instructionIndex < 0 ||
      static_cast<size_t>(instructionIndex) >= instructions_.size())
    return false;
  const auto &info = instructions_[static_cast<size_t>(instructionIndex)];
  if (info.loopPathSize() != iterations.size())
    return false;
  out.frames.clear();
  out.frames.reserve(iterations.size());
  for (size_t index = 0; index < iterations.size(); ++index) {
    const auto coordinate = coordinateFor(info.loopPath[index], iterations[index]);
    out.frames.push_back({coordinate.rep, coordinate.lane});
  }
  return true;
}

namespace {
// Builds the exact key for (static instruction, real representative coordinates).
// Returns false when the instruction is unknown or the coordinate depth does not
// match the instruction's enclosing-loop depth, exactly like the accessor
// contract before this change.
bool exactKeyFor(const Plan &plan, int32_t instructionIndex,
                 const IterationKey &key, Plan::EventKey &out) {
  if (instructionIndex < 0 ||
      static_cast<size_t>(instructionIndex) >= plan.instructions().size())
    return false;
  const auto &info = plan.instructions()[static_cast<size_t>(instructionIndex)];
  if (key.frames.size() != info.loopPathSize() ||
      key.frames.size() > Plan::kMaxEventDepth)
    return false;
  out.instruction = instructionIndex;
  out.depth = static_cast<uint8_t>(key.frames.size());
  for (size_t index = 0; index < key.frames.size(); ++index)
    out.frames[index] = key.frames[index];
  return true;
}

const Plan::EventEntry *findEvent(const Plan &plan, int32_t instructionIndex,
                                  const IterationKey &key) {
  Plan::EventKey exact;
  if (!exactKeyFor(plan, instructionIndex, key, exact))
    return nullptr;
  if (plan.denseEventIndex_ && exact.depth == 0) {
    // Straight-line plan: the event index is the instruction index, so the
    // diagnostic accessors and the scored hot path resolve the same record
    // without the (empty) exact-key index.
    const Plan::EventEntry &candidate =
        plan.events_[static_cast<size_t>(instructionIndex)];
    if (candidate.key.instruction != instructionIndex || candidate.key.depth != 0)
      return nullptr;
    return &candidate;
  }
  const auto found = plan.eventByKey_.find(exact);
  return found == plan.eventByKey_.end() ? nullptr : &plan.events_[found->second];
}
}  // namespace

bool Plan::hasEvent(int32_t instructionIndex, const IterationKey &key) const {
  return findEvent(*this, instructionIndex, key) != nullptr;
}

bool Plan::srcRelease(int32_t instructionIndex, const IterationKey &key,
                      size_t srcIndex) const {
  const EventEntry *entry = findEvent(*this, instructionIndex, key);
  if (entry == nullptr || srcIndex >= entry->srcCount)
    return false;
  return flags_[entry->flagOffset + srcIndex] != 0;
}

bool Plan::dstKeep(int32_t instructionIndex, const IterationKey &key,
                   size_t dstIndex) const {
  const EventEntry *entry = findEvent(*this, instructionIndex, key);
  if (entry == nullptr || dstIndex >= entry->dstCount)
    return false;
  return flags_[entry->flagOffset + entry->srcCount + dstIndex] != 0;
}

namespace {
inline void mixKeyValue(uint64_t &hash, int64_t value) {
  hash ^= static_cast<uint64_t>(value) + 0x9e3779b97f4a7c15ull +
          (hash << 6) + (hash >> 2);
}
}  // namespace

uint64_t Plan::numericKey(int32_t instructionIndex, const IterationKey &key) {
  uint64_t hash = 1469598103934665603ull;
  mixKeyValue(hash, instructionIndex);
  for (const auto &frame : key.frames) {
    mixKeyValue(hash, frame.rep);
    mixKeyValue(hash, frame.lane);
  }
  return hash;
}

bool Plan::eventFlagsByCoord(int32_t instructionIndex,
                             const IterationCoordinate *frames, uint8_t depth,
                             const uint8_t **srcRelease,
                             size_t *srcReleaseCount, const uint8_t **dstKeep,
                             size_t *dstKeepCount) const {
  // Same validation and failure semantics as eventFlagsForIterations, but the
  // representative coordinates are already amortised by the caller. A depth of 0
  // owns no coordinate and never reads the frame pointer.
  if (instructionIndex < 0 ||
      static_cast<size_t>(instructionIndex) >= instructions_.size())
    return false;
  const StaticInstructionInfo &info =
      instructions_[static_cast<size_t>(instructionIndex)];
  if (depth != info.loopPathDepth || depth > kMaxEventDepth)
    return false;
  const EventEntry *entry = nullptr;
  if (denseEventIndex_ && depth == 0) {
    // Straight-line plan: exactly one depth-0 event per static instruction.
    const EventEntry &candidate = events_[static_cast<size_t>(instructionIndex)];
    if (candidate.key.instruction != instructionIndex || candidate.key.depth != 0)
      return false;
    entry = &candidate;
  } else {
    EventKey exact;
    exact.instruction = instructionIndex;
    exact.depth = depth;
    for (uint8_t index = 0; index < depth; ++index)
      exact.frames[index] = frames[index];
    const auto found = eventByKey_.find(exact);
    if (found == eventByKey_.end())
      return false;
    entry = &events_[found->second];
  }
  if (srcRelease != nullptr)
    *srcRelease = flags_.data() + entry->flagOffset;
  if (srcReleaseCount != nullptr)
    *srcReleaseCount = entry->srcCount;
  if (dstKeep != nullptr)
    *dstKeep = flags_.data() + entry->flagOffset + entry->srcCount;
  if (dstKeepCount != nullptr)
    *dstKeepCount = entry->dstCount;
  return true;
}

bool Plan::eventFlags(int32_t instructionIndex, const IterationKey &key,
                      const uint8_t **srcRelease, size_t *srcReleaseCount,
                      const uint8_t **dstKeep, size_t *dstKeepCount) const {
  if (instructionIndex < 0 ||
      static_cast<size_t>(instructionIndex) >= instructions_.size())
    return false;
  const StaticInstructionInfo &info =
      instructions_[static_cast<size_t>(instructionIndex)];
  if (key.frames.size() != info.loopPathSize() ||
      key.frames.size() > kMaxEventDepth)
    return false;
  return eventFlagsByCoord(instructionIndex, key.frames.data(),
                           static_cast<uint8_t>(key.frames.size()), srcRelease,
                           srcReleaseCount, dstKeep, dstKeepCount);
}

bool Plan::eventFlagsForIterations(int32_t instructionIndex,
                                   const std::vector<int64_t> &iterations,
                                   const uint8_t **srcRelease,
                                   size_t *srcReleaseCount,
                                   const uint8_t **dstKeep,
                                   size_t *dstKeepCount) const {
  // Iteration numbers -> representative coordinates, then the same lookup the
  // hot path uses. Kept for standalone callers; the streaming cursor hands the
  // coordinates over directly.
  if (instructionIndex < 0 ||
      static_cast<size_t>(instructionIndex) >= instructions_.size())
    return false;
  const StaticInstructionInfo &info =
      instructions_[static_cast<size_t>(instructionIndex)];
  if (iterations.size() != info.loopPathSize() ||
      iterations.size() > kMaxEventDepth)
    return false;
  EventKey exact;
  exact.instruction = instructionIndex;
  exact.depth = static_cast<uint8_t>(iterations.size());
  for (size_t index = 0; index < iterations.size(); ++index) {
    const IterationCoordinate coordinate =
        coordinateFor(info.loopPath[index], iterations[index]);
    exact.frames[index].rep = coordinate.rep;
    exact.frames[index].lane = coordinate.lane;
  }
  const EventEntry *entry = nullptr;
  if (denseEventIndex_ && exact.depth == 0) {
    const EventEntry &candidate = events_[static_cast<size_t>(instructionIndex)];
    if (candidate.key.instruction != instructionIndex || candidate.key.depth != 0)
      return false;
    entry = &candidate;
  } else {
    const auto found = eventByKey_.find(exact);
    if (found == eventByKey_.end())
      return false;
    entry = &events_[found->second];
  }
  if (srcRelease != nullptr)
    *srcRelease = flags_.data() + entry->flagOffset;
  if (srcReleaseCount != nullptr)
    *srcReleaseCount = entry->srcCount;
  if (dstKeep != nullptr)
    *dstKeep = flags_.data() + entry->flagOffset + entry->srcCount;
  if (dstKeepCount != nullptr)
    *dstKeepCount = entry->dstCount;
  return true;
}

const std::vector<std::string> &Plan::eventOrder() const {
  // Diagnostic-only cache: the strings are rebuilt from the exact keys on first
  // call. Normal scoring never asks for them, so it never pays for key text.
  if (!orderedKeysBuilt_) {
    orderedKeys_.clear();
    orderedKeys_.reserve(events_.size());
    for (const auto &entry : events_) {
      std::string key;
      key.reserve(16 + static_cast<size_t>(entry.key.depth) * 16);
      appendKeyString(key, entry.key);
      orderedKeys_.push_back(std::move(key));
    }
    orderedKeysBuilt_ = true;
  }
  return orderedKeys_;
}

namespace {
void mixInt(uint64_t &hash, int64_t value) {
  for (int i = 0; i < 8; ++i) {
    hash ^= static_cast<unsigned char>(
        (static_cast<uint64_t>(value) >> (i * 8)) & 0xff);
    hash *= 1099511628211ull;
  }
}
}  // namespace

uint64_t Plan::digestLife() const {
  // Same mixing and emission order as the eager oracle's digest_life, read
  // directly from the contiguous store: no key strings are involved.
  uint64_t hash = 1469598103934665603ull;
  for (const auto &entry : events_) {
    for (uint32_t index = 0; index < entry.srcCount; ++index)
      mixInt(hash, flags_[entry.flagOffset + index]);
    for (uint32_t index = 0; index < entry.dstCount; ++index)
      mixInt(hash, flags_[entry.flagOffset + entry.srcCount + index]);
  }
  return hash;
}

static_assert(kMaxLoopDepth <= Plan::kMaxEventDepth,
              "exact event key cannot hold the builder's loop depth limit");

std::optional<Plan> buildPlan(const CanonicalVfInfo &vf,
                              std::string &declineReason) {
  CompilerOptimizationStats stats;
  const auto hoisted = hoistLoopInvariantContext(vf, stats);
  const std::vector<CanonicalNode> &context = hoisted ? *hoisted : vf.context;
  return buildPlanForContext(vf, context, declineReason);
}

std::optional<Plan> buildPlanForContext(
    const CanonicalVfInfo &vf, const std::vector<CanonicalNode> &context,
    std::string &declineReason) {
  Builder builder(vf, context);
  auto plan = builder.run(declineReason);
  if (!plan && !declineReason.empty() &&
      declineReason.rfind("lifetime-plan-decline:", 0) != 0)
    declineReason = "lifetime-plan-decline:" + declineReason;
  return plan;
}

}  // namespace cursor_lifetime
}  // namespace vfsim
