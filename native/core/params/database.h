// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OF ANY KIND, EITHER EXPRESS OR IMPLIED,
// INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT, MERCHANTABILITY, OR FITNESS
// FOR A PARTICULAR PURPOSE. See LICENSE in the root of the software repository
// for the full text of the License.

#ifndef VFSIM_NATIVE_PARAMS_DATABASE_H
#define VFSIM_NATIVE_PARAMS_DATABASE_H

#include "native/core/params/types.h"

#include <filesystem>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <tuple>
#include <unordered_map>
#include <vector>

namespace vfsim {

class ParamDB {
public:
  explicit ParamDB(std::filesystem::path baseDir = {});
  // Shared configuration is immutable; caches and warnings belong to this run.
  explicit ParamDB(std::shared_ptr<const ParamBundle> parameters);

  const ParamBundle &bundle() const noexcept { return *bundle_; }
  const UarchConfig &uarch() const noexcept { return bundle_->uarch; }
  const IsaDefaults &isaDefaults() const noexcept {
    return bundle_->isaDefaults;
  }

  bool hasInst(const std::string &op, const std::string &dtype) const;
  const InstructionProfile &inst(const std::string &op,
                                 const std::string &dtype,
                                 const std::string &instructionClass = {},
                                 const std::string &memoryMode = {}) const;
  void recordWarning(const std::string &kind,
                     std::map<std::string, json::Value> fields = {}) const;
  std::vector<ModelWarning> warnings() const;

  int64_t forwardingCycles(const std::string &prod, const std::string &prodForm,
                           const std::string &cons,
                           const std::string &consForm) const;
  int64_t initiationInterval(const std::string &prev,
                             const std::string &prevForm,
                             const std::string &cur,
                             const std::string &curForm) const;

  static std::filesystem::path resolveBaseDir(std::filesystem::path baseDir);

private:
  int64_t computeForwardingCycles(const std::string &prod,
                                  const std::string &prodForm,
                                  const std::string &cons,
                                  const std::string &consForm) const;
  InstructionProfile computeInst(const std::string &op,
                                 const std::string &form) const;
  std::shared_ptr<const ParamBundle> bundle_;
  mutable std::map<std::string, InstructionProfile> profileCache_;
  // Parameters are immutable for this instance. Cache relative latency, never
  // producer start times or register readiness. Cache lifetime and concurrency
  // requirements follow this ParamDB instance, just like profileCache_.
  using ForwardingKey =
      std::tuple<std::string, std::string, std::string, std::string>;
  mutable std::map<ForwardingKey, int64_t, std::less<>> forwardingCache_;
  mutable std::map<std::string, ModelWarning> warnings_;
  mutable std::vector<std::string> warningOrder_;
  mutable std::map<ForwardingKey, int64_t, std::less<>> iiCache_;
  mutable std::mutex warningsMutex_;

  InstructionProfile fallbackInst(const std::string &op,
                                  const std::string &dtype,
                                  bool unsupportedForm) const;
  void recordWarningOnce(const std::string &kind,
                         std::map<std::string, json::Value> fields) const;
};

} // namespace vfsim

#endif // VFSIM_NATIVE_PARAMS_DATABASE_H
