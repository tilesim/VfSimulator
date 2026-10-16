// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "tests/support.h"
#include <iostream>
namespace {
void require(bool condition, const char *message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}
} // namespace
int main() {
  try {
    using namespace vfsim;
    auto vf = InputAPI::loadJson(std::filesystem::path(VFSIM_SOURCE_ROOT) /
                                 "tests/fixtures/canonical_vf_info/v1_valid_loop.json");
    auto view = vf.values.at("acc.0");
    view.definitionId = "acc.view";
    view.dtype = "b32";
    vf.values.emplace(view.definitionId, view);
    auto output = view;
    output.definitionId = "result";
    output.logicalId = "result";
    output.producerNodeId = "inst.consume";
    vf.values.emplace(output.definitionId, output);
    CanonicalInstruction consume;
    consume.instructionId = "inst.consume";
    consume.opcode = "VOR";
    consume.form = "b32";
    consume.instructionClass = CanonicalInstructionClass::Compute;
    CanonicalOperand input;
    input.valueId = view.definitionId;
    input.dtype = view.dtype;
    input.role = CanonicalOperandRole::Source;
    consume.inputs = {input, input};
    CanonicalOperand result;
    result.valueId = output.definitionId;
    result.dtype = output.dtype;
    result.role = CanonicalOperandRole::Destination;
    consume.outputs = {result};
    auto loop = *std::get<std::shared_ptr<const CanonicalLoop>>(vf.context[0].payload);
    loop.body.push_back(CanonicalNode::makeInstruction(consume));
    vf.context[0] = CanonicalNode::makeLoop(loop);
    vf = InputAPI::fromCanonical(std::move(vf));

    ParamDB db;
    auto program = prepareProgram(vf, db, db.uarch());
    std::string latestLoad;
    size_t consumes = 0;
    for (const auto &inst : program.instructions) {
      if (inst.op == "VLDS") {
        latestLoad = inst.dst.at(0);
      } else if (inst.op == "VOR") {
        ++consumes;
        require(inst.src == std::vector<std::string>({latestLoad, latestLoad}),
                "Bit view lost the dynamic producer dependency");
      }
    }
    require(consumes == 4, "Bit view created or removed instructions");
    require(runProgram(vf, db).vfEndCycle > 0, "Bit view simulation failed");

    auto invalid = vf;
    invalid.values.at("acc.view").shape = {32};
    require(!validateCanonicalVfInfo(invalid).ok(), "Accepted unequal bit widths");
    invalid = vf;
    invalid.values.at("acc.view").logicalId = "unrelated";
    require(!validateCanonicalVfInfo(invalid).ok(), "Accepted unrelated logical IDs");
    invalid = vf;
    invalid.values.at("acc.view").producerNodeId = "unknown";
    require(!validateCanonicalVfInfo(invalid).ok(), "Accepted an unknown producer");

    auto external = vf.values.at("acc.0");
    external.definitionId = "external";
    external.logicalId = "external";
    external.producerNodeId.reset();
    vf.values.emplace(external.definitionId, external);
    vf.values.at("acc.view").logicalId = "external";
    vf.values.at("acc.view").producerNodeId.reset();
    vf = InputAPI::fromCanonical(std::move(vf));
    program = prepareProgram(vf, db, db.uarch());
    for (const auto &inst : program.instructions) {
      if (inst.op == "VOR") {
        require(inst.src == std::vector<std::string>({"external", "external"}),
                "External bit views do not share the source register");
      }
    }
    std::cout << "Register bit views preserve dynamic dependencies\n";
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
