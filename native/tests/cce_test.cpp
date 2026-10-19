// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0

#include "native/api/cce/affine.h"
#include "native/api/cce/source.h"
#include "native/api/input_api.h"
#include "native/api/json/serialization.h"
#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <stdexcept>

namespace {
using namespace vfsim;

void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

class Inputs {
public:
  Inputs() {
    root = std::filesystem::temp_directory_path() /
           ("vfsim_cce_test_" +
            std::to_string(
                std::chrono::steady_clock::now().time_since_epoch().count()));
    std::filesystem::create_directories(root);
  }
  ~Inputs() {
    std::error_code ignored;
    std::filesystem::remove_all(root, ignored);
  }
  CanonicalVfInfo
  parse(const std::string &source, const std::string &kernel = {},
        const std::unordered_map<std::string, int64_t> &params = {}) {
    const auto path = root / (std::to_string(serial++) + ".cce");
    {
      std::ofstream file(path);
      file << source;
    }
    return InputAPI::loadCce(path, kernel, params);
  }
  void rejects(const std::string &source, const std::string &message) {
    try {
      parse(source);
    } catch (const std::exception &error) {
      require(std::string(error.what()).find(message) != std::string::npos,
              "Unexpected CCE error: " + std::string(error.what()));
      return;
    }
    throw std::runtime_error("CCE input must be rejected: " + source);
  }
  std::filesystem::path root;

private:
  int serial = 0;
};

const CanonicalInstruction &inst(const CanonicalNode &node) {
  return std::get<CanonicalInstruction>(node.payload);
}
const CanonicalLoop &loop(const CanonicalNode &node) {
  return *std::get<std::shared_ptr<const CanonicalLoop>>(node.payload);
}
const CanonicalLoopCarriedValue &carried(const CanonicalLoop &l,
                                         const std::string &logical) {
  for (const auto &value : l.carriedValues)
    if (value.logicalId == logical)
      return value;
  throw std::runtime_error("Missing loop-carried value " + logical);
}
std::string scope(const std::string &body) {
  return "void vf(__ubuf__ float *p, __ubuf__ float *q) { __VEC_SCOPE__ {"
         "vector_f32 a,b,c; vector_bool mask,pred;" +
         body + "}}";
}

void sourceAndLoops(Inputs &in) {
  const auto vf = in.parse(
      "// source line\nvoid first(){__VEC_SCOPE__{}}\n"
      "void second(uint32_t N){__VEC_SCOPE__{\nvector_f32 a,b;vector_bool "
      "mask=pset_b32(PAT_ALL);\n"
      "#pragma unroll 2\nfor(uint32_t i=2;i<=N;i+=2){vadd(a,b,b,mask);}}}",
      "second", {{"N", 8}});
  const auto &l = loop(vf.context.at(1));
  require(std::get<int64_t>(l.count) == 4 && std::get<int64_t>(l.unroll) == 2 &&
              std::get<int64_t>(l.induction.start) == 2 &&
              std::get<int64_t>(l.induction.step) == 2,
          "CCE loop induction/count/unroll contract");
  require(l.sourceLocation->line == 6 &&
              inst(l.body[0]).sourceLocation->line == 6,
          "CCE source line identity must survive scanning");
  in.rejects("void a(){__VEC_SCOPE__{}}void b(){__VEC_SCOPE__{}}", "Multiple");
  in.rejects(scope("while(1){vadd(a,b,c);}"), "Unsupported CCE statement");
}

void catalogBinding(Inputs &in) {
  auto vf =
      in.parse(scope("mask=pset_b32(PAT_ALL);vlds(a,p,64,NORM,POST_UPDATE);"
                     "vdup(b,a,mask,POS_LOWEST,MODE_ZEROING);vaxpy(b,a,2.0f,"
                     "mask,MODE_ZEROING);"));
  require(inst(vf.context[3]).inputs[0].role == CanonicalOperandRole::Source &&
              inst(vf.context[3]).outputs[0].role ==
                  CanonicalOperandRole::Destination,
          "Read-write operands must have direction-specific roles");
  in.rejects(scope("vadd(a,b);"), "missing");
  in.rejects(scope("mask=pset_b32(PAT_ALL);vdup(a,b,mask,POS_LOWEST);"),
             "variant");
  in.rejects(scope("vlds(a,p,0,123);"), "configuration");
}

void valueAliases(Inputs &in) {
  auto vf =
      in.parse(scope("mask=pset_b32(PAT_ALL);vdup(a,1.0f,mask,MODE_ZEROING);"
                     "b=a;vdup(a,2.0f,mask,MODE_ZEROING);vadd(c,b,a,mask);"));
  require(vf.context.size() == 4,
          "Register alias must not emit an instruction");
  const auto &sum = inst(vf.context[3]);
  require(sum.inputs[0].valueId == inst(vf.context[1]).outputs[0].valueId &&
              sum.inputs[1].valueId == inst(vf.context[2]).outputs[0].valueId &&
              sum.inputs[0].valueId != sum.inputs[1].valueId,
          "Register alias must snapshot the current definition");
  in.rejects("void vf(){__VEC_SCOPE__{vector_f16 a;vector_f32 b;b=a;}}",
             "matching");
}

void loopValueVersions(Inputs &in) {
  auto vf = in.parse(
      scope("mask=pset_b32(PAT_ALL);vadd(a,b,b,mask);for(int i=0;i<2;++i){"
            "for(int j=0;j<3;++j){vadd(a,a,b,mask);}}vadd(c,a,b,mask);"));
  const auto &outer = loop(vf.context[2]);
  const auto &inner = loop(outer.body[0]);
  require(carried(outer, "a").backEdgeValueId ==
                  carried(inner, "a").exitValueId &&
              inst(vf.context[3]).inputs[0].valueId ==
                  carried(outer, "a").exitValueId,
          "Nested loop exits must become enclosing back edges");
  auto first =
      in.parse(scope("mask=pset_b32(PAT_ALL);for(int "
                     "i=0;i<2;++i){vadd(a,b,b,mask);}vadd(c,a,b,mask);"));
  require(!carried(loop(first.context[1]), "a").entryValueId &&
              first.schemaVersion == 2,
          "First loop definition must use an exit-only carry");
  in.rejects(scope("mask=pset_b32(PAT_ALL);for(int "
                   "i=0;i<0;++i){vadd(a,b,b,mask);}vadd(c,a,b,mask);"),
             "undefined");
  auto alias = in.parse(scope("mask=pset_b32(PAT_ALL);vadd(b,a,a,mask);for(int "
                              "i=0;i<2;++i){vadd(c,b,a,mask);b=c;}"));
  require(carried(loop(alias.context[2]), "b").backEdgeValueId ==
              inst(loop(alias.context[2]).body[0]).outputs[0].valueId,
          "Alias back edge may reference a different logical value");
}

void affineAndAddresses(Inputs &in) {
  require(cce::parseAffine("-9223372036854775808", {}, {}).constant ==
              INT64_MIN,
          "Affine constants must preserve the full signed integer range");
  auto expression = cce::parseAffine("(i+j)*64+j*64-8", {}, {"i", "j"});
  require(expression.constant == -8 && expression.terms.size() == 2 &&
              expression.terms[0].coefficient == 64 &&
              expression.terms[1].coefficient == 128,
          "Affine normalization must distribute constant multiplication");
  auto vf = in.parse(scope(
      "__ubuf__ float *r=(__ubuf__ float *)(p+4);"
      "__ubuf__ float *s=r+2;vlds(a,s,1,NORM,POST_UPDATE);"
      "vlds(b,r,0,NORM);vlds(c,((__ubuf__ half *&)s),1,BRC_B16,POST_UPDATE);"));
  const auto &a = *inst(vf.context[0]).inputs[0].memoryAccess;
  const auto &b = *inst(vf.context[1]).inputs[0].memoryAccess;
  const auto &c = *inst(vf.context[2]).inputs[0].memoryAccess;
  require(a.baseObjectId == b.baseObjectId && a.offset.constant == 6 &&
              a.addressStateId != b.addressStateId &&
              a.addressStateId == c.addressStateId &&
              a.postUpdateDeltaBytes->constant == 4 &&
              c.postUpdateDeltaBytes->constant == 2,
          "UB aliases must retain stable bases and independent pointer states");
  in.rejects(scope("for(int i=0;i<2;++i){vlds(a,p,i*i,NORM);}"), "offset");
  in.rejects(scope("for(int i=0;i<2;++i){vlds(a,p,(i-i)*i,NORM);}"), "offset");
  in.rejects(scope("for(int i=0;i<2;++i){__ubuf__ float "
                   "*r=p;vlds(a,r,1,NORM,POST_UPDATE);}"),
             "loop-local");
}

void predicateValues(Inputs &in) {
  auto vf = in.parse(scope("mask=pset_b32(PAT_ALL);vlds(a,p,0,NORM);"
                           "vcmp_gt(pred,a,a,mask);vsel(b,a,a,pred);"
                           "vadd(c,a,b,pset_b32(PAT_ALL));"));
  require(vf.schemaVersion == 2 && vf.context.size() == 6 &&
              inst(vf.context[4]).opcode == "PSET_B32" &&
              inst(vf.context[5]).inputs.back().valueId ==
                  inst(vf.context[4]).outputs[0].valueId,
          "Inline predicate producers must become explicit data dependencies");
  in.rejects(scope("vsel(a,b,c,pred);"), "predicate_live_in_not_supported");
  in.rejects(scope("mask=pset_b32(PAT_ALL);vunknown(a,b,mask);"), "Predicate");
}

void alignState(Inputs &in) {
  auto vf = in.parse(
      scope("vector_align state;vldas(state,p);vldus(a,state,p,64,POST_UPDATE);"
            "vstus(state,1,a,q,POST_UPDATE);vstas(state,q,0,POST_UPDATE);"));
  const auto &first = inst(vf.context[0]);
  const auto &last = inst(vf.context[3]);
  require(std::get<std::string>(first.attributes.at("align_state_id")) ==
                  std::get<std::string>(last.attributes.at("align_state_id")) &&
              last.inputs.empty(),
          "Align state must use shared explicit state attributes");
  in.rejects(scope("vldus(a,state,p);"), "align");
}

void packedAndConversions(Inputs &in) {
  auto packedConfig =
      in.parse("void vf(__ubuf__ half *p){constexpr uint64_t packed="
               "(1ULL<<63)|(1ULL<<16)|1ULL;__VEC_SCOPE__{vector_f16 a;"
               "vector_bool "
               "mask=pset_b16(PAT_ALL);vsstb(a,p,packed,mask,POST_UPDATE);}}");
  require(
      inst(packedConfig.context[1])
              .outputs[0]
              .memoryAccess->postUpdateDeltaBytes->constant == 32,
      "Packed POST_UPDATE must decode unsigned 64-bit configuration values");
  auto vf = in.parse(scope("vlds(a,b,p,8,DINTLV_B32);"));
  const auto &load = inst(vf.context[0]);
  require(load.outputs.size() == 2 &&
              std::get<std::string>(load.attributes.at("catalog_mode")) ==
                  "DINTLV_B32" &&
              load.inputs[0].memoryAccess->span == 128,
          "Packed loads must retain multiple results and explicit spans");
  auto cvt = in.parse(
      "void vf(){__VEC_SCOPE__{vector_f32 a;vector_f16 b;"
      "vector_bool "
      "p=pset_b16(PAT_ALL);vcvt(b,a,p,ROUND_R,RS_DISABLE,PART_EVEN);}}");
  require(inst(cvt.context[1]).opcode == "VCVT_F32_TO_F16",
          "Conversion opcode specialization");
  in.rejects("void vf(){__VEC_SCOPE__{vector_f16 a,b;vector_bool "
             "p=pset_b16(PAT_ALL);vcvt(a,b,p,ROUND_R,RS_DISABLE,PART_EVEN);}}",
             "form");
  in.rejects(scope("vlds(a,a,p,8,DINTLV_B32);"), "distinct");
}

json::Value normalizeSourcePaths(const json::Value &value) {
  if (value.isObject()) {
    json::Value::Object out;
    for (const auto &[key, child] : value.asObject())
      out.emplace(key, key == "path" ? json::Value("<cce>")
                                     : normalizeSourcePaths(child));
    return out;
  }
  if (value.isArray()) {
    json::Value::Array out;
    for (const auto &child : value.asArray())
      out.push_back(normalizeSourcePaths(child));
    return out;
  }
  return value;
}

void predicateMemoryAndMasks(Inputs &in) {
  auto vf = in.parse("void vf(__ubuf__ uint32_t *spill){__VEC_SCOPE__{"
                     "vector_bool p;__ubuf__ uint32_t *slot=spill+8;"
                     "plds(p,slot,-32,NORM);psts(p,slot,32,NORM);}}");
  const auto &load = inst(vf.context[0]);
  const auto &store = inst(vf.context[1]);
  require(load.inputs[0].memoryAccess->offset.constant == 0 &&
              store.outputs[0].memoryAccess->offset.constant == 64 &&
              load.inputs[0].memoryAccess->addressUnitBytes == 1 &&
              load.inputs[0].memoryAccess->span == 32,
          "Predicate spill pointer aliases and byte offsets must use distinct "
          "units");
  for (const auto *body :
       {"movp_b32(a);", "plds(a,p,0,NORM);", "plds(mask,p,0,NORM,POST_UPDATE);",
        "plds(mask,p,0,BRC_B32);"})
    in.rejects(scope(body), "");
}

void canonicalContractFixtures() {
  const auto root =
      std::filesystem::path(VFSIM_SOURCE_ROOT) / "VFtest/cce/contracts";
  for (const auto *name : {"mixed", "alias", "packed", "post_update", "align",
                           "conversion", "movp", "predicate_spill"}) {
    const auto vf = InputAPI::loadCce(root / (std::string(name) + ".cce"));
    const auto expected = json::parseFile(
        std::filesystem::path(VFSIM_SOURCE_ROOT) /
        "VFtest/canonical/cce_expected" / (std::string(name) + ".json"));
    require(normalizeSourcePaths(encodeCanonicalVfInfoJson(vf)) == expected,
            "CCE Canonical contract fixture mismatch: " + std::string(name));
  }
}
} // namespace

int main() {
  Inputs inputs;
  sourceAndLoops(inputs);
  catalogBinding(inputs);
  valueAliases(inputs);
  loopValueVersions(inputs);
  affineAndAddresses(inputs);
  predicateValues(inputs);
  alignState(inputs);
  packedAndConversions(inputs);
  predicateMemoryAndMasks(inputs);
  canonicalContractFixtures();
}
