// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "native/api/cce/normalization.h"
#include "native/api/cce/source.h"
#include "native/api/common/instruction_catalog.h"
#include <functional>
#include <stdexcept>
namespace vfsim::cce {
bool renameable(CanonicalStorageKind s) {
  return s == CanonicalStorageKind::Register ||
         s == CanonicalStorageKind::PredicateRegister;
}
AdapterProgram normalizeProgram(AdapterProgram program) {
  auto record = [&](AdapterValue &v) {
    v.dtype = normalizeDtype(v.dtype);
    auto it = program.values.find(v.valueId);
    if (it == program.values.end())
      program.values.emplace(v.valueId, v);
    else {
      auto &old = it->second;
      if (old.storage != v.storage ||
          (!old.dtype.empty() && !v.dtype.empty() && old.dtype != v.dtype) ||
          (!old.shape.empty() && !v.shape.empty() && old.shape != v.shape))
        throw std::runtime_error("Conflicting adapter value declaration: " +
                                 v.valueId);
      if (old.dtype.empty())
        old.dtype = v.dtype;
      if (old.shape.empty())
        old.shape = v.shape;
    }
  };
  std::function<void(std::vector<AdapterNode> &)> visit = [&](auto &nodes) {
    for (auto &n : nodes) {
      if (n.kind == AdapterNode::Kind::Loop) {
        visit(n.body);
        continue;
      }
      if (n.kind == AdapterNode::Kind::Membar) {
        n.name = normalizeMembar(n.name);
        continue;
      }
      for (auto &v : n.src)
        record(v);
      for (auto &v : n.dst)
        record(v);
      if (n.kind == AdapterNode::Kind::Alias)
        continue;
      n.name = defaultInstructionCatalog().canonicalOpcode(n.name);
      if (!n.form.empty())
        n.form = normalizeForm(n.form);
      else {
        std::string src, dst;
        for (const auto &v : n.src)
          if (!program.values.at(v.valueId).dtype.empty()) {
            src = program.values.at(v.valueId).dtype;
            break;
          }
        for (const auto &v : n.dst)
          if (!program.values.at(v.valueId).dtype.empty()) {
            dst = program.values.at(v.valueId).dtype;
            break;
          }
        n.form = !src.empty() && !dst.empty() && src != dst
                     ? compactDtype(src) + "_to_" + compactDtype(dst)
                     : (!dst.empty()   ? dst
                        : !src.empty() ? src
                                       : program.defaultDtype);
      }
    }
  };
  visit(program.context);
  for (auto &[id, v] : program.values)
    if (v.dtype.empty())
      v.dtype = program.defaultDtype;
  return program;
}
} // namespace vfsim::cce
