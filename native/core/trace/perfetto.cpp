// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "native/core/trace/perfetto.h"
#include "native/core/trace/recorder.h"
#include <algorithm>
#include <fstream>
#include <map>
namespace vfsim {
void dumpPerfettoTrace(const TraceRecorder &trace,
                       const std::filesystem::path &path, int64_t issuePorts) {
  using Object = json::Value::Object;
  using Array = json::Value::Array;
  Array events;
  auto metadata = [&](const char *name, int pid, int tid, Object args) {
    events.emplace_back(Object{{"name", name},
                               {"ph", "M"},
                               {"pid", pid},
                               {"tid", tid},
                               {"args", std::move(args)}});
  };
  const std::vector<std::string> classes{"LOAD", "COMPUTE", "STORE"};
  for (int index = 0; index < 3; ++index) {
    const int pid = (index + 1) * 100;
    const std::string process = index == 0   ? "Load Unit"
                                : index == 1 ? "EXU Unit"
                                             : "Store Unit";
    metadata("process_name", pid, 0, {{"name", process}});
    metadata("process_sort_index", pid, 0, {{"sort_index", (index + 1) * 10}});
    if (index != 1) {
      metadata("thread_name", pid, 0,
               {{"name", index == 0 ? "Load Pipeline" : "Store Pipeline"}});
      metadata("thread_sort_index", pid, 0, {{"sort_index", 0}});
    }
  }
  for (int port = 0; port < std::max<int64_t>(1, issuePorts); ++port) {
    metadata("thread_name", 200, port,
             {{"name", "EXU" + std::to_string(port)}});
    metadata("thread_sort_index", 200, port, {{"sort_index", port}});
  }
  std::map<int64_t, int64_t> done;
  for (const auto &event : trace.history())
    if (event.event == "done")
      done[event.instruction.instId] =
          event.instruction.doneCycle.value_or(event.cycle);
  struct Slice {
    int64_t cycle, pid, tid, id;
    json::Value value;
  };
  std::vector<Slice> slices;
  for (const auto &event : trace.history()) {
    const auto &u = event.instruction;
    if (event.event != "start" || !done.count(u.instId))
      continue;
    const auto cls = u.profile->opClass;
    const int pid = cls == "LOAD" ? 100 : cls == "STORE" ? 300 : 200;
    const int tid = cls == "COMPUTE" && u.exuPort >= 0 ? u.exuPort : 0;
    const std::string track = cls == "LOAD"    ? "Load Pipeline"
                              : cls == "STORE" ? "Store Pipeline"
                                               : "EXU" + std::to_string(tid);
    auto identity = instructionTraceIdentity(u);
    Object args{{"inst_id", u.instId},
                {"static_instruction_id", identity.at("static_instruction_id")},
                {"stream_seq", u.streamSeq},
                {"start_cycle", event.cycle},
                {"done_cycle", done.at(u.instId)},
                {"ready_cycle", u.readyCycle},
                {"op_class", cls},
                {"fu_type", cls == "COMPUTE" ? json::Value(u.profile->fuType)
                                             : json::Value{}},
                {"track", track},
                {"src", identity.at("src")},
                {"dst", identity.at("dst")},
                {"iteration_path", identity.at("iteration_path")}};
    Array sources, destinations;
    for (const auto &p : u.pregSrc)
      sources.emplace_back(p ? json::Value(*p) : json::Value{});
    for (const auto &p : u.pregDst)
      destinations.emplace_back(p);
    args["preg_src"] = sources;
    args["preg_dst"] = destinations;
    std::string category = cls;
    std::transform(category.begin(), category.end(), category.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    Object slice{{"name", u.form.empty() ? u.op : u.op + "." + u.form},
                 {"cat", category},
                 {"ph", "X"},
                 {"ts", event.cycle},
                 {"dur", std::max<int64_t>(1, done.at(u.instId) - event.cycle)},
                 {"pid", pid},
                 {"tid", tid},
                 {"args", std::move(args)}};
    slices.push_back({event.cycle, pid, tid, u.instId, slice});
  }
  std::sort(slices.begin(), slices.end(), [](const auto &a, const auto &b) {
    return std::tie(a.cycle, a.pid, a.tid, a.id) <
           std::tie(b.cycle, b.pid, b.tid, b.id);
  });
  for (const auto &slice : slices)
    events.push_back(slice.value);
  std::ofstream output(path);
  if (!output)
    throw std::runtime_error("Cannot write Perfetto trace: " + path.string());
  output << json::stringify(Object{
      {"traceEvents", events},
      {"displayTimeUnit", "us"},
      {"metadata",
       Object{{"simulator", "VfSim"},
              {"timestamp_mapping", "1 simulator cycle = 1 microsecond"}}}});
}
} // namespace vfsim
