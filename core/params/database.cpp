// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "core/params/database.h"
#include "api/common/instruction_catalog.h"
#include "core/params/fallback.h"
#include "core/params/loader.h"
#include <algorithm>
#include <cctype>
#include <stdexcept>
namespace vfsim {
namespace {

std::string qualifyOp(const std::string &op, const std::string &form) {
  return form.empty() ? op : op + "." + form;
}

std::string upper(std::string value) {
  std::transform(
      value.begin(), value.end(), value.begin(),
      [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
  return value;
}

std::string warningKey(const std::string &kind,
                       const std::map<std::string, std::string> &fields) {
  std::string key = kind;
  for (const auto &[k, v] : fields)
    key += "|" + k + "=" + v;
  return key;
}

} // namespace
ParamDB::ParamDB(std::filesystem::path baseDir)
    : ParamDB(std::make_shared<const ParamBundle>(
          loadParameters(resolveBaseDir(std::move(baseDir))))) {}

ParamDB::ParamDB(std::shared_ptr<const ParamBundle> parameters)
    : bundle_(std::move(parameters)) {
  if (!bundle_)
    throw std::invalid_argument("ParamDB requires a parameter bundle");
}
const InstructionProfile &ParamDB::inst(const std::string &op,
                                        const std::string &form,
                                        const std::string &instructionClass) const {
  const std::string canonical = upper(op);
  const std::string key = canonical + "\x1f" + form + "\x1f" + instructionClass;
  auto it = profileCache_.find(key);
  if (it != profileCache_.end())
    return it->second;
  auto profile = computeInst(canonical, form);
  if (!instructionClass.empty())
    profile.opClass = instructionClass;
  if (profile.fuType.empty())
    profile.fuType = "ALU";
  profile.allowedExuPorts = profile.dispatchExu == "EXU0_ONLY"
                                ? std::vector<int>{0} : std::vector<int>{0, 1};
  return profileCache_.emplace(key, std::move(profile)).first->second;
}
std::filesystem::path ParamDB::resolveBaseDir(std::filesystem::path baseDir) {
  if (baseDir.empty())
    return std::filesystem::absolute(std::filesystem::path(VFSIM_SOURCE_ROOT));
  return std::filesystem::absolute(std::move(baseDir));
}

bool ParamDB::hasInst(const std::string &op, const std::string &dtype) const {
  const auto opIt = bundle_->isa.find(op);
  if (opIt == bundle_->isa.end())
    return false;
  for (const std::string &candidate : param_fallback::formCandidates(dtype)) {
    if (opIt->second.find(candidate) != opIt->second.end())
      return true;
  }
  return false;
}

InstructionProfile ParamDB::computeInst(const std::string &op,
                                        const std::string &dtype) const {
  const auto opIt = bundle_->isa.find(op);
  if (opIt == bundle_->isa.end())
    return fallbackInst(op, dtype, false);
  const auto dtypeIt = opIt->second.find(dtype);
  if (dtypeIt != opIt->second.end())
    return dtypeIt->second;
  const std::string compatible = param_fallback::compatibleForm(dtype);
  if (!compatible.empty()) {
    const auto compatibleIt = opIt->second.find(compatible);
    if (compatibleIt != opIt->second.end()) {
      recordWarningOnce(
          "compatible_isa_form_fallback",
          {{"op", op}, {"requested_form", dtype}, {"used_form", compatible}});
      return compatibleIt->second;
    }
  }
  return fallbackInst(op, dtype, true);
}

void ParamDB::recordWarning(const std::string &kind,
                            std::map<std::string, std::string> fields) const {
  const std::lock_guard<std::mutex> lock(warningsMutex_);
  const std::string key = warningKey(kind, fields);
  auto it = warnings_.find(key);
  if (it != warnings_.end()) {
    it->second.count += 1;
    return;
  }
  ModelWarning warning;
  warning.kind = kind;
  warning.fields = std::move(fields);
  warning.count = 1;
  warnings_.emplace(key, std::move(warning));
}

std::vector<ModelWarning> ParamDB::warnings() const {
  const std::lock_guard<std::mutex> lock(warningsMutex_);
  std::vector<ModelWarning> out;
  out.reserve(warnings_.size());
  for (const auto &[_, warning] : warnings_)
    out.push_back(warning);
  return out;
}

void ParamDB::recordWarningOnce(
    const std::string &kind, std::map<std::string, std::string> fields) const {
  const std::lock_guard<std::mutex> lock(warningsMutex_);
  const std::string key = warningKey(kind, fields);
  if (warnings_.find(key) != warnings_.end())
    return;
  warnings_.emplace(key, ModelWarning{kind, std::move(fields), 1});
}

int64_t ParamDB::forwardingCycles(const std::string &prod,
                                  const std::string &prodForm,
                                  const std::string &cons,
                                  const std::string &consForm) const {
  // Look up with references to avoid allocating strings on the hot path.
  const auto key = std::tie(prod, prodForm, cons, consForm);
  const auto it = forwardingCache_.find(key);
  if (it != forwardingCache_.end())
    return it->second;
  const int64_t cycles = computeForwardingCycles(prod, prodForm, cons, consForm);
  forwardingCache_.emplace(key, cycles);
  return cycles;
}

int64_t ParamDB::computeForwardingCycles(const std::string &prod,
                                         const std::string &prodForm,
                                         const std::string &cons,
                                         const std::string &consForm) const {
  const auto *spec = defaultInstructionCatalog().lookup(prod);
  if (spec && !spec->forwardingOpcode.empty())
    return forwardingCycles(spec->forwardingOpcode, prodForm, cons, consForm);
  const std::string requestedProd = qualifyOp(prod, prodForm);
  const std::string requestedCons = qualifyOp(cons, consForm);
  for (const auto &candidate :
       param_fallback::pairKeyCandidates(prod, prodForm, cons, consForm)) {
    const auto prodIt = bundle_->forwardingByForm.find(candidate.producer);
    if (prodIt != bundle_->forwardingByForm.end()) {
      const auto consIt = prodIt->second.find(candidate.consumer);
      if (consIt != prodIt->second.end()) {
        if (candidate.usedCompatible) {
          recordWarningOnce("compatible_forwarding_pair_fallback",
                            {{"producer", requestedProd},
                             {"consumer", requestedCons},
                             {"used_producer", candidate.producer},
                             {"used_consumer", candidate.consumer}});
        }
        return std::max<int64_t>(0, consIt->second);
      }
    }
  }
  const auto defaults = bundle_->forwardingByForm.find(requestedProd);
  if (defaults != bundle_->forwardingByForm.end()) {
    const auto wildcard = defaults->second.find("*");
    if (wildcard != defaults->second.end())
      return std::max<int64_t>(0, wildcard->second);
  }
  const InstructionProfile &prodCfg = inst(prod, prodForm);
  const InstructionProfile &consCfg = inst(cons, consForm);
  const bool lsuDefault =
      prodCfg.opClass == "LOAD" && consCfg.opClass == "STORE";
  const int64_t value = param_fallback::missingForwarding(
      prodCfg, consCfg, bundle_->forwardingDefaultOffset);
  recordWarningOnce(
      "missing_forwarding_pair",
      {{"producer", qualifyOp(prod, prodForm)},
       {"consumer", qualifyOp(cons, consForm)},
       {"producer_latency", std::to_string(prodCfg.latency)},
       {"used_default", std::to_string(value)},
       {"rule", lsuDefault ? "load_store_default"
                           : "producer_latency_minus_default_offset"}});
  return value;
}

int64_t ParamDB::initiationInterval(const std::string &prev,
                                    const std::string &prevForm,
                                    const std::string &cur,
                                    const std::string &curForm) const {
  const std::string requestedPrev = qualifyOp(prev, prevForm);
  const std::string requestedCur = qualifyOp(cur, curForm);
  for (const auto &candidate :
       param_fallback::pairKeyCandidates(prev, prevForm, cur, curForm)) {
    const auto prevIt =
        bundle_->initiationIntervalByForm.find(candidate.producer);
    if (prevIt != bundle_->initiationIntervalByForm.end()) {
      const auto curIt = prevIt->second.find(candidate.consumer);
      if (curIt != prevIt->second.end()) {
        if (candidate.usedCompatible) {
          recordWarningOnce("compatible_ii_pair_fallback",
                            {{"prev", requestedPrev},
                             {"cur", requestedCur},
                             {"used_prev", candidate.producer},
                             {"used_cur", candidate.consumer}});
        }
        return std::max<int64_t>(1, curIt->second);
      }
    }
  }
  const InstructionProfile &prevCfg = inst(prev, prevForm);
  const InstructionProfile &curCfg = inst(cur, curForm);
  const int64_t value = param_fallback::missingII(prevCfg, curCfg);
  recordWarningOnce("missing_ii_pair",
                    {{"prev", qualifyOp(prev, prevForm)},
                     {"cur", qualifyOp(cur, curForm)},
                     {"prev_latency", std::to_string(prevCfg.latency)},
                     {"cur_latency", std::to_string(curCfg.latency)},
                     {"used_default", std::to_string(value)},
                     {"rule", value == 2 ? "prev_latency_minus_cur_latency_eq_1"
                                         : "default_one"}});
  return value;
}

} // namespace vfsim
