// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#ifndef VFSIM_NATIVE_EXECUTION_PREPARE_H
#define VFSIM_NATIVE_EXECUTION_PREPARE_H

#include "core/execution/instruction.h"
#include "core/execution/optimization.h"
#include "core/params/database.h"
#include <stdexcept>

#include <memory>
#include <optional>
#include <string>
#include <unordered_map>

namespace vfsim {

// Opaque owner of one interning of the existing static graph (loop topology,
// static pc map and lowering plan). The streaming preparation builds it once and
// hands it to the cursor, so the Engine metadata and the on-demand lowering share
// a single layout instead of each constructing an Expander.
//
// The Expander inside a layout carries mutable lowering state (binding slots,
// frame stack), so a layout has exactly one consumer. That is enforced by the
// type system: the handle is a move-only unique_ptr with an out-of-line deleter
// (the complete type and its definition stay private to expansion.cpp).
struct CanonicalStaticLayout;
struct CanonicalStaticLayoutDeleter {
  void operator()(CanonicalStaticLayout *) const;
};
using CanonicalLayoutHandle =
    std::unique_ptr<CanonicalStaticLayout, CanonicalStaticLayoutDeleter>;

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
  // Streamed scoring materializes identities per iteration; registration is
  // monotonic and only happens before the first rename of that identity.
  bool add(const std::string &name, ValueStorageKind storage) {
    auto [it, inserted] = storageById_.try_emplace(name, storage);
    if (!inserted)
      it->second = storage;
    return inserted;
  }
  bool remove(const std::string &name) {
    return storageById_.erase(name) != 0;
  }
  size_t size() const { return storageById_.size(); }

private:
  std::unordered_map<std::string, ValueStorageKind> storageById_;
};

struct PreparedProgram {
  std::vector<DynamicInst> instructions;
  // Flat dense logical-operand ids; each instruction indexes a span with
  // DynamicInst::srcIdBase/dstIdBase. Empty for hand-built programs.
  std::vector<int32_t> operandIds;
  // Dense operand id -> defining topBlock id (-1 for external/static).
  std::vector<int32_t> operandOwner;
  std::unordered_map<std::string, ValueInfo> values;
  RuntimeParamMap params;
  std::unordered_map<int, std::vector<int64_t>> topBlockLoopBounds;
  std::unordered_set<int64_t> emptyTopBlocks;
  int64_t totalTopBlocks = 1;
  int64_t linearInstCount = 0;
  // Exact number of dynamic events the static graph will produce, -1 when the
  // counting path was not used. Derived from the static graph and trip counts
  // only (no dynamic pre-scan), so a streaming consumer can use it as the
  // stream length instead of a lookahead probe.
  int64_t dynamicEventCount = -1;
  CompilerOptimizationStats compilerOptimizationStats;
  bool retainsDiagnostics = true;
};

// Diagnostic preparation retains full value/provenance tables for trace
// export. Cost-only preparation retains all scheduling/dependency metadata
// and budgets, but cannot subsequently export a trace.
PreparedProgram prepareProgram(const CanonicalVfInfo &, const ParamDB &,
                               const UarchConfig &,
                               bool retainDiagnostics = true);

// Streaming preparation result: the Engine-facing static metadata plus the one
// static layout the cursor lowers from. The cursor consumes `layout` (move-only,
// single consumer); the static graph is interned exactly once for both.
struct PreparedStreamingLayout {
  PreparedProgram program;
  CanonicalLayoutHandle layout;
};

// Normal no-trace scoring preparation for the streamed execution path: no
// dynamic instruction/metadata materialisation, only the static graph tables,
// and the static layout is built once here and returned for the cursor to reuse.
// postLicmContext must be the exact post-LICM rewrite the caller also hands to
// the cursor (single LICM), and stats is that rewrite's report.
//
// postLicmContext is engaged only when LICM actually rewrote the root context.
// Without a rewrite the layout borrows input.context instead of copying it, so
// an unchanged program costs no canonical node copy here (the eager preparation
// expands the caller's context in place for the same reason). The caller keeps
// input alive for the plan's lifetime, which the layout already requires.
//
// The returned program keeps no value identity table: this is the no-trace
// scoring contract, exactly like prepareProgram(..., retainDiagnostics=false),
// and every streamed event carries its operand storage halves, so the Engine
// never resolves a value by name on this route.
PreparedStreamingLayout prepareProgramForStreamingLayout(
    const CanonicalVfInfo &,
    std::optional<std::vector<CanonicalNode>> postLicmContext, const ParamDB &,
    const UarchConfig &, CompilerOptimizationStats stats);
} // namespace vfsim

#endif // VFSIM_NATIVE_EXECUTION_PREPARE_H
