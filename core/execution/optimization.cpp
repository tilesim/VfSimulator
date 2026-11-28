#include "core/execution/optimization.h"
#include "api/common/instruction_catalog.h"
#include <functional>
#include <set>
#include <type_traits>

namespace vfsim {
namespace {
using Ids = std::set<std::string>;
void descendants(const std::vector<CanonicalNode> &nodes, Ids &ids,
                 Ids &scalarWrites, const CanonicalVfInfo &vf) {
  for (const auto &node : nodes) {
    if (const auto *loop = std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload)) {
      ids.insert((*loop)->loopId);
      descendants((*loop)->body, ids, scalarWrites, vf);
    } else if (const auto *instruction = std::get_if<CanonicalInstruction>(&node.payload)) {
      ids.insert(instruction->instructionId);
      for (const auto &output : instruction->outputs) {
        const auto &value = vf.values.at(output.valueId);
        if (value.storage == CanonicalStorageKind::Scalar) scalarWrites.insert(value.logicalId);
      }
    } else ids.insert(std::get<CanonicalMembar>(node.payload).instructionId);
  }
}
bool positiveCount(const CanonicalIntegerExpression &expression, const CanonicalVfInfo &vf) {
  if (auto count = std::get_if<int64_t>(&expression)) return *count > 0;
  const auto &name = std::get<std::string>(expression);
  if (auto found = vf.params.find(name); found != vf.params.end()) return found->second > 0;
  try {
    size_t used = 0;
    auto count = std::stoll(name, &used);
    return used == name.size() && count > 0;
  } catch (const std::exception &) { return false; }
}
} // namespace

