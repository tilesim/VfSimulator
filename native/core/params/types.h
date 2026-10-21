// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS
// FOR A PARTICULAR PURPOSE. See LICENSE in the root of the software repository
// for the full text of the License.

#ifndef VFSIM_NATIVE_PARAMS_TYPES_H
#define VFSIM_NATIVE_PARAMS_TYPES_H

#include "native/api/json/value.h"
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct InstructionProfile {
  int64_t latency = 0;
  std::string lsuResource;
  int64_t lsuIssueInterval = 1;
  int64_t alignStateLatency = 1;
  std::string fuType;
  std::string dispatchExu;
  std::string opClass;
  std::vector<int> allowedExuPorts;
};

struct IsaDefaults {
  int64_t vfStartupCost = 0;
  int64_t vfDrainCost = 0;
};

struct MembarDirectionTiming {
  int64_t releaseLatency = 0;
  int64_t retireLatency = 0;
  int64_t consumerDelay = 0;
  int64_t nextIssueDelay = 0;
};

struct MembarTimingConfig {
  int64_t admissionDelay = 0;
  std::map<std::string, int64_t> startFeedback;
  std::map<std::string, MembarDirectionTiming> directions;
};

struct UarchConfig {
  std::string name = "";
  int64_t issuePorts = 2;
  int64_t loadPorts = 2;
  int64_t storePorts = 1;
  int64_t ubSlots = 2;
  int64_t ubBandwidthBytesPerCycle = 512;
  int64_t iduWindowWidth = 6;
  int64_t iduIssueWidth = 5;
  int64_t ldqWidth = 24;
  int64_t vregNum = 68;
  int64_t physicalPredicateRegisters = 32;
  int64_t shqDepth = 58;
  int64_t exqDepth = 26;
  int64_t exqIssueInflightCapPerPort = 7;
  int64_t canonicalDynamicInstructionLimit = 20000;
  int64_t iduToOooDelay = 1;
  int64_t oooToShqDelay = 1;
  int64_t oooToLsqDelay = 1;
  int64_t exqRecvDelay = 1;
  int64_t shqToExqPortPerCycle = 1;
  int64_t shqReleaseDelay = 1;
  int64_t iduVisiblePregDelay = 0;
  int64_t iduVisibleShqDelay = 0;
  int64_t iduPostUpdateReadyLatency = 1;
  int64_t consumerReleaseStartOffset = 4;
  int64_t vloopToDispatchDelay = 2;
  int64_t iduDispatchStartAdvance = 2;
  int64_t initialTopBlockVloopStartCycle = 19;
  int64_t nestedVloopInitialStartGap = 1;
  int64_t loop1MinFeedbackGap = 7;
  int64_t innermostIterDispatchStride = 1;
  int64_t lsuStorePriorityPregThreshold = 1;
  std::string shqExqDispatchPolicy = "fu_round_robin_exu0_reserve";
  int64_t exu0ReserveLookahead = 8;
  int64_t exu0ReserveMinCount = 1;
  bool enableIsuQueueModel = true;
  bool enableShqCreditModel = true;
  bool enableCreditVisibilityDelay = true;
  bool globalShqPregGate = false;
  bool useExplicitIduCreditBank = false;
  bool exqCapacityCountsInflight = false;
  bool enforceSameCycleSrcHazard = false;
  bool enableCrossFuIi = true;
  bool enableExqGreedyBalance = false;
  bool assertStartReleaseIntegrity = true;
  bool predictExqIssueWithCrossFu = false;
  bool shqExqStaticRr = false;
  std::optional<MembarTimingConfig> membarTiming;
  bool theoreticalLimitMode = false;
  bool theoreticalLimitVloopOnly = false;
  bool theoreticalLimitLegacyForwarding = false;
  bool theoreticalLimitDirectIssue = false;
  std::optional<int64_t> theoreticalLimitVloopToDispatchDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduDispatchStartAdvance = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduWindowWidth = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduIssueWidth = std::nullopt;
  std::optional<int64_t> theoreticalLimitLdqWidth = std::nullopt;
  std::optional<int64_t> theoreticalLimitShqDepth = std::nullopt;
  std::optional<int64_t> theoreticalLimitExqDepth = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduToOooDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitOooToShqDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitOooToLsqDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitExqRecvDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitShqReleaseDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduVisiblePregDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitIduVisibleShqDelay = std::nullopt;
  std::optional<int64_t> theoreticalLimitShqToExqPortPerCycle = std::nullopt;
  std::optional<int64_t> theoreticalLimitExqIssueInflightCapPerPort =
      std::nullopt;
};

using DTypeName = std::string;
using OpName = std::string;

struct ParamBundle {
  IsaDefaults isaDefaults;
  std::unordered_map<OpName, std::unordered_map<DTypeName, InstructionProfile>>
      isa;
  std::unordered_map<OpName, std::unordered_map<std::string, int64_t>>
      memoryModeLatency;
  std::unordered_map<std::string, std::unordered_map<std::string, int64_t>>
      forwardingByForm;
  std::unordered_map<std::string, std::unordered_map<std::string, int64_t>>
      initiationIntervalByForm;
  int64_t forwardingDefaultOffset = 3;
  UarchConfig uarch;
};

struct ModelWarning {
  std::string kind;
  std::map<std::string, json::Value> fields;
  int64_t count = 1;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PARAMS_TYPES_H
