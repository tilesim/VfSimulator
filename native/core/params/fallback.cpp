// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/core/params/fallback.h"
#include "native/api/common/instruction_catalog.h"
#include "native/core/params/database.h"
#include <algorithm>

namespace vfsim::param_fallback {
namespace {

std::string qualify(const std::string &op, const std::string &form) {
  return form.empty() ? op : op + "." + form;
}

} // namespace

std::string compatibleForm(const std::string &form) {
  if (form == "b16")
    return "fp16";
  if (form == "b32")
    return "fp32";
  return {};
}

std::vector<std::string> formCandidates(const std::string &form) {
  if (form.empty())
    return {};
  const std::string compatible = compatibleForm(form);
  if (!compatible.empty() && compatible != form)
    return {form, compatible};
  return {form};
}

std::vector<PairKeyCandidate> pairKeyCandidates(
    const std::string &producerOp, const std::string &producerForm,
    const std::string &consumerOp, const std::string &consumerForm) {
  std::vector<PairKeyCandidate> out;
  for (const std::string &pForm : formCandidates(producerForm)) {
    for (const std::string &cForm : formCandidates(consumerForm)) {
      out.push_back(PairKeyCandidate{
          qualify(producerOp, pForm),
          qualify(consumerOp, cForm),
          pForm != producerForm || cForm != consumerForm,
      });
    }
  }
  return out;
}

} // namespace vfsim::param_fallback

namespace vfsim {
InstructionProfile ParamDB::fallbackInst(const std::string &op,
                                         const std::string &dtype,
                                         bool unsupportedForm) const {
  const std::string &canon = op;
  InstructionProfile cfg;
  cfg.latency = 9;
  const auto *spec = defaultInstructionCatalog().lookup(canon);
  if (spec) {
    cfg.opClass =
        spec->instructionClass == CatalogInstructionClass::Load    ? "LOAD"
        : spec->instructionClass == CatalogInstructionClass::Store ? "STORE"
                                                                   : "COMPUTE";
  } else if (canon.rfind("VLD", 0) == 0) {
    cfg.opClass = "LOAD";
  } else if (canon.rfind("VST", 0) == 0) {
    cfg.opClass = "STORE";
  } else {
    cfg.opClass = "COMPUTE";
  }
  if (cfg.opClass == "COMPUTE") {
    cfg.fuType = "ALU";
    cfg.dispatchExu = "EXU01";
  }

  json::Value::Object defaults{{"op_class", cfg.opClass},
                               {"latency", cfg.latency}};
  if (cfg.opClass == "COMPUTE") {
    defaults.emplace("EXU", "ALU");
    defaults.emplace("dispatch_exu", "EXU01");
  }
  recordWarningOnce(unsupportedForm ? "unsupported_isa_form"
                                    : "unsupported_isa_op",
                    {{"op", op}, {"form", dtype}, {"used_defaults", defaults}});
  return cfg;
}

} // namespace vfsim
namespace vfsim::param_fallback {
int64_t missingForwarding(const InstructionProfile &producer,
                          const InstructionProfile &consumer, int64_t offset) {
  return producer.opClass == "LOAD" && consumer.opClass == "STORE"
             ? 6
             : std::max<int64_t>(0, producer.latency - offset);
}
int64_t missingII(const InstructionProfile &previous,
                  const InstructionProfile &current) {
  return previous.latency - current.latency == 1 ? 2 : 1;
}
} // namespace vfsim::param_fallback
