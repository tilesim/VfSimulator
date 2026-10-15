// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "core/trace/writer.h"
#include "core/execution/membar.h"
#include "core/trace/perfetto.h"
#include "core/trace/recorder.h"
#include "core/uarch/idu.h"
#include "core/uarch/registers.h"
#include <algorithm>
#include <fstream>
namespace vfsim {
namespace {
using Object = json::Value::Object;
using Array = json::Value::Array;
template <class T> json::Value optional(const std::optional<T> &value) {
  return value ? json::Value(*value) : json::Value{};
}
template <class T> Array array(const std::vector<T> &values) {
  Array result;
  for (const auto &value : values)
    result.emplace_back(value);
  return result;
}
template <class T> Array array(const std::vector<std::optional<T>> &values) {
  Array result;
  for (const auto &value : values)
    result.emplace_back(optional(value));
  return result;
}
void writeJson(const std::filesystem::path &path, const json::Value &value) {
  std::ofstream output(path);
  if (!output)
    throw std::runtime_error("Cannot write simulator trace: " + path.string());
  output << json::stringify(value) << '\n';
}
void dumpIDULog(const std::vector<IDUDispatchRecord> &records,
                const std::filesystem::path &path,
                const std::map<int64_t, const Uop *> &instructions) {
  std::ofstream output(path);
  for (const auto &r : records) {
    Object row{{"cy", r.cycle},
               {"inst_id", r.instId},
               {"op", r.op},
               {"static_instruction_id", r.staticInstructionId},
               {"iteration_path", iterationPathJson(r.iterationPath)},
               {"stream_seq", r.streamSeq},
               {"event", r.event},
               {"predicate_phys_free", r.predicatePhysFree}};
    if (r.event == "dispatch") {
      const auto it = instructions.find(r.instId);
      Object identity = it == instructions.end()
                            ? Object{}
                            : instructionTraceIdentity(*it->second);
      row["dst"] = it == instructions.end() ? json::Value(array(r.dst))
                                            : identity.at("dst");
      row["src"] = it == instructions.end() ? json::Value(array(r.src))
                                            : identity.at("src");
      row["top_block_id"] = r.topBlockId;
      row["vreg"] = r.vreg;
      row["SHQ_QUEUE"] = r.shqQueue;
      row["LSQ"] = r.lsq;
      row["SHQ"] = r.shq;
    } else if (!r.blockedReason.empty() && r.blockedReason != "address_state") {
      row["blocked_reason"] = r.blockedReason;
      row["vreg"] = r.vreg;
    }
    Array dependencies;
    for (const auto &d : r.addressDependencies)
      dependencies.emplace_back(
          Object{{"address_state_id", d.stateId},
                 {"producer_inst_id", d.producerInstId},
                 {"producer_dispatch_cycle", d.producerDispatchCycle},
                 {"ready_cycle", d.readyCycle}});
    if (r.event == "dispatch" || r.blockedReason.empty() ||
        r.blockedReason == "address_state")
      row["address_dependencies"] = dependencies;
    if (r.event == "blocked" &&
        (r.blockedReason.empty() || r.blockedReason == "address_state")) {
      row["blocked_reason"] = "address_state";
      row.erase("predicate_phys_free");
    }
    output << json::stringify(row) << '\n';
  }
}
} // namespace
void dumpLogs(const std::filesystem::path &root, const TraceRecorder &trace,
              const IDU &idu, const LoopControl &loops,
              const MembarController &control, const ParamDB &db,
              int64_t issuePorts) {
  std::filesystem::create_directories(root);
  std::ofstream start(root / "start_by_cycle.json"),
      done(root / "done_by_cycle.json");
  if (!start || !done)
    throw std::runtime_error("Cannot write simulator trace: " + root.string());
  Array history;
  std::map<int64_t, const Uop *> instructions;
  for (const auto &event : trace.history()) {
    const auto &u = event.instruction;
    instructions[u.instId] = &u;
    auto row = instructionTraceIdentity(u);
    row["cy"] = event.cycle;
    row["event"] = event.event;
    row["id"] = u.instId;
    row["state"] = u.state;
    row["blocked_reason"] = optional(u.blockedReason);
    row["ready"] = u.readyCycle;
    row["start"] = optional(u.startCycle);
    row["done"] = optional(u.doneCycle);
    row["preg_src"] = array(u.pregSrc);
    row["preg_dst"] = array(u.pregDst);
    row["vector_phys_free"] = event.vectorFree;
    row["predicate_phys_free"] = event.predicateFree;
    row["src_predicate_phys"] = array(event.sourcePredicates);
    row["dst_predicate_phys"] = array(event.destinationPredicates);
    row["producer_op_for_store"] = optional(u.producerOpForStore);
    row["producer_form_for_store"] = optional(u.producerFormForStore);
    row["producer_start_for_store"] = optional(u.producerStartForStore);
    row["store_dependencies_resolved"] = u.storeDependenciesResolved;
    row["align_state_operation"] = u.alignStateOperation.empty()
                                       ? json::Value{}
                                       : json::Value(u.alignStateOperation);
    row["align_state_id"] =
        u.alignStateId.empty() ? json::Value{} : json::Value(u.alignStateId);
    row["align_generation"] = u.alignGeneration
                                  ? json::Value(u.alignGeneration->generationId)
                                  : json::Value{};
    history.emplace_back(std::move(row));
    if (event.event != "start" && event.event != "done")
      continue;
    row = instructionTraceIdentity(u);
    row["cy"] =
        event.event == "done" ? u.doneCycle.value_or(event.cycle) : event.cycle;
    row["inst_id"] = u.instId;
    row["op_class"] = u.profile->opClass;
    row["fu_type"] = u.profile->opClass == "COMPUTE"
                         ? json::Value(u.profile->fuType)
                         : json::Value{};
    if (event.event == "start") {
      row["exu_port"] = u.exuPort < 0 ? json::Value{} : json::Value(u.exuPort);
      row["ready_cycle"] = u.readyCycle;
      row["preg_src"] = array(u.pregSrc);
      row["preg_dst"] = array(u.pregDst);
    }
    (event.event == "start" ? start : done) << json::stringify(row) << '\n';
  }
  writeJson(root / "sim_history.json", history);
  dumpPerfettoTrace(trace, root / "trace.json", issuePorts);
  dumpIDULog(idu.dispatchLog(), root / "idu_to_ooo.json", instructions);
  dumpIDULog(idu.addressBlockLog(), root / "idu_address_blocked.json",
             instructions);
  dumpIDULog(idu.resourceBlockLog(), root / "idu_resource_blocked.json",
             instructions);
  auto loopEvents = loops.trace();
  std::sort(loopEvents.begin(), loopEvents.end(),
            [](const auto &a, const auto &b) {
              return std::tie(a.topBlockId, a.startCycle, a.loopId, a.iter) <
                     std::tie(b.topBlockId, b.startCycle, b.loopId, b.iter);
            });
  std::ofstream loopOutput(root / "vloop_trace.json");
  for (const auto &r : loopEvents)
    loopOutput << json::stringify(Object{{"top_block_id", r.topBlockId},
                                         {"loop_id", r.loopId},
                                         {"iter", array(r.iter)},
                                         {"start_cycle", r.startCycle}})
               << '\n';
  if (!db.warnings().empty()) {
    Array warnings;
    for (const auto &warning : db.warnings()) {
      Object payload(warning.fields.begin(), warning.fields.end());
      payload["kind"] = warning.kind;
      payload["count"] = warning.count;
      warnings.emplace_back(std::move(payload));
    }
    writeJson(root / "model_warnings.json",
              Object{{"has_warning", true},
                     {"instruction_fallback_warnings", warnings}});
  }
  if (control.hasTiming()) {
    Array barriers;
    for (const auto &e : control.history())
      barriers.emplace_back(Object{{"cy", e.cy},
                                   {"stream_seq", e.streamSeq},
                                   {"pc", e.pc},
                                   {"event", e.event},
                                   {"barrier", e.barrier}});
    writeJson(root / "membar_history.json", barriers);
  }
}
} // namespace vfsim
