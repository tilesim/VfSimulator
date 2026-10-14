// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/trace/writer.h"
#include "native/execution/membar.h"
#include "native/trace/recorder.h"
#include "native/uarch/idu.h"
#include "native/uarch/registers.h"
#include <algorithm>
#include <fstream>
#include <sstream>
#include <stdexcept>
namespace vfsim {
namespace {
std::string jsonEscape(const std::string &text) {
  std::string out;
  out.reserve(text.size() + 8);
  for (char c : text) {
    switch (c) {
    case '\\':
      out += "\\\\";
      break;
    case '"':
      out += "\\\"";
      break;
    case '\n':
      out += "\\n";
      break;
    case '\r':
      out += "\\r";
      break;
    case '\t':
      out += "\\t";
      break;
    default:
      if (static_cast<unsigned char>(c) < 0x20) {
        static constexpr char digits[] = "0123456789abcdef";
        out += "\\u00";
        out.push_back(digits[(static_cast<unsigned char>(c) >> 4) & 15]);
        out.push_back(digits[static_cast<unsigned char>(c) & 15]);
      } else
        out.push_back(c);
      break;
    }
  }
  return out;
}

template <typename T> std::string joinJsonArray(const std::vector<T> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    oss << values[i];
  }
  oss << "]";
  return oss.str();
}

template <>
std::string joinJsonArray<std::string>(const std::vector<std::string> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    oss << '"' << jsonEscape(values[i]) << '"';
  }
  oss << "]";
  return oss.str();
}

template <>
std::string joinJsonArray<std::optional<std::string>>(
    const std::vector<std::optional<std::string>> &values) {
  std::ostringstream oss;
  oss << "[";
  for (size_t i = 0; i < values.size(); ++i) {
    if (i)
      oss << ", ";
    if (values[i].has_value())
      oss << '"' << jsonEscape(*values[i]) << '"';
    else
      oss << "null";
  }
  oss << "]";
  return oss.str();
}

std::string
joinIterationPath(const std::vector<std::pair<std::string, int64_t>> &path) {
  std::ostringstream oss;
  oss << "[";
  for (size_t index = 0; index < path.size(); ++index) {
    if (index)
      oss << ", ";
    oss << "{\"loop_id\":\"" << jsonEscape(path[index].first)
        << "\",\"iteration\":" << path[index].second << "}";
  }
  oss << "]";
  return oss.str();
}

void dumpIDULog(const std::vector<IDUDispatchRecord> &records,
                const std::string &path) {
  std::ofstream os(path);
  for (const auto &r : records) {
    os << "{\"cy\":" << r.cycle << ",\"inst_id\":" << r.instId << ",\"op\":\""
       << jsonEscape(r.op) << "\""
       << ",\"dst\":" << joinJsonArray(r.dst)
       << ",\"src\":" << joinJsonArray(r.src)
       << ",\"top_block_id\":" << r.topBlockId << ",\"vreg\":" << r.vreg
       << ",\"predicate_phys_free\":" << r.predicatePhysFree
       << ",\"SHQ_QUEUE\":" << r.shqQueue << ",\"LSQ\":" << r.lsq
       << ",\"SHQ\":" << r.shq << ",\"static_instruction_id\":\""
       << jsonEscape(r.staticInstructionId) << "\""
       << ",\"iteration_path\":" << joinIterationPath(r.iterationPath)
       << ",\"stream_seq\":" << r.streamSeq << ",\"event\":\"" << r.event
       << "\"";
    if (r.event == "blocked")
      os << ",\"blocked_reason\":\"" << r.blockedReason << "\"";
    os << ",\"address_dependencies\":[";
    for (size_t i = 0; i < r.addressDependencies.size(); ++i) {
      const auto &d = r.addressDependencies[i];
      if (i)
        os << ",";
      os << "{\"address_state_id\":\"" << jsonEscape(d.stateId)
         << "\",\"producer_inst_id\":" << d.producerInstId
         << ",\"producer_dispatch_cycle\":" << d.producerDispatchCycle
         << ",\"ready_cycle\":" << d.readyCycle << "}";
    }
    os << "]}\n";
  }
}

void dumpVloopTrace(const LoopControl &loops, const std::string &path) {
  std::ofstream os(path);
  for (const auto &r : loops.trace()) {
    os << "{\"top_block_id\":" << r.topBlockId << ",\"loop_id\":\""
       << jsonEscape(r.loopId) << "\""
       << ",\"iter\":" << joinJsonArray(r.iter)
       << ",\"start_cycle\":" << r.startCycle << "}\n";
  }
}

void dumpModelWarnings(const ParamDB &db, const std::string &path) {
  const auto warnings = db.warnings();
  if (warnings.empty())
    return;
  std::ofstream os(path);
  os << "{\n"
     << "  \"has_warning\": true,\n"
     << "  \"instruction_fallback_warnings\": [\n";
  for (size_t i = 0; i < warnings.size(); ++i) {
    const auto &warning = warnings[i];
    os << "    {\"kind\":\"" << jsonEscape(warning.kind) << "\"";
    for (const auto &[key, value] : warning.fields)
      os << ",\"" << jsonEscape(key) << "\":\"" << jsonEscape(value) << "\"";
    os << ",\"count\":" << warning.count << "}";
    if (i + 1 < warnings.size())
      os << ",";
    os << "\n";
  }
  os << "  ]\n"
     << "}\n";
}

