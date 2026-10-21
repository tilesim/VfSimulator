// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "tests/support.h"
// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "api/common/instruction_catalog.h"
#include "api/common/schema.h"

#include "api/common/uarch_validation.h"

#include "api/json/serialization.h"
#include "api/json/value.h"
#include "core/params/database.h"

#include <filesystem>
#include <fstream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

namespace {

using namespace vfsim;

bool hasDiagnostic(const CanonicalValidationResult &result,
                   const std::string &code) {
  for (const auto &diagnostic : result.diagnostics)
    if (diagnostic.code == code)
      return true;
  return false;
}

std::string readText(const std::filesystem::path &path) {
  std::ifstream stream(path);
  std::ostringstream text;
  text << stream.rdbuf();
  return text.str();
}

} // namespace

int main() {
  for (
      const auto *text :
      {R"({"schema_version":true,"context":[],"values":{}})",
       R"({"schema_version":1,"context":[],"values":{},"params":{"count":"2"}})"}) {
    bool rejected = false;
    try {
      decodeCanonicalVfInfoJson(json::parseString(text));
    } catch (const std::runtime_error &) {
      rejected = true;
    }
    if (!rejected)
      throw std::runtime_error(
          "Canonical JSON must reject numeric type coercion");
  }

  const auto &instructionCatalog = defaultInstructionCatalog();
  if (instructionCatalog.canonicalOpcode("vld") != "VLD" ||
      instructionCatalog.canonicalOpcode("vexpdif") != "VEXPDIF" ||
      instructionCatalog.specializeOpcode("vcvt", "f32_to_bf16") !=
          "VCVT_F32_TO_BF16" ||
      instructionCatalog.lookup("VSSTB") == nullptr ||
      instructionCatalog.lookup("VSSTB")->instructionClass !=
          CatalogInstructionClass::Store ||
      !instructionCatalog.lookup("VADD")->forms.count("b16") ||
      instructionCatalog.lookup("VADD")->signature != "binary" ||
      instructionCatalog.lookup("VADD")->operands.size() != 5 ||
      instructionCatalog.lookup("VADD")->operands[2].kind !=
          CatalogArgumentKind::Register ||
      !instructionCatalog.lookup("VADD")->operands[4].optional ||
      instructionCatalog.lookup("VADD")->operands[4].allowedValues.count(
          "MODE_ZEROING") != 1 ||
      instructionCatalog.lookup("VLDS")->operands[3].allowedValues.count(
          "NORM") != 1 ||
      instructionCatalog.lookup("VLDS")->callVariants.size() != 2 ||
      !instructionCatalog.lookup("VLDS")->operands[2].allowIntegerExpression ||
      instructionCatalog.lookup("VLDS")->operands[2].postUpdateDeltaEncoding !=
          "element_count" ||
      instructionCatalog.lookup("VLDS")->operands[3].allowIntegerExpression ||
      instructionCatalog.lookup("VSSTB")->operands[2].postUpdateDeltaEncoding !=
          "unsigned_bit_field" ||
      instructionCatalog.lookup("VSSTB")->operands[2].postUpdateDeltaBitWidth !=
          16 ||
      instructionCatalog.lookup("VSSTB")
              ->operands[2]
              .postUpdateDeltaUnitBytes != 32 ||
      instructionCatalog.lookup("VDUP")->operands[1].kind !=
          CatalogArgumentKind::Register ||
      instructionCatalog.lookup("VDUPS")->operands[1].kind !=
          CatalogArgumentKind::Scalar ||
      instructionCatalog.lookup("VSTUS")->alignStateOperation != "append" ||
      instructionCatalog.lookup("VSTAS")->alignStateOperation != "consume" ||
      instructionCatalog.lookup("VSTAS")->operands.front().kind !=
          CatalogArgumentKind::AlignState)
    throw std::runtime_error("native generated instruction catalog mismatch");

  auto packed = decodeCanonicalVfInfoJson(
      json::parseFile(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                      "VFtest/canonical/fixtures/v2_plt_packed_fp16.json"));
  if (!validateCanonicalVfInfo(packed).ok())
    throw std::runtime_error("PLT/packed fp16 fixture rejected");
  if (instructionCatalog.lookupMemoryMode("VSTS", "PK_B32")->ubTransferBytes !=
          128 ||
      instructionCatalog.lookupMemoryMode("VLDS", "UNPK_B16")->memorySpan !=
          128)
    throw std::runtime_error("Packed payload/transaction width mismatch");
  packed.values.at("count.0").dtype = "fp32";
  if (validateCanonicalVfInfo(packed).ok())
    throw std::runtime_error("PLT accepted floating-point element count");

  const auto sharedFixtureJson =
      json::parseFile(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                      "VFtest/canonical/fixtures/v1_valid_loop.json");
  CanonicalVfInfo canonicalContract =
      decodeCanonicalVfInfoJson(sharedFixtureJson);
  if (!validateCanonicalVfInfo(canonicalContract).ok())
    throw std::runtime_error(
        "shared valid CanonicalVfInfo fixture was rejected");
  bool rejectedUnknownCanonicalField = false;
  try {
    (void)decodeCanonicalVfInfoJson(json::parseString(
        R"({"schema_version":1,"context":[],"values":{},"unexpected":1})"));
  } catch (const std::runtime_error &) {
    rejectedUnknownCanonicalField = true;
  }
  if (!rejectedUnknownCanonicalField)
    throw std::runtime_error("native canonical JSON accepted an unknown field");
  CanonicalVfInfo missingAlignContract = canonicalContract;
  CanonicalLoop missingAlignLoop =
      *std::get<std::shared_ptr<const CanonicalLoop>>(
          missingAlignContract.context.front().payload);
  CanonicalInstruction missingAlignStore =
      std::get<CanonicalInstruction>(missingAlignLoop.body.back().payload);
  missingAlignStore.opcode = "VSTAS";
  missingAlignStore.inputs.clear();
  missingAlignStore.attributes.clear();
  missingAlignLoop.body.back() =
      CanonicalNode::makeInstruction(std::move(missingAlignStore));
  missingAlignContract.context.front() =
      CanonicalNode::makeLoop(std::move(missingAlignLoop));
  if (!hasDiagnostic(validateCanonicalVfInfo(missingAlignContract),
                     "catalog_align_state_mismatch"))
    throw std::runtime_error(
        "native validator accepted VSTAS without align-state attributes");
  const auto sharedCarriedJson =
      json::parseFile(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                      "VFtest/canonical/fixtures/v1_valid_loop_carried.json");
  CanonicalVfInfo canonicalCarriedContract =
      decodeCanonicalVfInfoJson(sharedCarriedJson);
  if (!validateCanonicalVfInfo(canonicalCarriedContract).ok())
    throw std::runtime_error("shared valid loop-carried fixture was rejected");
  CanonicalVfInfo aliasBackEdgeContract = canonicalCarriedContract;
  const auto aliasLoop = std::get<std::shared_ptr<const CanonicalLoop>>(
      aliasBackEdgeContract.context.front().payload);
  const std::string aliasBackEdgeId =
      aliasLoop->carriedValues.front().backEdgeValueId;
  aliasBackEdgeContract.values.at(aliasBackEdgeId).logicalId = "alias.source";
  if (!validateCanonicalVfInfo(aliasBackEdgeContract).ok())
    throw std::runtime_error(
        "native validator rejected a type-compatible alias back-edge");
  aliasBackEdgeContract.values.at(aliasBackEdgeId).dtype = "fp16";
  if (!hasDiagnostic(validateCanonicalVfInfo(aliasBackEdgeContract),
                     "loop_carried_type_mismatch"))
    throw std::runtime_error(
        "native validator accepted an alias back-edge type mismatch");
  CanonicalVfInfo invariantBackEdgeContract = canonicalCarriedContract;
  CanonicalLoop invariantLoop = *std::get<std::shared_ptr<const CanonicalLoop>>(
      invariantBackEdgeContract.context.front().payload);
  invariantLoop.carriedValues.front().backEdgeValueId = "rhs.entry";
  invariantBackEdgeContract.context.front() =
      CanonicalNode::makeLoop(std::move(invariantLoop));
  if (!validateCanonicalVfInfo(invariantBackEdgeContract).ok())
    throw std::runtime_error(
        "native validator rejected an invariant alias back-edge");

  const ParamDB db(std::filesystem::path(VFSIM_SOURCE_ROOT));
  const auto canonicalOutDir =
      std::filesystem::temp_directory_path() / "vfsim_native_canonical_api";
  const SimulationResult canonicalResult =
      runProgram(canonicalContract, db, canonicalOutDir.string());
  if (canonicalResult.cyclesExecuted != 35 || canonicalResult.vfEndCycle != 47)
    throw std::runtime_error(
        "native canonical loop result differs from expected timing: cycles=" +
        std::to_string(canonicalResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalResult.vfEndCycle));
  const std::string canonicalHistory =
      readText(canonicalOutDir / "sim_history.json");
  const auto historyRows = json::parseString(canonicalHistory).asArray();
  const auto &identity = historyRows.front().asObject();
  const auto &iteration =
      identity.at("iteration_path").asArray().front().asObject();
  if (identity.at("static_instruction_id").asString() != "inst.load" ||
      iteration.at("loop_id").asString() != "loop.row" ||
      iteration.at("iteration").asInt() != 0 ||
      iteration.at("induction_variable").asString() != "i" ||
      identity.at("stream_seq").asInt() != 0)
    throw std::runtime_error(
        "Canonical dynamic identity was not preserved in history");
  const SimulationResult canonicalCarriedResult =
      runProgram(canonicalCarriedContract, db);
  if (canonicalCarriedResult.cyclesExecuted != 40 ||
      canonicalCarriedResult.vfEndCycle != 52)
    throw std::runtime_error(
        "native canonical loop-carried result differs from expected timing: "
        "cycles=" +
        std::to_string(canonicalCarriedResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalCarriedResult.vfEndCycle));
  const auto topBlockJson =
      json::parseFile(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                      "VFtest/canonical/fixtures/v1_valid_top_blocks.json");
  const CanonicalVfInfo topBlockContract =
      decodeCanonicalVfInfoJson(topBlockJson);
  const PreparedProgram topBlockRuntime =
      prepareProgram(topBlockContract, db, db.uarch());
  if (topBlockRuntime.totalTopBlocks != 2 ||
      topBlockRuntime.instructions.size() != 4 ||
      topBlockRuntime.instructions[0].topBlockId != 0 ||
      topBlockRuntime.instructions[1].topBlockId != 0 ||
      !topBlockRuntime.instructions[1].isLastInTopBlock ||
      topBlockRuntime.instructions[2].topBlockId != 0 ||
      topBlockRuntime.instructions[3].topBlockId != 1 ||
      !topBlockRuntime.instructions[3].isLastInTopBlock)
    throw std::runtime_error(
        "native canonical top-level epilogue block assignment is invalid");
  const SimulationResult topBlockResult =
      runProgram(topBlockContract, db, {}, 1000);
  if (topBlockResult.vfEndCycle <= 0 || topBlockResult.cyclesExecuted >= 1000)
    throw std::runtime_error(
        "native canonical loop-epilogue-membar-loop did not complete");
  const auto emptyTopBlockJson = json::parseFile(
      std::filesystem::path(VFSIM_SOURCE_ROOT) /
      "VFtest/canonical/fixtures/v1_valid_empty_top_block.json");
  const CanonicalVfInfo emptyTopBlockContract =
      decodeCanonicalVfInfoJson(emptyTopBlockJson);
  const PreparedProgram emptyTopBlockRuntime =
      prepareProgram(emptyTopBlockContract, db, db.uarch());
  if (emptyTopBlockRuntime.totalTopBlocks != 2 ||
      emptyTopBlockRuntime.emptyTopBlocks.size() != 1 ||
      !emptyTopBlockRuntime.emptyTopBlocks.count(0) ||
      emptyTopBlockRuntime.instructions.size() != 1 ||
      emptyTopBlockRuntime.instructions.front().topBlockId != 1)
    throw std::runtime_error(
        "native canonical zero-iteration top-block metadata is invalid");
  const SimulationResult emptyTopBlockResult =
      runProgram(emptyTopBlockContract, db, {}, 1000);
  if (emptyTopBlockResult.cyclesExecuted != 33 ||
      emptyTopBlockResult.vfEndCycle != 45)
    throw std::runtime_error("native canonical zero-iteration top block "
                             "differs from expected timing");
  CanonicalVfInfo canonicalUnrolledContract = canonicalCarriedContract;
  CanonicalLoop unrolledLoop = *std::get<std::shared_ptr<const CanonicalLoop>>(
      canonicalUnrolledContract.context.front().payload);
  unrolledLoop.unroll = int64_t{2};
  canonicalUnrolledContract.context.front() =
      CanonicalNode::makeLoop(std::move(unrolledLoop));
  const SimulationResult canonicalUnrolledResult =
      runProgram(canonicalUnrolledContract, db);
  if (canonicalUnrolledResult.cyclesExecuted != 40 ||
      canonicalUnrolledResult.vfEndCycle != 52)
    throw std::runtime_error(
        "native canonical structured unroll differs from expected timing: "
        "cycles=" +
        std::to_string(canonicalUnrolledResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalUnrolledResult.vfEndCycle));
  CanonicalVfInfo canonicalMembarUnrollContract = canonicalCarriedContract;
  CanonicalLoop membarUnrolledLoop =
      *std::get<std::shared_ptr<const CanonicalLoop>>(
          canonicalMembarUnrollContract.context.front().payload);
  membarUnrolledLoop.unroll = int64_t{2};
  CanonicalMembar loopMembar;
  loopMembar.instructionId = "membar.loop";
  loopMembar.barrier = "VST_VLD";
  membarUnrolledLoop.body.push_back(
      CanonicalNode::makeMembar(std::move(loopMembar)));
  canonicalMembarUnrollContract.context.front() =
      CanonicalNode::makeLoop(std::move(membarUnrolledLoop));
  const ParamDB warningDb(std::filesystem::path(VFSIM_SOURCE_ROOT));
  (void)runProgram(canonicalMembarUnrollContract, warningDb);
  bool sawMembarUnrollWarning = false;
  for (const auto &warning : warningDb.warnings())
    sawMembarUnrollWarning =
        sawMembarUnrollWarning || warning.kind == "membar_unroll_disabled";
  if (!sawMembarUnrollWarning)
    throw std::runtime_error(
        "native canonical membar unroll fallback did not record warning");
  CanonicalVfInfo canonicalUarchContract = canonicalContract;
  canonicalUarchContract.uarch["vreg_num"] = int64_t{1};
  const SimulationResult canonicalUarchResult =
      runProgram(canonicalUarchContract, db);
  if (canonicalUarchResult.cyclesExecuted != 68 ||
      canonicalUarchResult.vfEndCycle != 80)
    throw std::runtime_error(
        "native canonical uarch override differs from expected timing: "
        "cycles=" +
        std::to_string(canonicalUarchResult.cyclesExecuted) +
        ", end=" + std::to_string(canonicalUarchResult.vfEndCycle));
  for (const auto *field :
       {"load_done_latency", "consumer_release_start_offset_by_op",
        "three_ports_mode"}) {
    auto invalid = canonicalContract;
    invalid.uarch[field] = int64_t{1};
    if (!hasDiagnostic(validateCanonicalVfInfo(invalid), "unknown_uarch_field"))
      throw std::runtime_error("Unsupported field accepted");
  }
  for (const auto &[name, value] :
       std::vector<std::pair<std::string, CanonicalScalar>>{
           {"issue_ports", std::string("2")},
           {"issue_ports", true},
           {"shq_exq_dispatch_policy", int64_t{1}},
           {"shq_exq_dispatch_policy", false},
       }) {
    CanonicalVfInfo invalidUarchTypeContract = canonicalContract;
    invalidUarchTypeContract.uarch[name] = value;
    if (!hasDiagnostic(validateCanonicalVfInfo(invalidUarchTypeContract),
                       "uarch_field_type_mismatch"))
      throw std::runtime_error(
          "native validator accepted wrong uarch field type: " + name);
  }
  auto limited = canonicalContract;
  limited.uarch["canonical_dynamic_instruction_limit"] = int64_t{1};
  if (!validateCanonicalVfInfo(limited).ok())
    throw std::runtime_error("Shared dynamic limit field rejected");
  bool limitedExpansion = false;
  try {
    runProgram(limited, db);
  } catch (const std::runtime_error &e) {
    limitedExpansion = std::string(e.what()).find(
                           "dynamic instruction limit") != std::string::npos;
  }
  if (!limitedExpansion)
    throw std::runtime_error("Dynamic limit was not enforced");
  auto unknown = canonicalContract;
  unknown.uarch["unknown_uarch_field"] = int64_t{1};
  if (!hasDiagnostic(validateCanonicalVfInfo(unknown), "unknown_uarch_field"))
    throw std::runtime_error("Unknown override accepted");
  CanonicalVfInfo nonInnermostUnrollContract;
  CanonicalLoop outerLoop;
  outerLoop.loopId = "loop.outer";
  outerLoop.induction.variableId = "i";
  outerLoop.count = int64_t{2};
  outerLoop.unroll = int64_t{2};
  CanonicalLoop innerLoop;
  innerLoop.loopId = "loop.inner";
  innerLoop.induction.variableId = "j";
  innerLoop.count = int64_t{2};
  outerLoop.body.push_back(CanonicalNode::makeLoop(std::move(innerLoop)));
  nonInnermostUnrollContract.context.push_back(
      CanonicalNode::makeLoop(std::move(outerLoop)));
  bool rejectedNonInnermostUnroll = false;
  try {
    (void)runProgram(nonInnermostUnrollContract, db);
  } catch (const std::runtime_error &error) {
    rejectedNonInnermostUnroll =
        std::string(error.what()).find("non-innermost") != std::string::npos;
  }
  if (!rejectedNonInnermostUnroll)
    throw std::runtime_error(
        "native canonical non-innermost unroll was not rejected");

  CanonicalVfInfo ghostContract = canonicalContract;
  CanonicalValue ghostValue;
  ghostValue.definitionId = "ghost.0";
  ghostValue.logicalId = "ghost";
  ghostValue.storage = CanonicalStorageKind::Register;
  ghostValue.dtype = "fp32";
  ghostValue.producerNodeId = "inst.load";
  ghostContract.values.emplace(ghostValue.definitionId, std::move(ghostValue));
  if (!hasDiagnostic(validateCanonicalVfInfo(ghostContract),
                     "producer_definition_not_emitted"))
    throw std::runtime_error("native ghost definition was not diagnosed");

  CanonicalVfInfo classContract = canonicalContract;
  const auto originalLoop = std::get<std::shared_ptr<const CanonicalLoop>>(
      classContract.context.front().payload);
  CanonicalLoop computeLoop = *originalLoop;
  CanonicalInstruction computeWithMemory =
      std::get<CanonicalInstruction>(computeLoop.body.front().payload);
  computeWithMemory.instructionClass = CanonicalInstructionClass::Compute;
  computeLoop.body.front() =
      CanonicalNode::makeInstruction(std::move(computeWithMemory));
  classContract.context.front() =
      CanonicalNode::makeLoop(std::move(computeLoop));
  if (!hasDiagnostic(validateCanonicalVfInfo(classContract),
                     "instruction_class_memory_access_mismatch"))
    throw std::runtime_error("native class/access mismatch was not diagnosed");

  CanonicalVfInfo catalogContract = canonicalContract;
  CanonicalLoop catalogLoop = *originalLoop;
  CanonicalInstruction catalogMismatch =
      std::get<CanonicalInstruction>(catalogLoop.body.front().payload);
  catalogMismatch.opcode = "VEXPDIF";
  catalogMismatch.form = "bf16";
  catalogLoop.body.front() =
      CanonicalNode::makeInstruction(std::move(catalogMismatch));
  catalogContract.context.front() =
      CanonicalNode::makeLoop(std::move(catalogLoop));
  const auto catalogResult = validateCanonicalVfInfo(catalogContract);
  if (!hasDiagnostic(catalogResult, "catalog_instruction_class_mismatch") ||
      !hasDiagnostic(catalogResult, "catalog_instruction_form_mismatch"))
    throw std::runtime_error(
        "native Catalog semantics mismatch was not diagnosed");

  if (CanonicalInstruction{}.instructionClass !=
          CanonicalInstructionClass::Unknown ||
      CanonicalOperand{}.role != CanonicalOperandRole::Unknown ||
      CanonicalMemoryAccess{}.accessKind != CanonicalAccessKind::Unknown ||
      CanonicalStorageObject{}.storage != CanonicalStorageKind::Unknown ||
      CanonicalValue{}.storage != CanonicalStorageKind::Unknown)
    throw std::runtime_error("native required enum defaults must be Unknown");
  CanonicalVfInfo missingClassContract;
  CanonicalInstruction missingClass;
  missingClass.instructionId = "inst.missing_class";
  missingClass.opcode = "VUNKNOWN";
  missingClass.form = "fp32";
  missingClassContract.context.push_back(
      CanonicalNode::makeInstruction(std::move(missingClass)));
  if (!hasDiagnostic(validateCanonicalVfInfo(missingClassContract),
                     "missing_instruction_class"))
    throw std::runtime_error(
        "native missing instruction class was not diagnosed");

  using LoopPtr = std::variant_alternative_t<1, CanonicalNode::Payload>;
  static_assert(std::is_const_v<typename LoopPtr::element_type>,
                "canonical loop payload must be immutable when shared");

  CanonicalVfInfo invalidContract = canonicalContract;
  CanonicalMembar unsupportedMembar;
  unsupportedMembar.instructionId = "membar.invalid";
  unsupportedMembar.barrier = "";
  invalidContract.context.push_back(
      CanonicalNode::makeMembar(std::move(unsupportedMembar)));
  const auto invalidResult = validateCanonicalVfInfo(invalidContract);
  if (invalidResult.ok() ||
      invalidResult.diagnostics.back().code != "missing_membar_type")
    throw std::runtime_error("missing native Membar type was not diagnosed");

  const auto sharedInvalidJson =
      json::parseFile(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                      "VFtest/canonical/fixtures/v1_invalid_loop_scope.json");
  const auto sharedInvalidResult =
      validateCanonicalVfInfo(decodeCanonicalVfInfoJson(sharedInvalidJson));
  if (sharedInvalidResult.ok() || sharedInvalidResult.diagnostics.size() != 1 ||
      sharedInvalidResult.diagnostics.front().code !=
          "loop_back_edge_out_of_scope")
    throw std::runtime_error(
        "shared invalid fixture did not match expected diagnostic");

  CanonicalVfInfo nullLoopContract;
  nullLoopContract.context.push_back(
      CanonicalNode{std::shared_ptr<const CanonicalLoop>{}});
  const auto nullLoopResult = validateCanonicalVfInfo(nullLoopContract);
  if (nullLoopResult.ok() ||
      nullLoopResult.diagnostics.front().code != "invalid_loop_payload")
    throw std::runtime_error("null native loop payload was not diagnosed");

  CanonicalVfInfo hugeCountContract;
  CanonicalLoop hugeCountLoop;
  hugeCountLoop.loopId = "loop.huge";
  hugeCountLoop.induction.variableId = "j";
  hugeCountLoop.count = "999999999999999999999999999999";
  hugeCountContract.context.push_back(
      CanonicalNode::makeLoop(std::move(hugeCountLoop)));
  const auto hugeCountResult = validateCanonicalVfInfo(hugeCountContract);
  if (hugeCountResult.ok() ||
      hugeCountResult.diagnostics.front().code != "unresolved_parameter")
    throw std::runtime_error("oversized loop count was not diagnosed");

  CanonicalVfInfo nonFiniteContract;
  nonFiniteContract.uarch.emplace("invalid",
                                  std::numeric_limits<double>::infinity());
  const auto nonFiniteResult = validateCanonicalVfInfo(nonFiniteContract);
  if (nonFiniteResult.ok() ||
      nonFiniteResult.diagnostics.front().code != "invalid_scalar_attribute")
    throw std::runtime_error("non-finite native scalar was not diagnosed");

  // Removed Canonical fields are rejected even when their value is empty.
  for (const auto &node : std::vector<json::Value::Object>{
           {{"kind", "instruction"},
            {"instruction_id", "removed.inst"},
            {"opcode", "VUNKNOWN"},
            {"instruction_class", "compute"},
            {"form", "fp32"},
            {"dependencies", json::Value::Array{}}},
           {{"kind", "membar"},
            {"instruction_id", "removed.membar"},
            {"barrier", "VST_VLD"},
            {"dependencies", json::Value::Array{}}},
       }) {
    bool rejected = false;
    try {
      decodeCanonicalVfInfoJson(
          json::Value::Object{{"schema_version", int64_t{1}},
                              {"context", json::Value::Array{node}},
                              {"values", json::Value::Object{}}});
    } catch (const std::runtime_error &error) {
      rejected =
          std::string(error.what()).find("unknown field: dependencies") !=
          std::string::npos;
    }
    if (!rejected)
      throw std::runtime_error(
          "Removed Canonical dependencies field was accepted");
  }
  return 0;
}
