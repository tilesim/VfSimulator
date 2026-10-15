// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_EXECUTION_MEMBAR_H
#define VFSIM_NATIVE_EXECUTION_MEMBAR_H

#include "core/execution/instruction.h"
#include "core/params/database.h"

#include <functional>
#include <memory>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct MembarEvent {
  int64_t cy, streamSeq, pc;
  std::string event, barrier;
};

class MembarController {
public:
  explicit MembarController(
      const ParamDB *db = nullptr,
      std::optional<MembarTimingConfig> timing = std::nullopt,
      int64_t issueFloor = 0);

  void acceptMembar(const DynamicInst &inst, int64_t cycle = 0);
  void observeInstruction(const DynamicInst &inst);
  void notifyLsuStart(int64_t streamSeq, int64_t cycle);
  void update(
      const std::function<bool(int64_t, const std::string &)> &hasPendingBefore,
      int64_t cycle = 0,
      const std::function<bool(int64_t)> &hasPendingDispatch = {});
  bool blocks(int64_t streamSeq, const std::string &opClass) const;
  bool empty() const noexcept { return barriers_.empty(); }
  int64_t lastRetireCycle() const noexcept { return lastRetireCycle_; }
  bool hasTiming() const { return timing_.has_value(); }
  const std::vector<MembarEvent> &history() const { return history_; }

private:
  struct LsuProgress {
    std::string opClass;
    int64_t pending = 0;
    std::optional<int64_t> startCycle;
  };
  struct Barrier {
    int64_t streamSeq = -1;
    int64_t pc = -1;
    std::string barrier;
    std::string waitClass;
    std::string blockClass;
    bool released = false;
    int64_t availableCycle = 0;
    std::shared_ptr<Barrier> previous;
    std::shared_ptr<LsuProgress> predecessors;
    std::optional<int64_t> issueCycle;
    std::optional<int64_t> releaseCycle;
    std::optional<int64_t> retireCycle;
    bool retired = false;
  };

  void log(const std::string &event, const Barrier &barrier);

  const ParamDB *db_ = nullptr;
  std::optional<MembarTimingConfig> timing_;
  int64_t issueFloor_ = 0;
  int64_t cycle_ = 0;
  int64_t lastRetireCycle_ = 0;
  std::vector<std::shared_ptr<Barrier>> barriers_;
  std::shared_ptr<Barrier> lastBarrier_;
  std::unordered_map<std::string, std::shared_ptr<LsuProgress>> segment_;
  std::unordered_map<int64_t, std::shared_ptr<LsuProgress>> pendingStarts_;
  std::vector<MembarEvent> history_;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_EXECUTION_MEMBAR_H
