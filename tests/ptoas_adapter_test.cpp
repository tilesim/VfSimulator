// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/input_api.h"
#include "core/execution/prepare.h"
#include "core/params/database.h"
#include "core/simulator.h"
#include "mlir/Dialect/Arith/IR/Arith.h"
#include "mlir/Dialect/Func/IR/FuncOps.h"
#include "mlir/Dialect/SCF/IR/SCF.h"
#include "mlir/IR/Builders.h"
#include "mlir/IR/BuiltinOps.h"
#include "mlir/IR/Diagnostics.h"
#include "mlir/Parser/Parser.h"
#include "ptoas_adapter/IRPlanner.h"
#include "ptoas_adapter/tileop/TileOpTemplates.h"
#include "ptoas_adapter/vmi/VmiCanonicalAdapter.h"
#include <iostream>
#include <stdexcept>

namespace {
void require(bool condition, const char *message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

// PTO remains an unregistered dialect: the adapter must not depend on it.
mlir::OwningOpRef<mlir::ModuleOp> parse(mlir::MLIRContext &context, llvm::StringRef text) {
  auto module = mlir::parseSourceString<mlir::ModuleOp>(
      text, mlir::ParserConfig(&context, /*verifyAfterParse=*/false));
  require(static_cast<bool>(module), "Failed to parse adapter fixture");
  return module;
}

constexpr llvm::StringLiteral kVmi = R"mlir(
module {
  func.func @vmi(%base: i64) {
    %c0 = arith.constant 0 : index
    %c1 = arith.constant 1 : index
    %c5 = arith.constant 5 : index
    "pto.vecscope"() ({
      %ptr = "pto.castptr"(%base) : (i64) -> !pto.ptr<f32, ub>
      scf.for %iv = %c0 to %c5 step %c1 {
        %a = "pto.vlds"(%ptr) : (!pto.ptr<f32, ub>) -> vector<64xf32>
        %b = "pto.vadd"(%a, %a) : (vector<64xf32>, vector<64xf32>) -> vector<64xf32>
        %view = "pto.vbitcast"(%b) : (vector<64xf32>) -> vector<64xi32>
        "pto.vsts"(%view, %ptr) : (vector<64xi32>, !pto.ptr<f32, ub>) -> ()
      } {pto.vmi.loop_fused, pto.vmi.loop_fusion.id = 0 : i64}
      "pto.yield"() : () -> ()
    }) : () -> ()
    return
  }
})mlir";

constexpr llvm::StringLiteral kTile = R"mlir(
module {
  %b = "pto.alloc_tile"() : () -> !pto.tile_buf<vec, 32x128xf32, valid=32x64>
  %c = "pto.alloc_tile"() : () -> !pto.tile_buf<vec, 32x128xf32, valid=32x64>
  %a = "pto.alloc_tile"() : () -> !pto.tile_buf<vec, 32x128xf32, valid=32x64>
  %d = "pto.alloc_tile"() : () -> !pto.tile_buf<vec, 32x128xf32, valid=32x64>
  "pto.tadd"(%b, %c, %a) {
    candidates = [{name = "vmi_tadd", loop_depth = 1 : i64, tags = ["vmi"]},
                  {name = "ordinary_tadd", loop_depth = 2 : i64}],
    pto.fusion.group_id = 0 : i64, pto.fusion.order = 0 : i64
  } : (!pto.tile_buf<vec, 32x128xf32, valid=32x64>,
       !pto.tile_buf<vec, 32x128xf32, valid=32x64>,
       !pto.tile_buf<vec, 32x128xf32, valid=32x64>) -> ()
  "pto.tmul"(%a, %b, %d) {
    candidates = [{name = "vmi_tmul", loop_depth = 1 : i64, tags = ["vmi"]},
                  {name = "ordinary_tmul", loop_depth = 2 : i64}],
    pto.fusion.group_id = 0 : i64, pto.fusion.order = 1 : i64
  } : (!pto.tile_buf<vec, 32x128xf32, valid=32x64>,
       !pto.tile_buf<vec, 32x128xf32, valid=32x64>,
       !pto.tile_buf<vec, 32x128xf32, valid=32x64>) -> ()
  "pto.tstore"(%a) : (!pto.tile_buf<vec, 32x128xf32, valid=32x64>) -> ()
  "pto.tstore"(%d) : (!pto.tile_buf<vec, 32x128xf32, valid=32x64>) -> ()
})mlir";

