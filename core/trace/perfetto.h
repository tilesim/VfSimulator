#pragma once
#include <filesystem>
namespace vfsim {
class TraceRecorder;
void dumpPerfettoTrace(const TraceRecorder &, const std::filesystem::path &);
} // namespace vfsim
