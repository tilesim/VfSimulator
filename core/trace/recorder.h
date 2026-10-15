#pragma once
#include "core/execution/instruction.h"
namespace vfsim {
class Registers;
json::Value iterationPathJson(const std::vector<IterationIdentity> &path);
json::Value valueInstancesJson(const std::vector<ValueInstance> &values);
json::Value::Object instructionTraceIdentity(const Uop &instruction);
struct TraceEvent {
  int64_t cycle;
  std::string event;
  Uop instruction;
  int64_t vectorFree, predicateFree;
  std::vector<std::string> sourcePredicates, destinationPredicates;
};
class TraceRecorder {
public:
  explicit TraceRecorder(const Registers &regs) : regs_(regs) {}
  void log(const std::string &event, const Uop &u, int64_t cycle);
  const std::vector<TraceEvent> &history() const { return history_; }

private:
  const Registers &regs_;
  std::vector<TraceEvent> history_;
};
} // namespace vfsim
