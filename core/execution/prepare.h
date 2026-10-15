// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_EXECUTION_PREPARE_H
#define VFSIM_NATIVE_EXECUTION_PREPARE_H

#include "core/execution/instruction.h"
#include "core/params/database.h"
#include <stdexcept>

#include <string>
#include <unordered_map>

namespace vfsim {

class ValueStorageLookup {
public:
  ValueStorageLookup() = default;
  explicit ValueStorageLookup(
      const std::unordered_map<std::string, ValueInfo> &values) {
    for (const auto &[key, value] : values) {
      const std::string id = value.valueId.empty() ? key : value.valueId;
      storageById_.emplace(id, value.storage);
    }
  }

  ValueStorageKind storageOf(const std::string &name) const {
    auto it = storageById_.find(name);
    if (it != storageById_.end())
      return it->second;

    throw std::runtime_error("Undeclared Canonical value: " + name);
  }

  bool isRegister(const std::string &name) const {
    return storageOf(name) == ValueStorageKind::Register;
  }

  bool isPredicate(const std::string &name) const {
    return storageOf(name) == ValueStorageKind::PredicateRegister;
  }

  bool isRenameable(const std::string &name) const {
    return isRegister(name) || isPredicate(name);
  }

  bool isUB(const std::string &name) const {
    return storageOf(name) == ValueStorageKind::UB;
  }

private:
  std::unordered_map<std::string, ValueStorageKind> storageById_;
};

struct PreparedProgram {
  std::vector<DynamicInst> instructions;
  std::unordered_map<std::string, ValueInfo> values;
  RuntimeParamMap params;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds;
  std::unordered_set<int64_t> emptyTopBlocks;
  int64_t totalTopBlocks = 1;
  int64_t linearInstCount = 0;
};

PreparedProgram prepareProgram(const CanonicalVfInfo &, const ParamDB &,
                               const UarchConfig &);
} // namespace vfsim

#endif // VFSIM_NATIVE_EXECUTION_PREPARE_H