constexpr llvm::StringLiteral kVmiCarry = R"mlir(
module {
  func.func @carry(%ptr: !pto.ptr<f32, ub>) {
    %c0 = arith.constant 0 : index
    %c1 = arith.constant 1 : index
    %c5 = arith.constant 5 : index
    %f0 = arith.constant 0.0 : f32
    "pto.vecscope"() ({
      %zero = "pto.vdup"(%f0) : (f32) -> vector<64xf32>
      %result:3 = scf.for %iv = %c0 to %c5 step %c1
          iter_args(%s0 = %zero, %s1 = %zero, %p = %ptr)
          -> (vector<64xf32>, vector<64xf32>, !pto.ptr<f32, ub>) {
        %a = "pto.vlds"(%p) : (!pto.ptr<f32, ub>) -> vector<64xf32>
        %x = "pto.vadd"(%s0, %a) : (vector<64xf32>, vector<64xf32>) -> vector<64xf32>
        %y = "pto.vadd"(%s1, %a) : (vector<64xf32>, vector<64xf32>) -> vector<64xf32>
        %next = "pto.addptr"(%p, %c1) : (!pto.ptr<f32, ub>, index) -> !pto.ptr<f32, ub>
        scf.yield %x, %y, %next : vector<64xf32>, vector<64xf32>, !pto.ptr<f32, ub>
      } {pto.vmi.loop_fused, pto.vmi.loop_fusion.id = 0 : i64}
      "pto.vsts"(%result#0, %ptr) : (vector<64xf32>, !pto.ptr<f32, ub>) -> ()
      "pto.vsts"(%result#1, %ptr) : (vector<64xf32>, !pto.ptr<f32, ub>) -> ()
      "pto.yield"() : () -> ()
    }) : () -> ()
    return
  }
})mlir";

void testVmiCarry(mlir::MLIRContext &context) {
  auto module = parse(context, kVmiCarry);
  module->walk([&](mlir::scf::ForOp loop) {
    std::string reason;
    auto vf = vfsim::lowerVmiScope(loop->getParentOp(), loop.getOperation(),
                                   vfsim::VmiUnrollMode::Aabbcc, 2, reason);
    require(vf.has_value(), reason.c_str());
    auto accepted = vfsim::InputAPI::fromCanonical(std::move(*vf));
    const auto &main =
        *std::get<std::shared_ptr<const vfsim::CanonicalLoop>>(accepted.context.at(1).payload);
    require(main.carriedValues.size() == 4, "Reduction lanes are not independent");
    vfsim::CoreVfCostModel model({}, {});
    require(model.predictVfCycles(accepted) > 0, "Loop-carried prediction failed");
  });
  require(mlir::succeeded(vfsim::planVmiUnrollIR(module->getOperation(), {8, true})),
          "Loop-carried planner failed");
  module->walk([](mlir::scf::ForOp loop) {
    require(loop->hasAttr("pto.vfsim.unroll_factor"), "Loop-carried plan missing");
  });
}

size_t countStores(const std::vector<vfsim::CanonicalNode> &nodes) {
  size_t stores = 0;
  for (const auto &node : nodes) {
    if (const auto *inst = std::get_if<vfsim::CanonicalInstruction>(&node.payload)) {
      stores += inst->instructionClass == vfsim::CanonicalInstructionClass::Store;
    } else if (const auto *loop =
                   std::get_if<std::shared_ptr<const vfsim::CanonicalLoop>>(&node.payload)) {
      stores += countStores((*loop)->body);
    }
  }
  return stores;
}

void testVmi(mlir::MLIRContext &context, int64_t tripCount) {
  auto module = parse(context, kVmi);
  mlir::Builder builder(&context);
  module->walk([&](mlir::arith::ConstantOp constant) {
    auto value = constant->getAttrOfType<mlir::IntegerAttr>("value");
    if (value && value.getInt() == 5) {
      constant->setAttr("value", builder.getIndexAttr(tripCount));
    }
  });
  module->walk([&](mlir::scf::ForOp loop) {
    loop->setAttr("pto.unroll_factor", builder.getI32IntegerAttr(3));
    std::vector<std::vector<std::string>> orders;
    vfsim::ParamDB db;
    for (auto mode : {vfsim::VmiUnrollMode::Abcabc, vfsim::VmiUnrollMode::Aabbcc}) {
      std::string reason;
      auto vf = vfsim::lowerVmiScope(loop->getParentOp(), loop.getOperation(), mode, 2, reason);
      require(vf.has_value(), reason.c_str());
      auto accepted = vfsim::InputAPI::fromCanonical(std::move(*vf));
      auto program = vfsim::prepareProgram(accepted, db, db.uarch());
      std::vector<std::string> order;
      for (const auto &inst : program.instructions) {
        if (inst.type == "inst") {
          order.push_back(inst.op);
        }
      }
      require(order.size() == static_cast<size_t>(3 * tripCount),
              "Unrolling lost or duplicated tail instructions");
      orders.push_back(std::move(order));
    }
    require(orders[0] != orders[1], "ABCABC and AABBCC produced the same instruction order");
  });
  vfsim::PlannerOptions options;
  options.maxUnrollFactor = 99;
  options.dumpCandidates = true;
  require(mlir::succeeded(vfsim::planVmiUnrollIR(module->getOperation(), options)),
          "VMI planner failed");
  module->walk([&](mlir::scf::ForOp loop) {
    auto factor = loop->getAttrOfType<mlir::IntegerAttr>("pto.vfsim.unroll_factor");
    require(factor && factor.getType().isInteger(32), "Missing i32 VMI plan");
    require(factor.getInt() >= 1 && factor.getInt() <= std::min<int64_t>(8, tripCount),
            "Invalid VMI factor");
    require(loop->getAttrOfType<mlir::IntegerAttr>("pto.unroll_factor").getInt() == 3,
            "Planner overwrote manual attr");
  });
}

void testTile(mlir::MLIRContext &context, int64_t depth) {
  auto module = parse(context, kTile);
  mlir::Builder builder(&context);
  llvm::SmallVector<vfsim::PlannedTileOpIR> ops;
  module->walk([&](mlir::Operation *op) {
    auto order = op->getAttrOfType<mlir::IntegerAttr>("pto.fusion.order");
    if (!order) {
      return;
    }
    const auto ordinary = builder.getDictionaryAttr(
        {builder.getNamedAttr("name", builder.getStringAttr("ordinary")),
         builder.getNamedAttr("loop_depth", builder.getI64IntegerAttr(depth))});
    auto candidates = op->getAttrOfType<mlir::ArrayAttr>("candidates");
    op->setAttr("candidates", builder.getArrayAttr({candidates[0], ordinary}));
    ops.push_back({op, order.getInt()});
  });
  auto lowered = vfsim::lowerTileGroupToCanonicalVfInfo(ops);
  require(lowered.supported(), lowered.unsupportedReason.c_str());
  require(lowered.selectedTemplate == "ordinary", "Selected a VMI template");
  require(lowered.unrollTripCount == 32, "Ignored compact valid_shape");
  require(countStores(lowered.vfInfo.context) == 2, "Missing intermediate store");
  require((depth == 1) == (lowered.traversal == vfsim::TileTraversal::OneDimensional),
          "Wrong template traversal");
  vfsim::CoreVfCostModel model({}, {});
  for (auto mode : {vfsim::TileUnrollOrder::Abcabc, vfsim::TileUnrollOrder::Aabbcc}) {
    auto candidate = vfsim::materializeTileUnrollCandidate(lowered, 2, mode);
    require(model.predictVfCycles(vfsim::InputAPI::fromCanonical(std::move(candidate))) > 0,
            "Tile candidate prediction failed");
  }
  require(mlir::succeeded(vfsim::planTileFusionIR(module->getOperation(), {8, true})),
          "Tile planner failed");
  for (const auto &op : ops) {
    auto row = op.op->getAttrOfType<mlir::IntegerAttr>("pto.fusion.row_unroll_factor");
    auto col = op.op->getAttrOfType<mlir::IntegerAttr>("pto.fusion.col_unroll_factor");
    require(row && col, "Missing TileOp plan");
    require(row.getType().isInteger(64) && col.getType().isInteger(64), "Invalid TileOp attr type");
    require(depth == 1 ? row.getInt() == 1 : col.getInt() == 1, "Wrong unroll dimension");
  }
}
} // namespace

int main() {
  try {
    mlir::MLIRContext context;
    size_t diagnosticCount = 0;
    mlir::ScopedDiagnosticHandler diagnostics(&context, [&](mlir::Diagnostic &diagnostic) {
      ++diagnosticCount;
      diagnostic.print(llvm::errs());
      llvm::errs() << '\n';
      return mlir::success();
    });
    context.allowUnregisteredDialects();
    context
        .loadDialect<mlir::arith::ArithDialect, mlir::func::FuncDialect, mlir::scf::SCFDialect>();
    require(mlir::failed(vfsim::planTileFusionIR(nullptr)), "Accepted null TileOp IR");
    require(mlir::failed(vfsim::planVmiUnrollIR(nullptr)), "Accepted null VMI IR");
    testVmi(context, 2);
    testVmi(context, 5);
    testVmi(context, 9);
    testVmiCarry(context);
    testTile(context, 1);
    testTile(context, 2);
    require(diagnosticCount == 0, "Supported adapter fixtures produced diagnostics");
    std::cout << "PTOAS TileOp and VMI adapters passed\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
