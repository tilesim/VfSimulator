#include "native/core/execution/optimization.h"
#include "native/api/common/instruction_catalog.h"
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

LoopInvariantResult hoistLoopInvariants(const CanonicalVfInfo &vf) {
  LoopInvariantResult result{vf, {}};
  if (auto flag = vf.source.find("compiler_optimized"); flag != vf.source.end() &&
      std::get_if<bool>(&flag->second) && std::get<bool>(flag->second)) {
    result.stats.enabled = false;
    return result;
  }
  std::function<std::vector<CanonicalNode>(const std::vector<CanonicalNode> &, std::optional<std::string>)> visit;
  visit = [&](const auto &nodes, std::optional<std::string> parent) {
    std::vector<CanonicalNode> output;
    for (const auto &node : nodes) {
      const auto *original = std::get_if<std::shared_ptr<const CanonicalLoop>>(&node.payload);
      if (!original || !positiveCount((*original)->count, vf)) { output.push_back(node); continue; }
      auto loop = **original;
      loop.body = visit(loop.body, loop.loopId);
      Ids inside{loop.loopId}, scalarWrites, entries, moved;
      descendants(loop.body, inside, scalarWrites, vf);
      for (const auto &binding : loop.carriedValues)
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
      auto remaining = loop.body;
      while (true) {
        std::vector<CanonicalNode> retained;
        bool changed = false;
        for (const auto &candidate : remaining) {
          const auto *instruction = std::get_if<CanonicalInstruction>(&candidate.payload);
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
            output.push_back(candidate);
            result.stats.moves.push_back({instruction->instructionId, instruction->opcode, loop.loopId, parent});
            changed = true;
          } else retained.push_back(candidate);
        }
        remaining = std::move(retained);
        if (!changed) break;
      }
      loop.body = std::move(remaining);
      output.push_back(CanonicalNode::makeLoop(std::move(loop)));
    }
    return output;
  };
  result.vfInfo.context = visit(vf.context, std::nullopt);
  Ids unique;
  for (const auto &move : result.stats.moves) unique.insert(move.instructionId);
  result.stats.hoistedInstructionCount = static_cast<int64_t>(unique.size());
  return result;
}
} // namespace vfsim
