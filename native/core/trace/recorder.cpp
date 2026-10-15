// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/trace/recorder.h"
#include "native/core/uarch/registers.h"
namespace vfsim {
void TraceRecorder::log(const std::string &event, const Uop &u, int64_t cycle) {
  TraceEvent row{cycle,
                 event,
                 u,
                 regs_.physicalVectorFree(),
                 regs_.physicalPredicateFree(),
                 {},
                 {}};
  for (const auto &p : u.pregSrc)
    if (p && regs_.isPredicatePhysical(*p))
      row.sourcePredicates.push_back(*p);
  for (const auto &p : u.pregDst)
    if (regs_.isPredicatePhysical(p))
      row.destinationPredicates.push_back(p);
  history_.push_back(std::move(row));
}
} // namespace vfsim

namespace vfsim {
json::Value iterationPathJson(const std::vector<IterationIdentity> &path) {
  json::Value::Array result;
  for (const auto &item : path)
    result.emplace_back(
        json::Value::Object{{"loop_id", item.loopId},
                            {"iteration", item.iteration},
                            {"induction_variable", item.inductionVariable},
                            {"induction_value", item.inductionValue}});
  return result;
}
json::Value valueInstancesJson(const std::vector<ValueInstance> &values) {
  json::Value::Array result;
  for (const auto &item : values)
    result.emplace_back(json::Value::Object{
        {"definition_id", item.definitionId},
        {"iteration_path", iterationPathJson(item.iterationPath)}});
  return result;
}
json::Value::Object instructionTraceIdentity(const Uop &u) {
  auto names = [](const std::vector<ValueInstance> &values,
                  const std::vector<std::string> &fallback) {
    json::Value::Array result;
    if (values.empty())
      for (const auto &name : fallback)
        result.emplace_back(name);
    else
      for (const auto &item : values)
        result.emplace_back(item.definitionId);
    return result;
  };
  return {{"op", u.op},
          {"form", u.form},
          {"src", names(u.srcValueInstances, u.src)},
          {"dst", names(u.dstValueInstances, u.dst)},
          {"static_instruction_id", u.staticInstructionId.empty()
                                        ? json::Value{}
                                        : json::Value(u.staticInstructionId)},
          {"iteration_path", iterationPathJson(u.iterationPath)},
          {"stream_seq", u.streamSeq},
          {"src_value_instances", valueInstancesJson(u.srcValueInstances)},
          {"dst_value_instances", valueInstancesJson(u.dstValueInstances)}};
}
} // namespace vfsim
