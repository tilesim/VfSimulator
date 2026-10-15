// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/trace/perfetto.h"
#include "core/trace/recorder.h"
#include "api/json/value.h"
#include <algorithm>
#include <fstream>
#include <stdexcept>
namespace vfsim {
void dumpPerfettoTrace(const TraceRecorder &trace, const std::filesystem::path &path) {
  std::ofstream perfetto(path);
  if (!perfetto)
    throw std::runtime_error("Cannot write Perfetto trace: " + path.string());
  perfetto << "{\"traceEvents\":[";
  bool firstPerfetto = true;
  for (const auto &row : trace.history()) {
    const auto &u = row.instruction;
    if (row.event == "start") {
      if (!firstPerfetto)
        perfetto << ",";
      firstPerfetto = false;
      const int pid = u.profile->opClass == "LOAD"      ? 100
                      : u.profile->opClass == "COMPUTE" ? 200
                                               : 300;
      const int tid = u.exuPort >= 0 ? u.exuPort : 0;
      perfetto << "{\"name\":\"" << json::escapeString(u.op + "." + u.form)
               << "\",\"cat\":\"" << u.profile->opClass
               << "\",\"ph\":\"X\",\"ts\":" << row.cycle
               << ",\"dur\":" << std::max<int64_t>(1, u.profile->latency)
               << ",\"pid\":" << pid << ",\"tid\":" << tid
               << ",\"args\":{\"inst_id\":" << u.instId
               << ",\"stream_seq\":" << u.streamSeq << "}}";
    }
  }
  perfetto << "]}\n";
}
} // namespace vfsim
