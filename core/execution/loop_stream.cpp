#include "core/execution/loop_stream.h"

#include "core/execution/engine.h"
#include "core/execution/expansion.h"
#include "core/params/database.h"

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {
namespace {

int64_t resolveInteger(const CanonicalIntegerExpression &expr,
                       const std::unordered_map<std::string, int64_t> &params,
                       const char *what) {
  if (const auto *v = std::get_if<int64_t>(&expr))
    return *v;
  const auto &text = std::get<std::string>(expr);
  if (auto it = params.find(text); it != params.end())
    return it->second;
  size_t consumed = 0;
  try {
    const int64_t v = std::stoll(text, &consumed, 10);
    if (consumed == text.size())
      return v;
  } catch (...) {
  }
  throw std::runtime_error(std::string("cannot resolve ") + what + ": " + text);
}

void mix(uint64_t &h, const std::string &s) {
  for (unsigned char c : s) {
    h ^= c;
    h *= 1099511628211ull;
  }
  h ^= 0xffu;
  h *= 1099511628211ull;
}
void mix(uint64_t &h, int64_t v) {
  for (int i = 0; i < 8; ++i) {
    h ^= static_cast<unsigned char>((static_cast<uint64_t>(v) >> (i * 8)) & 0xff);
    h *= 1099511628211ull;
  }
}
void mixAccess(uint64_t &h, const CanonicalMemoryAccess &a) {
  mix(h, a.baseObjectId);
  mix(h, a.offset.constant);
  for (const auto &t : a.offset.terms) {
    mix(h, t.variableId);
    mix(h, t.coefficient);
  }
  mix(h, static_cast<int64_t>(a.accessKind));
  mix(h, a.span.value_or(-1));
  mix(h, a.aliasGroup.value_or(""));
  mix(h, a.addressStateId.value_or(""));
  mix(h, a.updateMode);
  mix(h, a.addressUnitBytes.value_or(-1));
}
void mixStructure(uint64_t &h, const DynamicInst &d) {
  mix(h, d.op); mix(h, d.form); mix(h, d.opClass);
  mix(h, d.topBlockId); mix(h, d.streamSeq); mix(h, d.instId);
  for (const auto &s : d.src) mix(h, s); mix(h, static_cast<int64_t>(d.src.size()));
  for (const auto &s : d.dst) mix(h, s); mix(h, static_cast<int64_t>(d.dst.size()));
  for (auto v : d.iterStack) mix(h, v);
  mix(h, static_cast<int64_t>(d.iterStack.size()));
  mix(h, static_cast<int64_t>(d.isLastInTopBlock));
}
void mixLifetime(uint64_t &h, const DynamicInst &d) {
  for (bool b : d.srcValueRelease) mix(h, static_cast<int64_t>(b));
  for (bool b : d.dstValueKeep) mix(h, static_cast<int64_t>(b));
}
void mixAddress(uint64_t &h, const DynamicInst &d) {
  for (const auto &a : d.memoryAccesses) mixAccess(h, a);
}
void mixInst(uint64_t &h, const DynamicInst &d) {
  mixStructure(h, d);
  mixLifetime(h, d);
  mixAddress(h, d);
}

struct LoopPlan {
  const CanonicalLoop *loop = nullptr;
  std::string loopId;
  int64_t count = 0;
  int64_t start = 0;
  int64_t step = 1;
  int64_t topBlock = 0;
  int64_t bodySize = 0;
  bool completed = false;
};

enum class SegmentKind { Instruction, Loop, Membar };
struct Segment {
  SegmentKind kind = SegmentKind::Instruction;
  int64_t topBlock = 0;
  int64_t templateStart = 0;
  int64_t templateCount = 0;
  int64_t loopIndex = -1;
  int64_t sequence = 0;
  bool lastInTopBlock = false;
};

struct NameMapper {
  const std::vector<LoopPlan> &loops;
  const std::unordered_map<std::string, int64_t> &loopIndex;
  int64_t currentLoop = -1;
  int64_t currentIteration = 0;

  int64_t iterationFor(int64_t index) const {
    if (loops[static_cast<size_t>(index)].completed)
      return loops[static_cast<size_t>(index)].count - 1;
    if (index == currentLoop)
      return currentIteration;
    return 0;
  }

  std::string map(const std::string &name) const {
    std::string result = name;
    size_t at = 0;
    while ((at = result.find('@', at)) != std::string::npos) {
      const size_t eq = result.find('=', at + 1);
      if (eq == std::string::npos)
        break;
      const std::string id = result.substr(at + 1, eq - at - 1);
      auto found = loopIndex.find(id);
      if (found == loopIndex.end()) {
        at = eq + 1;
        continue;
      }
      size_t begin = eq + 1;
      size_t end = begin;
      while (end < result.size() && result[end] >= '0' && result[end] <= '9')
        ++end;
      const std::string replacement = std::to_string(iterationFor(found->second));
      result.replace(begin, end - begin, replacement);
      at = begin + replacement.size();
    }
    return result;
  }
};

void remapPath(std::vector<IterationIdentity> &path,
               const std::vector<LoopPlan> &loops,
               const std::unordered_map<std::string, int64_t> &loopIndex,
               int64_t currentLoop, int64_t currentIteration) {
  for (auto &identity : path) {
    auto found = loopIndex.find(identity.loopId);
    if (found == loopIndex.end())
      continue;
    const auto &loop = loops[static_cast<size_t>(found->second)];
    const int64_t iteration =
        loop.completed ? loop.count - 1
                       : (found->second == currentLoop ? currentIteration : 0);
    identity.iteration = iteration;
    identity.inductionValue = loop.start + iteration * loop.step;
  }
}

void remapInstances(std::vector<ValueInstance> &instances,
                    const std::vector<LoopPlan> &loops,
                    const std::unordered_map<std::string, int64_t> &loopIndex,
                    int64_t currentLoop, int64_t currentIteration) {
  for (auto &instance : instances) {
    std::string result = instance.definitionId;
    size_t at = 0;
    while ((at = result.find('@', at)) != std::string::npos) {
      const size_t eq = result.find('=', at + 1);
      if (eq == std::string::npos)
        break;
      const std::string id = result.substr(at + 1, eq - at - 1);
      auto found = loopIndex.find(id);
      if (found == loopIndex.end()) {
        at = eq + 1;
        continue;
      }
      size_t begin = eq + 1;
      size_t end = begin;
      while (end < result.size() && result[end] >= '0' && result[end] <= '9')
        ++end;
      const auto &loop = loops[static_cast<size_t>(found->second)];
      const int64_t iteration =
          loop.completed ? loop.count - 1
                         : (found->second == currentLoop ? currentIteration : 0);
      const std::string replacement = std::to_string(iteration);
      result.replace(begin, end - begin, replacement);
      at = begin + replacement.size();
    }
    instance.definitionId = std::move(result);
    remapPath(instance.iterationPath, loops, loopIndex, currentLoop,
              currentIteration);
  }
}

bool isHorizontal(const std::string &op) {
  return op == "VCADD" || op == "VCMIN" || op == "VCMAX";
}
bool isPredicateSet(const std::string &op) {
  return op == "PSET_B8" || op == "PSET_B16" || op == "PSET_B32";
}
bool isVectorDup(const std::string &op) {
  return op == "VDUP" || op == "VDUPS";
}
bool isVectorLoad(const std::string &op) {
  return op == "VLDS" || op == "PLDS";
}



struct StreamState {
  const PreparedProgram &templateProgram;
  const std::vector<Segment> &segments;
  const std::vector<int32_t> &operandSrc;
  const std::vector<int32_t> &operandDst;
  const std::vector<uint32_t> &operandSrcBegin;
  const std::vector<uint32_t> &operandDstBegin;
  const std::vector<int32_t> &valueLoop;
  std::vector<int32_t> &operandIds;
  int32_t operandBlockSize = 0;
  int32_t iterationRing = 64;
  int32_t maxSlots = 0;
  std::vector<LoopPlan> &loops;
  const std::unordered_map<std::string, int64_t> &loopIndex;
  uint64_t &digest;
  uint64_t &structureDigest;
  uint64_t &lifetimeDigest;
  uint64_t &addressDigest;
  bool collectDigest = false;
  bool collectTiming = false;
  double &fetchSeconds;
  int64_t &lookupOps;
  int64_t &fetched;
  int64_t &highWater;
  int64_t &numericPeakId;
  int64_t targetDynamic = 0;
  bool namesRemapSafe = false;
  int64_t nextStreamSeq = 0;
  int64_t nextInstId = 0;
  size_t segmentIndex = 0;
  int64_t iteration = 0;
  int64_t instructionInSegment = 0;

  bool done() const { return fetched >= targetDynamic; }

  void makeOne(DynamicInst &d, const Segment &segment, int64_t currentLoop,
               int64_t currentIteration, int64_t offset, bool last) {
    std::optional<std::chrono::steady_clock::time_point> started;
    if (collectTiming)
      started = std::chrono::steady_clock::now();
    const int32_t operandBase = d.srcIdBase;
    d = templateProgram.instructions[
        static_cast<size_t>(segment.templateStart + offset)];
    const auto &base = templateProgram.instructions[
        static_cast<size_t>(segment.templateStart + offset)];
    NameMapper mapper{loops, loopIndex, currentLoop, currentIteration};
    d.topBlockId = segment.topBlock;
    d.streamSeq = nextStreamSeq++;
    d.instId = d.type == "membar" ? -1 : nextInstId++;
    d.srcIdBase = operandBase;
    d.dstIdBase = operandBase + static_cast<int32_t>(d.src.size());
    const size_t layoutIndex =
        static_cast<size_t>(segment.templateStart + offset);
    const size_t srcBegin = operandSrcBegin[layoutIndex];
    const size_t dstBegin = operandDstBegin[layoutIndex];
    auto decodeId = [&](int32_t slot, int64_t loop) {
      int64_t iterationInstance = 0;
      if (loop >= 0) {
        const auto &valueOwner = loops[static_cast<size_t>(loop)];
        iterationInstance =
            valueOwner.completed
                ? (valueOwner.count - 1) % iterationRing
                : ((loop == currentLoop ? currentIteration : 0) % iterationRing);
      }
      const int32_t id =
          slot * iterationRing + static_cast<int32_t>(iterationInstance);
      numericPeakId = std::max<int64_t>(numericPeakId, id + 1);
      return id;
    };
    if (operandBase >= 0 && operandBlockSize > 0) {
      const size_t base = static_cast<size_t>(operandBase);
      if (base + d.src.size() + d.dst.size() > operandIds.size())
        throw std::runtime_error("stream operand id table overflow");
      for (size_t i = 0; i < d.src.size() && srcBegin + i < operandSrc.size(); ++i)
        operandIds[base + i] = decodeId(
            operandSrc[srcBegin + i],
            valueLoop[static_cast<size_t>(operandSrc[srcBegin + i])]);
      const size_t dstBase = base + d.src.size();
      for (size_t i = 0; i < d.dst.size() && dstBegin + i < operandDst.size(); ++i)
        operandIds[dstBase + i] = decodeId(
            operandDst[dstBegin + i],
            valueLoop[static_cast<size_t>(operandDst[dstBegin + i])]);
    }
    d.isLastInTopBlock = last;
    const auto &originalRelease = base.srcValueRelease;
    if (!namesRemapSafe) {
      for (size_t i = 0; i < d.src.size(); ++i) {
        const int32_t id = operandSrc[srcBegin + i];
        if (static_cast<size_t>(id) < valueLoop.size() && valueLoop[id] >= 0)
          d.src[i] = mapper.map(d.src[i]);
      }
      for (size_t i = 0; i < d.dst.size(); ++i) {
        const int32_t id = operandDst[dstBegin + i];
        if (static_cast<size_t>(id) < valueLoop.size() && valueLoop[id] >= 0)
          d.dst[i] = mapper.map(d.dst[i]);
      }
    }
    if (collectDigest) {
      remapInstances(d.srcValueInstances, loops, loopIndex, currentLoop,
                     currentIteration);
      remapInstances(d.dstValueInstances, loops, loopIndex, currentLoop,
                     currentIteration);
      remapPath(d.iterationPath, loops, loopIndex, currentLoop,
                currentIteration);
    }
    if (currentLoop >= 0)
      d.iterStack.assign(1, currentIteration);
    for (auto &key : d.schedulingKeys) {
      key.topBlockId = segment.topBlock;
      if (currentLoop >= 0 && key.depth >= 1)
        key.c0 = currentIteration;
    }
    d.normalizedBlockKeys.clear();
    d.blockKeyByLevel.clear();
    if (currentLoop >= 0 && currentIteration + 1 < loops[currentLoop].count) {
      for (size_t i = 0; i < originalRelease.size() && i < d.src.size(); ++i) {
        if (!originalRelease[i])
          continue;
        const int32_t id = operandSrc[srcBegin + i];
        const int32_t owner =
            static_cast<size_t>(id) < valueLoop.size() ? valueLoop[id] : -1;
        if (owner != currentLoop)
          d.srcValueRelease[i] = false;
      }
    }
    if (collectDigest) {
      if (namesRemapSafe) {
        DynamicInst observed = d;
        observed.src.clear();
        observed.src.reserve(base.src.size());
        for (size_t i = 0; i < base.src.size(); ++i) {
          const int32_t id = operandSrc[srcBegin + i];
          const bool dynamic =
              static_cast<size_t>(id) < valueLoop.size() && valueLoop[id] >= 0;
          observed.src.push_back(dynamic ? mapper.map(base.src[i]) : base.src[i]);
        }
        observed.dst.clear();
        observed.dst.reserve(base.dst.size());
        for (size_t i = 0; i < base.dst.size(); ++i) {
          const int32_t id = operandDst[dstBegin + i];
          const bool dynamic =
              static_cast<size_t>(id) < valueLoop.size() && valueLoop[id] >= 0;
          observed.dst.push_back(dynamic ? mapper.map(base.dst[i]) : base.dst[i]);
        }
        mixInst(digest, observed);
        mixStructure(structureDigest, observed);
      } else {
        mixInst(digest, d);
        mixStructure(structureDigest, d);
      }
      mixLifetime(lifetimeDigest, d);
      mixAddress(addressDigest, d);
    }
    lookupOps += static_cast<int64_t>(d.src.size() + d.dst.size());
    ++fetched;
    highWater = std::max<int64_t>(highWater, 1);
    if (started)
      fetchSeconds += std::chrono::duration<double>(
                          std::chrono::steady_clock::now() - *started)
                          .count();
  }

  void nextInto(DynamicInst &out) {
    while (segmentIndex < segments.size()) {
      const Segment &segment = segments[segmentIndex];
      if (segment.kind != SegmentKind::Loop) {
        makeOne(out, segment, -1, 0, 0, segment.lastInTopBlock);
        ++segmentIndex;
        return;
      }
      LoopPlan &loop = loops[static_cast<size_t>(segment.loopIndex)];
      if (segment.templateCount == 0) {
        loop.completed = true;
        ++segmentIndex;
        iteration = 0;
        instructionInSegment = 0;
        continue;
      }
      if (iteration >= loop.count) {
        loop.completed = true;
        ++segmentIndex;
        iteration = 0;
        instructionInSegment = 0;
        continue;
      }
      if (instructionInSegment >= segment.templateCount) {
        if (iteration + 1 == loop.count)
          loop.completed = true;
        ++iteration;
        instructionInSegment = 0;
        continue;
      }
      const bool lastInstruction =
          iteration + 1 == loop.count &&
          instructionInSegment + 1 == segment.templateCount &&
          segment.lastInTopBlock;
      makeOne(out, segment, segment.loopIndex, iteration,
              instructionInSegment, lastInstruction);
      ++instructionInSegment;
      if (instructionInSegment >= segment.templateCount) {
        if (iteration + 1 == loop.count)
          loop.completed = true;
        ++iteration;
        instructionInSegment = 0;
      }
      return;
    }
    throw std::runtime_error("stream provider exhausted");
  }
};

} // namespace

std::optional<SimulationResult>
tryRunLoopStream(const CanonicalVfInfo &vf, const ParamDB &db,
                 const UarchConfig &cfg, int64_t maxCycles,
                 LoopStreamStats *stats) {
  LoopStreamStats local;
  LoopStreamStats &st = stats ? *stats : local;
  const bool collectDigest = st.collectVerificationDigest;
  const bool collectTiming = st.collectTiming;
  st = LoopStreamStats{};
  st.collectVerificationDigest = collectDigest;
  st.collectTiming = collectTiming;
  st.attempted = true;
  auto decline = [&](std::string why) -> std::optional<SimulationResult> {
    st.declineReason = std::move(why);
    return std::nullopt;
  };

  CompilerOptimizationStats licmStats;
  const auto hoistedContext = hoistLoopInvariantContext(vf, licmStats);
  const std::vector<CanonicalNode> &streamContext =
      hoistedContext ? *hoistedContext : vf.context;
  if (licmStats.hoistedInstructionCount > 0)
    (void)0;

  struct BuildPlan {
    std::vector<Segment> segments;
    std::vector<LoopPlan> loops;
    int64_t nextTopBlock = 0;
    int64_t templateCursor = 0;
    int64_t totalDynamic = 0;
  } plan;
  int64_t activeTopBlock = 0;
  bool sawLoop = false;
  for (const auto &node : streamContext) {
    if (const auto *inst = std::get_if<CanonicalInstruction>(&node.payload)) {
      (void)inst;
      Segment s;
      s.kind = SegmentKind::Instruction;
      s.topBlock = activeTopBlock;
      s.templateStart = plan.templateCursor++;
      s.templateCount = 1;
      s.sequence = static_cast<int64_t>(plan.segments.size());
      plan.totalDynamic += 1;
      plan.segments.push_back(s);
      continue;
    }
    if (std::holds_alternative<CanonicalMembar>(node.payload)) {
      Segment s;
      s.kind = SegmentKind::Membar;
      s.topBlock = activeTopBlock;
      s.templateStart = plan.templateCursor++;
      s.templateCount = 1;
      s.sequence = static_cast<int64_t>(plan.segments.size());
      plan.totalDynamic += 1;
      plan.segments.push_back(s);
      continue;
    }
    const auto *loopPtr =
        std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
    if (!loopPtr || !*loopPtr)
      return decline("non-loop-mixed-root");
    const CanonicalLoop &loop = **loopPtr;
    sawLoop = true;
    if (!loop.carriedValues.empty())
      return decline("loop-carried-value");
    int64_t count = 0, unroll = 1;
    try {
      count = resolveInteger(loop.count, vf.params, "loop count");
      unroll = resolveInteger(loop.unroll, vf.params, "loop unroll");
    } catch (const std::exception &e) {
      st.declineReason = e.what();
      return decline(std::string("non-fixed-loop: ") + e.what());
    }
    if (count <= 0 || unroll != 1)
      return decline("non-positive-or-unrolled-loop");
    for (const auto &bodyNode : loop.body) {
      if (std::holds_alternative<CanonicalMembar>(bodyNode.payload))
        return decline("membar-in-loop");
      if (std::holds_alternative<std::shared_ptr<const CanonicalLoop>>(
              bodyNode.payload))
        return decline("nested-loop");
      const auto *inst = std::get_if<CanonicalInstruction>(&bodyNode.payload);
      if (!inst)
        return decline("non-instruction-loop-body");
      for (const auto *operands : {&inst->inputs, &inst->outputs}) {
        for (const auto &op : *operands) {
          if (!op.memoryAccess)
            continue;
          if (op.memoryAccess->aliasGroup &&
              !op.memoryAccess->aliasGroup->empty())
            return decline("alias-group-not-proven");
          if (op.memoryAccess->addressStateId &&
              !op.memoryAccess->addressStateId->empty())
            return decline("address-state-not-proven");
          for (const auto &term : op.memoryAccess->offset.terms)
            if (term.variableId != loop.induction.variableId)
              return decline("non-local-affine-term");
        }
      }
    }
    LoopPlan lp;
    lp.loop = &loop;
    lp.loopId = loop.loopId;
    lp.count = count;
    lp.start = resolveInteger(loop.induction.start, vf.params, "induction start");
    lp.step = resolveInteger(loop.induction.step, vf.params, "induction step");
    lp.topBlock = plan.nextTopBlock++;
    lp.bodySize = static_cast<int64_t>(loop.body.size());
    Segment s;
    s.kind = SegmentKind::Loop;
    s.topBlock = lp.topBlock;
    s.templateStart = plan.templateCursor;
    s.templateCount = lp.bodySize;
    s.loopIndex = static_cast<int64_t>(plan.loops.size());
    s.sequence = static_cast<int64_t>(plan.segments.size());
    plan.templateCursor += lp.bodySize;
    plan.totalDynamic += count * lp.bodySize;
    plan.segments.push_back(s);
    plan.loops.push_back(lp);
    activeTopBlock = lp.topBlock;
  }
  if (!sawLoop)
    return decline("no-loop");
  st.staticInstructions = plan.templateCursor;
  st.staticTemplateBytes = plan.templateCursor * static_cast<int64_t>(sizeof(DynamicInst));
  st.staticLoopNodes = static_cast<int64_t>(plan.loops.size());
  int64_t maxLoopCount = 0;
  for (const auto &loop : plan.loops)
    maxLoopCount = std::max(maxLoopCount, loop.count);
  constexpr int64_t kMaxIterationRing = 64;
  if (maxLoopCount > kMaxIterationRing)
    return decline("iteration-ring-exceeded");
  const int32_t iterationRing =
      static_cast<int32_t>(std::max<int64_t>(1, maxLoopCount));
  st.iterations = 0;
  for (const auto &loop : plan.loops)
    st.iterations += loop.count;
  st.dynamicHighWater = 0;
  for (auto &s : plan.segments) {
    s.lastInTopBlock = s.kind != SegmentKind::Membar;
    for (size_t j = static_cast<size_t>(s.sequence) + 1; j < plan.segments.size();
         ++j)
      if (plan.segments[j].kind != SegmentKind::Membar &&
          plan.segments[j].templateCount > 0 &&
          plan.segments[j].topBlock == s.topBlock) {
        s.lastInTopBlock = false;
        break;
      }
  }

  // Expand the complete static plan exactly once, but with every top-level
  // loop at one iteration. This is O(static nodes), not O(dynamic instructions).
  std::vector<CanonicalNode> oneContext = streamContext;
  for (auto &node : oneContext) {
    auto *loopPtr =
        std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
    if (!loopPtr || !*loopPtr)
      continue;
    auto copy = std::make_shared<CanonicalLoop>(**loopPtr);
    copy->count = int64_t{1};
    copy->unroll = int64_t{1};
    node = CanonicalNode::makeLoop(std::move(*copy));
  }
  const auto prepareStart = std::chrono::steady_clock::now();
  PreparedProgram tpl;
  try {
    tpl = prepareProgramForStream(vf, oneContext, db, cfg, false, true);
  } catch (const std::exception &e) {
    return decline("template-prepare-failed: " + std::string(e.what()));
  }
  tpl.compilerOptimizationStats = licmStats;
  if (tpl.instructions.size() != static_cast<size_t>(plan.templateCursor))
    return decline("template-shape-mismatch");
  for (const auto &s : plan.segments) {
    if (s.kind == SegmentKind::Membar) {
      if (tpl.instructions[static_cast<size_t>(s.templateStart)].type != "membar")
        return decline("template-membar-mismatch");
    } else if (s.kind == SegmentKind::Instruction) {
      if (tpl.instructions[static_cast<size_t>(s.templateStart)].type != "inst")
        return decline("template-inst-mismatch");
    } else {
      for (int64_t k = 0; k < s.templateCount; ++k)
        if (tpl.instructions[static_cast<size_t>(s.templateStart + k)].type !=
            "inst")
          return decline("template-loop-mismatch");
    }
  }

  // Counters are derived from the verified post-LICM template; loop bodies
  // are multiplied by their canonical trip count.
  for (const auto &seg : plan.segments) {
    const int64_t multiplier =
        seg.kind == SegmentKind::Loop
            ? plan.loops[static_cast<size_t>(seg.loopIndex)].count
            : 1;
    for (int64_t off = 0; off < seg.templateCount; ++off) {
      const auto &inst =
          tpl.instructions[static_cast<size_t>(seg.templateStart + off)];
      if (isHorizontal(inst.op))
        st.horizontalReductions += multiplier;
      if (isPredicateSet(inst.op))
        st.predicateSets += multiplier;
      if (isVectorDup(inst.op)) {
        st.vectorDups += multiplier;
        if (seg.kind == SegmentKind::Loop)
          st.loopVectorDups += multiplier;
      }
      if (isVectorLoad(inst.op) && seg.kind == SegmentKind::Loop)
        st.loopLoads += multiplier;
    }
  }

  st.prepareSeconds = std::chrono::duration<double>(
                           std::chrono::steady_clock::now() - prepareStart)
                           .count();
  // Build the engine-facing metadata record without copying the static
  // template's instruction vector (which is large and would be cleared
  // immediately). Move only fields the engine owns; tpl keeps its instruction
  // templates for the provider.
  PreparedProgram stream;
  stream.values = std::move(tpl.values);
  stream.params = std::move(tpl.params);
  stream.compilerOptimizationStats = std::move(tpl.compilerOptimizationStats);
  stream.retainsDiagnostics = tpl.retainsDiagnostics;
  for (const auto &loop : plan.loops)
    stream.topBlockLoopBounds[static_cast<int>(loop.topBlock)] =
        std::vector<int64_t>{loop.count};
  stream.totalTopBlocks = std::max<int64_t>(1, plan.nextTopBlock);
  // Preserve the historical prepareProgram linearInstCount (static node count);
  // dynamic fetch count is reported separately in LoopStreamStats.
  stream.linearInstCount = tpl.linearInstCount;

  std::unordered_map<std::string, int64_t> loopIndex;
  for (size_t i = 0; i < plan.loops.size(); ++i)
    loopIndex.emplace(plan.loops[i].loopId, static_cast<int64_t>(i));

  int32_t maxOperandId = -1;
  for (const auto &inst : tpl.instructions) {
    if (inst.srcIdBase < 0 || inst.dstIdBase < 0)
      return decline("dense-operands-unavailable");
    for (size_t k = 0; k < inst.src.size(); ++k) {
      const int32_t id = tpl.operandIds[static_cast<size_t>(inst.srcIdBase) + k];
      if (id < 0 || static_cast<size_t>(inst.srcIdBase) + k >= tpl.operandIds.size())
        return decline("dense-operands-unavailable");
      maxOperandId = std::max(maxOperandId, id);
    }
    for (size_t k = 0; k < inst.dst.size(); ++k) {
      const int32_t id = tpl.operandIds[static_cast<size_t>(inst.dstIdBase) + k];
      if (id < 0 || static_cast<size_t>(inst.dstIdBase) + k >= tpl.operandIds.size())
        return decline("dense-operands-unavailable");
      maxOperandId = std::max(maxOperandId, id);
    }
  }
  if (maxOperandId < 0)
    return decline("dense-operands-unavailable");
  std::vector<int32_t> valueLoop(static_cast<size_t>(maxOperandId) + 1, -1);
  std::vector<int32_t> segmentOf(tpl.instructions.size(), -1);
  for (const auto &seg : plan.segments)
    for (int64_t off = 0; off < seg.templateCount; ++off)
      segmentOf[static_cast<size_t>(seg.templateStart + off)] =
          static_cast<int32_t>(seg.sequence);
  for (size_t i = 0; i < tpl.instructions.size(); ++i) {
    const int32_t segmentId = segmentOf[i];
    if (segmentId < 0)
      continue;
    const auto &seg = plan.segments[static_cast<size_t>(segmentId)];
    if (seg.kind != SegmentKind::Loop)
      continue;
    const auto &dstNames = tpl.instructions[i].dst;
    for (size_t k = 0; k < dstNames.size(); ++k) {
      const int32_t id = tpl.operandIds[
          static_cast<size_t>(tpl.instructions[i].dstIdBase) + k];
      if (id < 0)
        continue;
      if (static_cast<size_t>(id) >= tpl.operandOwner.size())
        return decline("dense-owner-unavailable");
      const int32_t ownerTop = tpl.operandOwner[static_cast<size_t>(id)];
      int32_t owner = -1;
      if (ownerTop >= 0) {
        for (size_t li = 0; li < plan.loops.size(); ++li)
          if (plan.loops[li].topBlock == ownerTop) {
            owner = static_cast<int32_t>(li);
            break;
          }
      }
      valueLoop[static_cast<size_t>(id)] = owner;
    }
  }
  std::vector<int32_t> operandSrc;
  std::vector<int32_t> operandDst;
  std::vector<uint32_t> operandSrcBegin(tpl.instructions.size() + 1, 0);
  std::vector<uint32_t> operandDstBegin(tpl.instructions.size() + 1, 0);
  int32_t maxSrc = 0;
  int32_t maxDst = 0;
  for (size_t i = 0; i < tpl.instructions.size(); ++i) {
    const auto &inst = tpl.instructions[i];
    operandSrcBegin[i] = static_cast<uint32_t>(operandSrc.size());
    operandDstBegin[i] = static_cast<uint32_t>(operandDst.size());
    for (size_t k = 0; k < inst.src.size(); ++k)
      operandSrc.push_back(tpl.operandIds[static_cast<size_t>(inst.srcIdBase) + k]);
    for (size_t k = 0; k < inst.dst.size(); ++k)
      operandDst.push_back(tpl.operandIds[static_cast<size_t>(inst.dstIdBase) + k]);
    maxSrc = std::max<int32_t>(
        maxSrc, static_cast<int32_t>(operandSrc.size() - operandSrcBegin[i]));
    maxDst = std::max<int32_t>(
        maxDst, static_cast<int32_t>(operandDst.size() - operandDstBegin[i]));
  }
  operandSrcBegin[tpl.instructions.size()] =
      static_cast<uint32_t>(operandSrc.size());
  operandDstBegin[tpl.instructions.size()] =
      static_cast<uint32_t>(operandDst.size());
  constexpr int32_t kStreamMaxSlots = 64;
  const int32_t operandBlockSize = maxSrc + maxDst;
  if (operandBlockSize > 0) {
    stream.operandIds.assign(
        static_cast<size_t>(kStreamMaxSlots) * static_cast<size_t>(operandBlockSize),
        0);
  }
  bool namesRemapSafe = !cfg.enforceSameCycleSrcHazard;
  for (const auto &inst : tpl.instructions) {
    if (inst.srcStorage.size() != inst.src.size() ||
        inst.dstStorage.size() != inst.dst.size()) {
      namesRemapSafe = false;
      break;
    }
  }

  uint64_t digest = 1469598103934665603ull;
  uint64_t structureDigest = 1469598103934665603ull;
  uint64_t lifetimeDigest = 1469598103934665603ull;
  uint64_t addressDigest = 1469598103934665603ull;
  int64_t fetched = 0;
  int64_t highWater = 0;
  int64_t numericPeakId = 0;
  double fetchSeconds = 0;
  int64_t lookupOps = 0;
  st.numericSlots = static_cast<int64_t>(valueLoop.size());
  st.numericRing = iterationRing;
  st.numericTableBytes = static_cast<int64_t>(stream.operandIds.size() * sizeof(int32_t));
  StreamState state{tpl, plan.segments, operandSrc, operandDst, operandSrcBegin,
                    operandDstBegin, valueLoop, stream.operandIds,
                    operandBlockSize, iterationRing, kStreamMaxSlots, plan.loops,
                    loopIndex, digest, structureDigest, lifetimeDigest,
                    addressDigest, collectDigest, collectTiming, fetchSeconds,
                    lookupOps, fetched, highWater, numericPeakId};
  state.targetDynamic = plan.totalDynamic;
  state.namesRemapSafe = namesRemapSafe;
  Engine engine(stream, cfg, db);
  engine.useInstructionStream([&state] { return state.done(); },
                              [&state](DynamicInst &out) { state.nextInto(out); },
                              operandBlockSize, kStreamMaxSlots);
  const auto runStart = std::chrono::steady_clock::now();
  SimulationResult result;
  try {
    result = engine.run({}, maxCycles);
  } catch (const std::exception &e) {
    return decline("stream-run-failed: " + std::string(e.what()));
  }
  if (fetched != plan.totalDynamic)
    return decline("stream-fetch-count-mismatch fetched=" +
                   std::to_string(fetched) + "/" +
                   std::to_string(plan.totalDynamic));
  st.numericPeakId = numericPeakId;
  st.numericIdBytes = numericPeakId * static_cast<int64_t>(sizeof(void *));
  st.runSeconds = std::chrono::duration<double>(
                      std::chrono::steady_clock::now() - runStart)
                      .count();
  st.accepted = true;
  st.expandedCounterValid = true;
  st.fetchedInstructions = fetched;
  st.fetchCount = fetched;
  st.fetchSeconds = fetchSeconds;
  st.engineSeconds =
      std::max<double>(0.0, st.runSeconds - st.fetchSeconds);
  st.windowHighWater = highWater;
  st.arenaHighWater = engine.streamArenaHighWater();
  st.arenaBytesHighWater = st.arenaHighWater * static_cast<int64_t>(sizeof(DynamicInst));
  st.valueLookupOps = lookupOps;
  st.maxOutstanding = st.arenaHighWater;
  st.liveValueHighWater = engine.streamLiveValueHighWater();
  st.liveValueCount = st.liveValueHighWater;
  st.peakLive = std::max<int64_t>(st.windowHighWater, st.arenaHighWater);
  st.issuedInstructions = engine.streamIssuedInstructions();
  st.dynamicHighWater = highWater;
  st.instructionDigest = digest;
  st.structureDigest = structureDigest;
  st.lifetimeDigest = lifetimeDigest;
  st.addressDigest = addressDigest;
  return result;
}

} // namespace vfsim