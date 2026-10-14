// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/trace/recorder.h"
#include "native/uarch/registers.h"
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
