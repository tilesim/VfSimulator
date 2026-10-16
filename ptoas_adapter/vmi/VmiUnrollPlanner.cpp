// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// This program is free software, you can redistribute it and/or modify it under
// the terms and conditions of CANN Open Software License Agreement Version 2.0
// (the "License"). Please refer to the License for details. You may not use
// this file except in compliance with the License. THIS SOFTWARE IS PROVIDED ON
// AN "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, EITHER
// EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO NON-INFRINGEMENT,
// MERCHANTABILITY, or FITNESS FOR A PARTICULAR PURPOSE. See LICENSE in the root
// of the software repository for the full text of the License.

#include "ptoas_adapter/vmi/VmiUnrollPlanner.h"
#include "ptoas_adapter/vmi/VmiCanonicalAdapter.h"

#include "api/input_api.h"
#include "core/simulator.h"

#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinAttributes.h"
#include "mlir/IR/BuiltinTypes.h"
#include "mlir/IR/Diagnostics.h"
#include "mlir/IR/Operation.h"
#include "mlir/IR/Visitors.h"
#include "llvm/ADT/SmallVector.h"
#include "llvm/ADT/StringRef.h"
#include "llvm/Support/raw_ostream.h"

#include <algorithm>
#include <cstdint>
#include <exception>
#include <filesystem>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace vfsim {
namespace {

constexpr llvm::StringLiteral kLoopFusedAttr = "pto.vmi.loop_fused";
constexpr llvm::StringLiteral kLoopFusionIdAttr = "pto.vmi.loop_fusion.id";
constexpr llvm::StringLiteral kVfSimUnrollFactorAttr = "pto.vfsim.unroll_factor";

mlir::Operation *findVectorScope(mlir::Operation *operation) {
  for (auto *parent = operation->getParentOp(); parent != nullptr; parent = parent->getParentOp()) {
    const auto name = parent->getName().getStringRef();
    if (name == "pto.vecscope" || name == "pto.vec_scope" || name == "pto.strict_vecscope" ||
        name == "pto.strict_vec_scope") {
      return parent;
    }
  }
  return nullptr;
}

struct CandidateResult {
  VmiUnrollMode mode = VmiUnrollMode::NoUnroll;
  int64_t factor = 1;
  int64_t cycles = 0;
};

llvm::StringRef modeName(VmiUnrollMode mode) {
  switch (mode) {
  case VmiUnrollMode::NoUnroll:
    return "NO_UNROLL";
  case VmiUnrollMode::Abcabc:
    return "ABCABC";
  case VmiUnrollMode::Aabbcc:
    return "AABBCC";
  }
  return "UNKNOWN";
}

std::optional<CandidateResult> evaluateCandidate(mlir::Operation *vectorScope,
                                                 mlir::Operation *targetLoop, VmiUnrollMode mode,
                                                 int64_t factor, const CoreVfCostModel &model,
                                                 std::string &reason) {
  auto vfInfo = lowerVmiScope(vectorScope, targetLoop, mode, factor, reason);
  if (!vfInfo) {
    return std::nullopt;
  }
  try {
    const SimulationResult result = model.runVfInfo(InputAPI::fromCanonical(std::move(*vfInfo)));
    return CandidateResult{mode, factor, std::max(result.vfEndCycle, result.cyclesExecuted)};
  } catch (const std::exception &error) {
    reason = error.what();
    return std::nullopt;
  }
}

void writePlan(mlir::Operation *loop, int64_t factor) {
  mlir::Builder builder(loop->getContext());
  loop->setAttr(kVfSimUnrollFactorAttr, builder.getI32IntegerAttr(factor));
}

} // namespace

mlir::LogicalResult planVmiUnrollIR(mlir::Operation *scope, const PlannerOptions &options) {
  if (scope == nullptr) {
    return mlir::failure();
  }

  std::unique_ptr<CoreVfCostModel> model;
  try {
    model = std::make_unique<CoreVfCostModel>(std::filesystem::path(VFSIM_SOURCE_ROOT),
                                              std::filesystem::path{});
  } catch (const std::exception &error) {
    scope->emitError() << "VfSim failed to load its parameter database: " << error.what();
    return mlir::failure();
  }
  llvm::SmallVector<mlir::Operation *> candidateLoops;
  scope->walk([&](mlir::Operation *operation) {
    if (operation->getName().getStringRef() == "scf.for" && operation->hasAttr(kLoopFusedAttr) &&
        operation->hasAttr(kLoopFusionIdAttr)) {
      candidateLoops.push_back(operation);
    }
  });

  for (mlir::Operation *loop : candidateLoops) {
    const auto tripCount = getStaticLoopTripCount(loop);
    if (!tripCount || *tripCount <= 1) {
      loop->emitWarning() << "VfSim skipped VMI fused loop: trip count is not a static "
                             "integer greater than one";
      continue;
    }
    mlir::Operation *vectorScope = findVectorScope(loop);
    if (vectorScope == nullptr) {
      loop->emitWarning() << "VfSim skipped VMI fused loop: no enclosing vector scope";
      continue;
    }

    std::vector<int64_t> factors;
    const int64_t upper =
        std::min<int64_t>({options.maxUnrollFactor, kMaxPlannerUnrollFactor, *tripCount});
    for (int64_t factor = 2; factor <= upper; ++factor) {
      factors.push_back(factor);
    }
    if (factors.empty()) {
      if (options.dumpCandidates) {
        llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                     << " skipped: no candidate factor in [2, " << options.maxUnrollFactor << "]\n";
      }
      continue;
    }

    std::string reason;
    auto baseline =
        evaluateCandidate(vectorScope, loop, VmiUnrollMode::NoUnroll, 1, *model, reason);
    if (!baseline) {
      loop->emitWarning() << "VfSim skipped VMI fused loop: " << reason;
      continue;
    }
    CandidateResult best = *baseline;
    if (options.dumpCandidates) {
      llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                   << " candidate mode=NO_UNROLL factor=1 cycles=" << baseline->cycles << "\n";
    }

    for (int64_t factor : factors) {
      for (VmiUnrollMode mode : {VmiUnrollMode::Abcabc, VmiUnrollMode::Aabbcc}) {
        reason.clear();
        auto candidate = evaluateCandidate(vectorScope, loop, mode, factor, *model, reason);
        if (!candidate) {
          loop->emitWarning() << "VfSim skipped VMI fused loop candidate " << modeName(mode)
                              << " factor=" << factor << ": " << reason;
          continue;
        }
        if (options.dumpCandidates) {
          llvm::errs() << "[VfSim] loop trip_count=" << *tripCount
                       << " candidate mode=" << modeName(mode) << " factor=" << factor
                       << " cycles=" << candidate->cycles << "\n";
        }
        if (candidate->cycles < best.cycles) {
          best = *candidate;
        }
      }
    }

    writePlan(loop, best.factor);
    if (options.dumpCandidates) {
      llvm::errs() << "[VfSim] selected mode=" << modeName(best.mode) << " factor=" << best.factor
                   << " cycles=" << best.cycles << "\n";
    }
  }
  return mlir::success();
}

} // namespace vfsim