std::optional<std::vector<CanonicalNode>>
hoistLoopInvariantContext(const CanonicalVfInfo &vf,
                          const std::vector<CanonicalNode> &rootContext,
                          CompilerOptimizationStats &stats) {
  stats = CompilerOptimizationStats{};
  if (auto flag = vf.source.find("compiler_optimized"); flag != vf.source.end() &&
      std::get_if<bool>(&flag->second) && std::get<bool>(flag->second)) {
    stats.enabled = false;
    return std::nullopt;
  }
  // [L2] Lazy rewrite: the analysis always runs on borrowed nodes and a level is
  // materialised only when it (or a child) actually changed, so a candidate with no
  // loop-invariant move copies zero context nodes.
  using ContextOpt = std::optional<std::vector<CanonicalNode>>;
  std::function<ContextOpt(const std::vector<CanonicalNode> &, std::optional<std::string>)> visit;
  visit = [&](const auto &nodes, std::optional<std::string> parent) -> ContextOpt {
    ContextOpt output;
    const auto ensurePrefix = [&](std::size_t upto) {
      if (output) return;
      output.emplace();
      output->reserve(nodes.size());
      for (std::size_t i = 0; i < upto; ++i) output->push_back(nodes[i]);
    };
    for (std::size_t index = 0; index < nodes.size(); ++index) {
      const auto &node = nodes[index];
      const auto *original = std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (!original || !positiveCount((*original)->count, vf)) {
        if (output) output->push_back(node);
        continue;
      }
      const ContextOpt child = visit((*original)->body, (*original)->loopId);
      const std::vector<CanonicalNode> &body = child ? *child : (*original)->body;
      Ids inside{(*original)->loopId}, scalarWrites, entries, moved;
      descendants(body, inside, scalarWrites, vf);
      for (const auto &binding : (*original)->carriedValues)
        if (binding.entryValueId && *binding.entryValueId != binding.backEdgeValueId) entries.insert(*binding.entryValueId);
      std::function<bool(const std::string &, Ids)> invariant = [&](const std::string &id, Ids visiting) {
        if (entries.count(id) || !visiting.insert(id).second) return false;
        const auto &value = vf.values.at(id);
        if (value.storage == CanonicalStorageKind::UB) return false;
        if (value.producerNodeId && inside.count(*value.producerNodeId)) return moved.count(*value.producerNodeId) != 0;
        if (value.storage == CanonicalStorageKind::Scalar) {
          for (const auto &dependency : value.scalarLoopDependencies) if (inside.count(dependency)) return false;
          if (!value.scalarExpression && scalarWrites.count(value.logicalId)) return false;
          for (const auto &dependency : value.scalarDependencies) if (!invariant(dependency, visiting)) return false;
        }
        return true;
      };
      // Fixed point over indices of the stable body: identification is unchanged,
      // only the moved positions are recorded instead of copied out.
      std::vector<char> hoisted(body.size(), 0);
      std::vector<std::size_t> remaining(body.size()), order;
      for (std::size_t k = 0; k < body.size(); ++k) remaining[k] = k;
      while (true) {
        std::vector<std::size_t> retained;
        retained.reserve(remaining.size());
        bool changed = false;
        for (const std::size_t candidate : remaining) {
          const auto &candidateNode = body[candidate];
          const auto *instruction = std::get_if<CanonicalInstruction>(&candidateNode.payload);
          auto spec = instruction ? defaultInstructionCatalog().lookup(instruction->opcode) : nullptr;
          bool pure = spec && spec->pure && instruction->instructionClass == CanonicalInstructionClass::Compute && !instruction->outputs.empty();
          if (pure) {
            auto flag = instruction->attributes.find("volatile");
            if (flag != instruction->attributes.end()) {
              // Presence with a truthy JSON scalar blocks motion, as in Python.
              const auto &v = flag->second;
              pure = std::visit([](const auto &x) { using T = std::decay_t<decltype(x)>;
                if constexpr (std::is_same_v<T, std::monostate>) return true;
                else if constexpr (std::is_same_v<T, std::string>) return x.empty();
                else return !bool(x);
              }, v);
            }
            for (const auto &operand : instruction->inputs)
              pure = pure && !operand.memoryAccess && invariant(operand.valueId, {});
            for (const auto &operand : instruction->outputs)
              pure = pure && !operand.memoryAccess && vf.values.at(operand.valueId).storage != CanonicalStorageKind::UB;
          }
          if (pure) {
            moved.insert(instruction->instructionId);
            hoisted[candidate] = 1;
            order.push_back(candidate);
            stats.moves.push_back({instruction->instructionId, instruction->opcode, (*original)->loopId, parent});
            changed = true;
          } else retained.push_back(candidate);
        }
        remaining.swap(retained);
        if (!changed) break;
      }
      if (order.empty() && !child) {
        if (output) output->push_back(node);
        continue;
      }
      ensurePrefix(index);
      for (const std::size_t candidate : order) output->push_back(body[candidate]);
      std::vector<CanonicalNode> newBody;
      newBody.reserve(body.size() - order.size());
      for (std::size_t k = 0; k < body.size(); ++k)
        if (!hoisted[k]) newBody.push_back(body[k]);
      CanonicalLoop loop = **original;
      loop.body = std::move(newBody);
      output->push_back(CanonicalNode::makeLoop(std::move(loop)));
    }
    return output;
  };
  ContextOpt context = visit(rootContext, std::nullopt);
  Ids unique;
  for (const auto &move : stats.moves) unique.insert(move.instructionId);
  stats.hoistedInstructionCount = static_cast<int64_t>(unique.size());
  // No level changed => the caller keeps its own context; nothing was copied.
  if (!context)
    return std::nullopt;
  return context;
}

std::optional<std::vector<CanonicalNode>>
hoistLoopInvariantContext(const CanonicalVfInfo &vf,
                          CompilerOptimizationStats &stats) {
  return hoistLoopInvariantContext(vf, vf.context, stats);
}

LoopInvariantResult hoistLoopInvariants(const CanonicalVfInfo &vf) {
  LoopInvariantResult result{vf, {}};
  if (auto context = hoistLoopInvariantContext(vf, result.stats))
    result.vfInfo.context = std::move(*context);
  return result;
}
} // namespace vfsim
