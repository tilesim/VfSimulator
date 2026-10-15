// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_PARAMS_FALLBACK_H
#define VFSIM_NATIVE_PARAMS_FALLBACK_H

#include "native/core/params/types.h"
#include <string>
#include <vector>

namespace vfsim::param_fallback {

int64_t missingForwarding(const InstructionProfile &producer,
                          const InstructionProfile &consumer, int64_t offset);
int64_t missingII(const InstructionProfile &previous,
                  const InstructionProfile &current);

std::string compatibleForm(const std::string &form);
std::vector<std::string> formCandidates(const std::string &form);

struct PairKeyCandidate {
  std::string producer;
  std::string consumer;
  bool usedCompatible = false;
};

std::vector<PairKeyCandidate> pairKeyCandidates(
    const std::string &producerOp, const std::string &producerForm,
    const std::string &consumerOp, const std::string &consumerForm);

} // namespace vfsim::param_fallback

#endif // VFSIM_NATIVE_PARAMS_FALLBACK_H
