#pragma once
#include <cstdint>
#include <filesystem>
namespace vfsim {
class TraceRecorder;
void dumpPerfettoTrace(const TraceRecorder &, const std::filesystem::path &,
                       int64_t issuePorts);
} // namespace vfsim