void writeIdentity(std::ostream &out, const Uop &u) {
  out << "\"op\":\"" << jsonEscape(u.op) << "\",\"form\":\""
      << jsonEscape(u.form) << "\",\"op_class\":\"" << u.opClass
      << "\",\"src\":" << joinJsonArray(u.src)
      << ",\"dst\":" << joinJsonArray(u.dst) << ",\"static_instruction_id\":\""
      << jsonEscape(u.staticInstructionId)
      << "\",\"iteration_path\":" << joinIterationPath(u.iterationPath)
      << ",\"stream_seq\":" << u.streamSeq;
}
template <typename T>
std::string optionalNumber(const std::optional<T> &value) {
  return value ? std::to_string(*value) : "null";
}
std::string optionalText(const std::optional<std::string> &value) {
  return value ? "\"" + jsonEscape(*value) + "\"" : "null";
}
} // namespace
void dumpLogs(const std::filesystem::path &root, const TraceRecorder &trace,
              const IDU &idu, const LoopControl &loops,
              const MembarController &control, const ParamDB &db) {
  std::filesystem::create_directories(root);
  std::ofstream history(root / "sim_history.json"),
      start(root / "start_by_cycle.json"), done(root / "done_by_cycle.json"),
      perfetto(root / "trace.json");
  if (!history || !start || !done || !perfetto)
    throw std::runtime_error("Cannot write simulator trace: " + root.string());
  history << "[\n";
  perfetto << "{\"traceEvents\":[";
  bool first = true, firstPerfetto = true;
  for (const auto &row : trace.history()) {
    const auto &u = row.instruction;
    if (!first)
      history << ",\n";
    first = false;
    history << "{\"cy\":" << row.cycle << ",\"event\":\"" << row.event
            << "\",\"id\":" << u.instId << ",";
    writeIdentity(history, u);
    history << ",\"state\":\"" << u.state
            << "\",\"blocked_reason\":" << optionalText(u.blockedReason)
            << ",\"ready\":" << u.readyCycle
            << ",\"start\":" << optionalNumber(u.startCycle)
            << ",\"done\":" << optionalNumber(u.doneCycle)
            << ",\"preg_src\":" << joinJsonArray(u.pregSrc)
            << ",\"preg_dst\":" << joinJsonArray(u.pregDst)
            << ",\"vector_phys_free\":" << row.vectorFree
            << ",\"predicate_phys_free\":" << row.predicateFree
            << ",\"src_predicate_phys\":" << joinJsonArray(row.sourcePredicates)
            << ",\"dst_predicate_phys\":"
            << joinJsonArray(row.destinationPredicates)
            << ",\"producer_op_for_store\":"
            << optionalText(u.producerOpForStore)
            << ",\"producer_form_for_store\":"
            << optionalText(u.producerFormForStore)
            << ",\"producer_start_for_store\":"
            << optionalNumber(u.producerStartForStore)
            << ",\"store_dependencies_resolved\":"
            << (u.storeDependenciesResolved ? "true" : "false")
            << ",\"align_state_operation\":\"" << u.alignStateOperation
            << "\",\"align_state_id\":\"" << jsonEscape(u.alignStateId)
            << "\",\"align_generation\":"
            << (u.alignGeneration
                    ? std::to_string(u.alignGeneration->generationId)
                    : "null")
            << "}";
    if (row.event == "start" || row.event == "done") {
      auto &out = row.event == "start" ? start : done;
      out << "{\"cy\":"
          << (row.event == "done" ? u.doneCycle.value_or(row.cycle) : row.cycle)
          << ",\"inst_id\":" << u.instId << ",";
      writeIdentity(out, u);
      if (row.event == "start")
        out << ",\"exu_port\":"
            << (u.exuPort >= 0 ? std::to_string(u.exuPort) : "null")
            << ",\"ready_cycle\":" << u.readyCycle
            << ",\"preg_src\":" << joinJsonArray(u.pregSrc)
            << ",\"preg_dst\":" << joinJsonArray(u.pregDst);
      out << "}\n";
    }
    if (row.event == "start") {
      if (!firstPerfetto)
        perfetto << ",";
      firstPerfetto = false;
      const int pid = u.opClass == "LOAD"      ? 100
                      : u.opClass == "COMPUTE" ? 200
                                               : 300;
      const int tid = u.exuPort >= 0 ? u.exuPort : 0;
      perfetto << "{\"name\":\"" << jsonEscape(u.op + "." + u.form)
               << "\",\"cat\":\"" << u.opClass
               << "\",\"ph\":\"X\",\"ts\":" << row.cycle
               << ",\"dur\":" << std::max<int64_t>(1, u.latency)
               << ",\"pid\":" << pid << ",\"tid\":" << tid
               << ",\"args\":{\"inst_id\":" << u.instId
               << ",\"stream_seq\":" << u.streamSeq << "}}";
    }
  }
  history << "\n]\n";
  perfetto << "]}\n";
  dumpIDULog(idu.dispatchLog(), (root / "idu_to_ooo.json").string());
  dumpIDULog(idu.addressBlockLog(),
             (root / "idu_address_blocked.json").string());
  dumpIDULog(idu.resourceBlockLog(),
             (root / "idu_resource_blocked.json").string());
  dumpVloopTrace(loops, (root / "vloop_trace.json").string());
  dumpModelWarnings(db, (root / "model_warnings.json").string());
  if (control.hasTiming()) {
    std::ofstream out(root / "membar_history.json");
    if (!out)
      throw std::runtime_error("Cannot write membar history");
    out << "[\n";
    bool first = true;
    for (const auto &e : control.history()) {
      if (!first)
        out << ",\n";
      first = false;
      out << "{\"cy\":" << e.cy << ",\"stream_seq\":" << e.streamSeq
          << ",\"pc\":" << e.pc << ",\"event\":\"" << jsonEscape(e.event)
          << "\",\"barrier\":\"" << jsonEscape(e.barrier) << "\"}";
    }
    out << "\n]\n";
  }
}
} // namespace vfsim
