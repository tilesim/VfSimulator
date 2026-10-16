// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER
// EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
// MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE in the root
// of the software repository for the full text of the License.

#ifndef VFSIM_PTOAS_ADAPTER_PLANNER_OPTIONS_H
#define VFSIM_PTOAS_ADAPTER_PLANNER_OPTIONS_H
namespace vfsim {
constexpr unsigned kMaxPlannerUnrollFactor = 8;
struct PlannerOptions {
  unsigned maxUnrollFactor = kMaxPlannerUnrollFactor;
  bool dumpCandidates = false;
};
} // namespace vfsim
#endif
