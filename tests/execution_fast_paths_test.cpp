// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/trace/recorder.h"
#include "tests/support.h"
#include <stdexcept>

using namespace vfsim;
namespace {
void require(bool condition, const char *message) {
  if (!condition)
    throw std::runtime_error(message);
}
} // namespace

int main() {
  ParamDB db(VFSIM_SOURCE_ROOT);
  auto vf = InputAPI::loadJson(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                               "VFtest/canonical/fixtures/v1_valid_loop.json");
  const auto out = std::filesystem::temp_directory_path() /
                   "vfsim_execution_fast_paths_test";
  const auto traced = runProgram(vf, db, out);
  const auto headless = runProgram(vf, db);
  require(traced.cyclesExecuted == headless.cyclesExecuted &&
              traced.vfEndCycle == headless.vfEndCycle,
          "Headless execution must preserve every simulated cycle");
  require(std::filesystem::file_size(out / "sim_history.json") > 2,
          "Explicit trace export must still record instructions");
  for (const auto &output : {std::filesystem::path{}, out / "limited"}) {
    bool rejected = false;
    try {
      runProgram(vf, db, output, 1);
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    require(rejected, "Headless execution must enforce the cycle budget");
  }

  Registers regs(db.uarch(), db, {});
  TraceRecorder trace(regs);
  Uop u;
  trace.log("start", u, 0);
  require(trace.history().size() == 1, "Direct recorders trace by default");
  trace.setEnabled(false);
  trace.log("start", u, 1);
  require(trace.history().size() == 1, "Disabled traces must not copy uops");
  trace.setEnabled(true);
  trace.log("start", u, 2);
  require(trace.history().size() == 2 && trace.history().back().cycle == 2,
          "Direct recorder clients retain tracing by default");

  // Pending producer starts are not cached. Once all source times are known,
  // repeated queue polling may reuse them while retaining fresh queue delays.
  std::unordered_map<std::string, ValueInfo> values{
      {"a", {"a", CanonicalStorageKind::Register, "fp32", {64}}},
      {"b", {"b", CanonicalStorageKind::Register, "fp32", {64}}},
      {"c", {"c", CanonicalStorageKind::Register, "fp32", {64}}}};
  Registers dependencies(db.uarch(), db, values);
  DynamicInst producer;
  producer.op = "VLDS";
  producer.form = "fp32";
  producer.opClass = "LOAD";
  producer.dst = {"a"};
  auto produced = dependencies.rename(producer, 0);
  producer.dst = {"b"};
  auto second = dependencies.rename(producer, 0);
  DynamicInst consumer;
  consumer.op = "VADD";
  consumer.form = "b32";
  consumer.src = {"a", "b"};
  consumer.dst = {"c"};
  auto consumed = dependencies.rename(consumer, 0);
  require(dependencies.readyCycle(*consumed) == 1000000000 &&
              consumed->resolvedSourceCount == 0,
          "Unstarted sources must remain unresolved");
  dependencies.onStart(*produced, 100);
  require(dependencies.readyCycle(*consumed) == 1000000000 &&
              consumed->resolvedSourceCount == 1,
          "A resolved prefix must not hide a later pending producer");
  dependencies.onStart(*second, 110);
  const auto ready = dependencies.readyCycle(*consumed);
  require(ready >= 100 &&
              consumed->resolvedSourceCount == consumed->pregSrc.size() &&
              dependencies.readyCycle(*consumed) == ready,
          "Resolved source readiness must remain stable");
  consumed->shqReadyCycle = ready + 20;
  require(dependencies.readyCycle(*consumed) == ready + 20,
          "A cached source time must not hide a later queue delay");

  // A store updates all static aliases of the same UB object, including
  // aliases read in this iteration and on the next loop iteration.
  auto loop = *std::get<std::shared_ptr<const CanonicalLoop>>(
      vf.context.front().payload);
  const auto load = std::get<CanonicalInstruction>(loop.body.front().payload);
  auto alias = vf.values.at("input.0");
  alias.definitionId = alias.logicalId = "alias.0";
  vf.values.emplace(alias.definitionId, alias);
  auto stored = alias;
  stored.definitionId = stored.logicalId = "written.0";
  stored.producerNodeId = "inst.store";
  vf.values.emplace(stored.definitionId, stored);
  auto again = load;
  again.instructionId = "inst.again";
  again.inputs.front().valueId = alias.definitionId;
  again.outputs.front().valueId = "again.0";
  auto result = vf.values.at("acc.0");
  result.definitionId = result.logicalId = "again.0";
  result.producerNodeId = again.instructionId;
  vf.values.emplace(result.definitionId, result);
  CanonicalInstruction store;
  store.instructionId = "inst.store";
  store.opcode = "VSTS";
  store.form = "fp32";
  store.instructionClass = CanonicalInstructionClass::Store;
  store.inputs = {{"acc.0", CanonicalOperandRole::Source, {}, {}}};
  auto memory = load.inputs.front();
  memory.valueId = stored.definitionId;
  memory.memoryAccess->accessKind = CanonicalAccessKind::Write;
  store.outputs = {memory};
  loop.body.push_back(CanonicalNode::makeInstruction(store));
  loop.body.push_back(CanonicalNode::makeInstruction(again));
  vf.context.front() = CanonicalNode::makeLoop(loop);
  vf = InputAPI::fromCanonical(std::move(vf));
  const auto program = prepareProgram(vf, db, db.uarch());
  size_t stores = 0;
  for (size_t i = 0; i < program.instructions.size(); i += 3) {
    const auto &before = program.instructions[i].srcValueInstances.front();
    const auto &written = program.instructions[i + 1].dstValueInstances.front();
    const auto &after = program.instructions[i + 2].srcValueInstances.front();
    require(before.definitionId == written.definitionId &&
                after.definitionId == written.definitionId,
            "All UB aliases must share storage identity");
    require(after.iterationPath.size() == 1 &&
                after.iterationPath.front().iteration ==
                    static_cast<int64_t>(stores),
            "An alias read after a store must observe that iteration");
    require(stores == 0 ? before.iterationPath.empty()
                        : before.iterationPath.front().iteration ==
                              static_cast<int64_t>(stores - 1),
            "An alias read before a store must observe the previous iteration");
    ++stores;
  }
  require(stores == 4, "All loop iterations must retain alias updates");
  std::filesystem::remove_all(out);
}
