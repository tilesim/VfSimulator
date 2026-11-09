// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#pragma once

#include "native/core/params/types.h"

namespace vfsim {
std::vector<std::string> instructionBanks(const std::string &op);
std::optional<int64_t> computeStoreForwarding(const ParamBundle &rules, const InstructionProfile &producer,
                                            const InstructionProfile &consumer);
std::optional<int64_t> forwardingInterval(const ParamBundle &rules,
                                        const InstructionProfile &producer,
                                        const InstructionProfile &consumer);
std::optional<int64_t> issueInterval(const ParamBundle &rules, const InstructionProfile &previous,
                                   const InstructionProfile &current);
void validateResources(const ParamBundle &parameters);
} // namespace vfsim
