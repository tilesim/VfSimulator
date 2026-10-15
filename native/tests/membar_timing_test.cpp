// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/json/value.h"
#include "native/core/execution/membar.h"

#include <filesystem>
#include <iostream>
#include <stdexcept>

using namespace vfsim;

namespace {
void require(bool ok) {
  if (!ok)
    throw std::runtime_error("Membar timing assertion failed");
}
DynamicInst inst(int64_t seq, const std::string &op) {
  DynamicInst i;
  i.type = "inst";
  i.streamSeq = seq;
  i.op = op;
  i.opClass = op == "VLDS" ? "LOAD" : op == "VSTS" ? "STORE" : "COMPUTE";
  i.form = "fp32";
  return i;
}
void barrier(MembarController &unit, int64_t seq, const std::string &kind) {
  auto i = inst(seq, "");
  i.type = "membar";
  i.barrier = kind;
  unit.acceptMembar(i);
}
void run(const ParamDB &db) {
  const auto config = db.uarch().membarTiming;
  require(config.has_value());
  for (const auto &kind : {"VLD_VST", "VST_VLD"}) {
    const bool store = std::string(kind) == "VLD_VST";
    MembarController unit(&db, config);
    barrier(unit, 0, kind);
    const auto consumer = inst(1, store ? "VSTS" : "VLDS");
    for (int cy = 0; cy <= 24; ++cy) {
      unit.update([](auto, auto) { return false; }, cy);
      require(unit.blocks(consumer.streamSeq, consumer.opClass) ==
              (cy < (store ? 23 : 9)));
      require(!unit.blocks(inst(1, "VADD").streamSeq, inst(1, "VADD").opClass));
      if (cy == 9 && !store)
        require(!unit.empty());
    }
    require(unit.lastRetireCycle() == (store ? 21 : 10));

    MembarController repeated(&db, config);
    for (int i = 0; i < 8; ++i)
      barrier(repeated, i, kind);
    for (int cy = 0; cy <= 180; ++cy)
      repeated.update([](auto, auto) { return false; }, cy);
    require(repeated.empty());
    require(repeated.lastRetireCycle() == (store ? 168 : 87));
  }
  MembarController segment(&db, config);
  barrier(segment, 0, "VLD_VST");
  segment.observeInstruction(inst(1, "VSTS"));
  segment.observeInstruction(inst(2, "VSTS"));
  barrier(segment, 3, "VST_VLD");
  segment.observeInstruction(inst(4, "VLDS"));
  for (int cy = 0; cy <= 39; ++cy) {
    if (cy == 23)
      segment.notifyLsuStart(2, cy);
    if (cy == 26)
      segment.notifyLsuStart(1, cy);
    segment.update([](auto, auto) { return false; }, cy);
    require(segment.blocks(inst(4, "VLDS").streamSeq,
                           inst(4, "VLDS").opClass) == (cy < 38));
  }
  require(segment.lastRetireCycle() == 39);
  MembarController delayed(&db, config);
  barrier(delayed, 0, "VLD_VST");
  for (int cy = 0; cy <= 54; ++cy) {
    delayed.update([cy](auto, auto) { return cy < 50; }, cy);
    require(delayed.blocks(inst(1, "VSTS").streamSeq,
                           inst(1, "VSTS").opClass) == (cy < 54));
  }
  require(delayed.lastRetireCycle() == 52);
  MembarController dispatch(&db, config);
  barrier(dispatch, 1, "VST_VLD");
  for (int cy = 0; cy <= 16; ++cy) {
    dispatch.update([](auto, auto) { return false; }, cy,
                    [cy](auto) { return cy < 7; });
    require(dispatch.blocks(inst(2, "VLDS").streamSeq,
                            inst(2, "VLDS").opClass) == (cy < 15));
  }
  require(dispatch.lastRetireCycle() == 16);
  auto invalid = *config;
  invalid.directions.at("VST_VLD").retireLatency = -1;
  bool rejected = false;
  try {
    MembarController unit(&db, invalid);
  } catch (const std::invalid_argument &) {
    rejected = true;
  }
  require(rejected);
}
} // namespace

int main() {
  ParamDB db(VFSIM_SOURCE_ROOT);
  run(db);
  std::cout << "A5 Membar timing tests passed\n";
}
