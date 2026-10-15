// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/cce/parser.h"
#include "api/cce/affine.h"
#include "api/cce/normalization.h"
#include "api/common/instruction_catalog.h"
#include <algorithm>
#include <cctype>
#include <regex>
#include <stdexcept>
#include <tuple>

namespace vfsim::cce {
namespace {
const std::regex scalarDeclaration(
    R"(^\s*(?:(?:const|constexpr|volatile|static)\s+)*(u?int(?:8|16|32|64)_t|size_t|unsigned(?:\s+(?:short|int|long))?|signed(?:\s+(?:short|int|long))?|short|int|long|float|double|half)\s+([\s\S]+?)\s*;\s*$)");
const std::regex pointerDeclaration(
    R"(^\s*(?:(?:const|volatile|static)\s+)*__ubuf__\s+([A-Za-z_]\w*)\s*\*\s*([A-Za-z_]\w*)\s*=\s*([\s\S]+?)\s*;\s*$)");
const std::regex
    vectorDeclaration(R"(^\s*vector_([A-Za-z0-9_]+)\s+([^;]+)\s*;?\s*$)");
const std::regex callExpression(R"(([A-Za-z_]\w*)\s*\(([\s\S]*)\)\s*;)");
const std::regex identifier(R"([A-Za-z_]\w*)");
bool begins(const std::string &s, const std::string &prefix) {
  return s.compare(0, prefix.size(), prefix) == 0;
}
std::string withoutSemicolon(std::string s) {
  s = trim(s);
  if (!s.empty() && s.back() == ';')
    s.pop_back();
  return trim(s);
}
bool integerDtype(const std::string &s) {
  return s != "float" && s != "double" && s != "half";
}
bool numericLiteral(std::string text) {
  text = std::regex_replace(
      trim(text),
      std::regex(
          R"(^\(\s*(?:u?int(?:8|16|32|64)_t|unsigned|signed|int)\s*\)\s*)"),
      "");
  if (!text.empty() && text.front() == '(')
    text.erase(0, 1);
  if (!text.empty() && text.back() == ')')
    text.pop_back();
  text = trim(text);
  return std::regex_match(
             text,
             std::regex(
                 R"([+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uU](?:[lL]{1,2})?|[lL]{1,2}[uU]?)?)")) ||
         std::regex_match(
             text,
             std::regex(
                 R"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFlL]?)"));
}
std::string castScalarDtype(const std::string &text) {
  std::smatch m;
  return std::regex_search(text, m,
                           std::regex(R"(^\(\s*(u?int(?:8|16|32|64))_t\s*\))"))
             ? normalizeDtype(m[1])
             : "";
}
CanonicalInstructionClass instructionClass(CatalogInstructionClass c) {
  switch (c) {
  case CatalogInstructionClass::Load:
    return CanonicalInstructionClass::Load;
  case CatalogInstructionClass::Store:
    return CanonicalInstructionClass::Store;
  case CatalogInstructionClass::Control:
    return CanonicalInstructionClass::Control;
  default:
    return CanonicalInstructionClass::Compute;
  }
}
std::string combine(const std::string &a, const std::string &b) {
  if (trim(a) == "0")
    return b;
  if (trim(b) == "0")
    return a;
  return "(" + a + ")+(" + b + ")";
}

struct Symbols {
  std::map<std::string, std::string> registers, align, scalarDtypes,
      initializers, ubDtypes, pointerStates;
  std::set<std::string> scalars, offsetScalars, ubNames, induction;
  std::map<std::string, std::pair<std::string, std::string>> ubAliases;
};
class ScopeParser {
  const VFScope &scope_;
  IntegerParams params_;
  Symbols symbols_;
  const InstructionCatalog &catalog_ = defaultInstructionCatalog();
  int64_t alignSerial_ = 0, pointerSerial_ = 0, predicateSerial_ = 0;
  std::set<std::string> updated_, allUpdated_, loopPointers_;
  std::map<std::string, std::string> loopPointerSources_;
  std::vector<AdapterNode> predicatePrefix_;
  CanonicalSourceLocation location(int64_t line) const {
    CanonicalSourceLocation l;
    l.source = scope_.kernelName;
    l.line = line;
    l.path = scope_.sourcePath;
    return l;
  }
  std::string newAlign(const std::string &name) {
    return scope_.kernelName + ":align:" + std::to_string(alignSerial_++) +
           ":" + name;
  }
  std::string newPointer(const std::string &name) {
    return scope_.kernelName + ":pointer:" + std::to_string(pointerSerial_++) +
           ":" + name;
  }
  AdapterValue typedRegister(const std::string &name) const {
    auto it = symbols_.registers.find(name);
    if (it == symbols_.registers.end())
      throw std::runtime_error("Expected declared vector register: " + name);
    return {name,
            it->second == "bool" ? CanonicalStorageKind::PredicateRegister
                                 : CanonicalStorageKind::Register,
            it->second,
            {}};
  }
  void vectorDecl(const std::string &stmt) {
    std::smatch m;
    if (!std::regex_match(stmt, m, vectorDeclaration))
      return;
    auto dtype = normalizeDtype(m[1]);
    for (const auto &item : splitArguments(m[2])) {
      std::smatch name;
      if (!std::regex_search(item, name, std::regex(R"(^\s*([A-Za-z_]\w*))")))
        continue;
      auto id = name[1].str();
      if (dtype == "align") {
        symbols_.registers.erase(id);
        symbols_.align[id] = newAlign(id);
      } else {
        symbols_.align.erase(id);
        symbols_.registers[id] = dtype;
      }
    }
  }
  void scalarDecl(const std::string &stmt) {
    std::smatch m;
    if (!std::regex_match(stmt, m, scalarDeclaration))
      throw std::runtime_error("Unsupported local scalar declaration: " + stmt);
    auto dtype = m[1].str();
    for (const auto &item : splitArguments(m[2])) {
      std::smatch d;
      if (!std::regex_match(
              item, d, std::regex(R"(([A-Za-z_]\w*)\s*(?:=\s*([\s\S]+))?)")))
        throw std::runtime_error("Unsupported local scalar declaration: " +
                                 stmt);
      auto name = d[1].str();
      symbols_.scalars.insert(name);
      symbols_.offsetScalars.erase(name);
      symbols_.scalarDtypes[name] = dtype;
      symbols_.initializers.erase(name);
      if (d[2].matched)
        symbols_.initializers[name] = trim(d[2]);
    }
  }
  IntegerParams integerConstants() const {
    auto result = params_;
    std::map<std::string, std::string> pending;
    for (const auto &[name, expr] : symbols_.initializers)
      if (integerDtype(symbols_.scalarDtypes.at(name)))
        pending.emplace(name, expr);
    while (!pending.empty()) {
      bool progress = false;
      for (auto it = pending.begin(); it != pending.end();) {
        auto value = evaluateInteger(it->second, result);
        if (value) {
          result[it->first] = *value;
          it = pending.erase(it);
          progress = true;
        } else
          ++it;
      }
      if (!progress)
        break;
    }
    return result;
  }
  std::optional<std::pair<std::string, std::string>>
  ubReference(const std::string &expr) const {
    auto value = stripUbWrappers(expr);
    std::smatch m;
    if (!std::regex_match(
            value, m, std::regex(R"(([A-Za-z_]\w*)(?:\s*\+\s*([\s\S]+))?)")))
      return std::nullopt;
    auto name = m[1].str();
    if (!symbols_.ubNames.count(name))
      return std::nullopt;
    auto base = symbols_.ubAliases.at(name);
    if (m[2].matched)
      base.second =
          combine(base.second, expandExpression(m[2], symbols_.initializers));
    return base;
  }
  void pointerDecl(const std::string &stmt) {
    std::smatch m;
    if (!std::regex_match(stmt, m, pointerDeclaration))
      throw std::runtime_error("Unsupported local UB pointer declaration: " +
                               stmt);
    auto name = m[2].str(), initializer = trim(m[3]),
         source = trim(stripUbWrappers(initializer)
                           .substr(0, stripUbWrappers(initializer).find('+')));
    auto state = symbols_.pointerStates.find(source);
    std::optional<std::string> sourceState;
    if (state != symbols_.pointerStates.end())
      sourceState = state->second;
    if (sourceState && updated_.count(*sourceState))
      throw std::runtime_error("Cannot snapshot an updated pointer alias: " +
                               stmt);
    auto reference = ubReference(initializer);
    if (!reference)
      throw std::runtime_error("Unsupported local UB pointer initializer: " +
                               stmt);
    symbols_.ubNames.insert(name);
    symbols_.ubAliases[name] = *reference;
    symbols_.ubDtypes[name] = scalarDtype(m[1]);
    symbols_.pointerStates[name] = newPointer(name);
    if (!symbols_.induction.empty()) {
      loopPointers_.insert(symbols_.pointerStates[name]);
      if (sourceState)
        loopPointerSources_[symbols_.pointerStates[name]] = *sourceState;
    }
  }
  int64_t resolveCount(std::string expr) const {
    expr = trim(expr);
    std::smatch cast;
    while (std::regex_match(
        expr, cast, std::regex(R"([A-Za-z_]\w*\s*\(\s*([\s\S]+)\s*\))")))
      expr = trim(cast[1]);
    auto value = evaluateInteger(expr, integerConstants());
    if (!value)
      throw std::runtime_error(
          "Cannot resolve loop bound '" + expr +
          "'. Pass loop_params or use a constant loop bound.");
    return *value;
  }
  std::tuple<int64_t, std::string, int64_t, int64_t>
  loopHeader(const std::string &header) const {
    std::vector<std::string> parts;
    size_t start = 0;
    for (size_t i = 0; i < header.size(); ++i)
      if (header[i] == ';') {
        parts.push_back(trim(header.substr(start, i - start)));
        start = i + 1;
      }
    parts.push_back(trim(header.substr(start)));
    std::smatch init, cond;
    if (parts.size() < 2 ||
        !std::regex_search(parts[0], init,
                           std::regex(R"(([A-Za-z_]\w*)\s*=\s*(.+)$)")) ||
        !std::regex_search(parts[1], cond,
                           std::regex(R"(([A-Za-z_]\w*)\s*(<=|<)\s*(.+)$)")))
      throw std::runtime_error("Unsupported for-loop header: " + header);
    auto var = init[1].str();
    if (cond[1] != var)
      throw std::runtime_error("Unsupported for-loop variable mismatch: " +
                               header);
    auto initial = resolveCount(init[2]), bound = resolveCount(cond[3]);
    int64_t step = 1;
    auto expr = parts.size() > 2 ? parts[2] : "";
    std::smatch m;
    if (expr.empty() ||
        std::regex_match(expr, std::regex("(?:\\+\\+\\s*" + var + "|" + var +
                                          "\\s*\\+\\+)"))) {
    } else if (std::regex_match(expr, m,
                                std::regex(var + R"(\s*\+=\s*(.+))")) ||
               std::regex_match(
                   expr, m,
                   std::regex(var + "\\s*=\\s*" + var + R"(\s*\+\s*(.+))")))
      step = resolveCount(m[1]);
    else
      throw std::runtime_error("Unsupported for-loop step: " + expr);
    if (step <= 0)
      throw std::runtime_error("Only positive for-loop steps are supported: " +
                               header);
    if (bound < initial || (bound == initial && cond[2] == "<"))
      return {0, var, initial, step};
    auto span = bound - initial + (cond[2] == "<=" ? 1 : 0);
    return {(span + step - 1) / step, var, initial, step};
  }
  std::string inferForm(const std::string &op,
                        const std::vector<AdapterValue> &dst,
                        const std::vector<AdapterValue> &src) const {
    std::string source, destination;
    for (const auto &v : src)
      if (v.storage != CanonicalStorageKind::PredicateRegister &&
          !v.dtype.empty()) {
        source = v.dtype;
        break;
      }
    for (const auto &v : dst)
      if (v.storage != CanonicalStorageKind::PredicateRegister &&
          !v.dtype.empty()) {
        destination = v.dtype;
        break;
      }
    auto spec = catalog_.lookup(op);
    if (spec && spec->formRule == "fixed")
      return spec->fixedForm;
    if (spec && spec->formRule == "conversion" && !source.empty() &&
        !destination.empty())
      return compactDtype(source) + "_to_" + compactDtype(destination);
    return destination.empty() ? source : destination;
  }
  std::optional<AdapterValue> bindArgument(const std::string &callee,
                                           const std::string &arg,
                                           const NativeOperandSpec &operand) {
    auto name = baseIdentifier(arg);
    auto kind = operand.kind;
    auto error = [&](const std::string &message) -> void {
      throw std::runtime_error(callee + " argument " +
                               std::to_string(operand.argumentIndex) + " " +
                               message + ": " + arg);
    };
    if (kind == CatalogArgumentKind::AlignState) {
      if (!symbols_.align.count(name))
        error("must be a declared vector_align state");
      return std::nullopt;
    }
    if (kind == CatalogArgumentKind::Register) {
      if (!symbols_.registers.count(name) ||
          symbols_.registers.at(name) == "bool")
        error("must be a declared vector register");
      return typedRegister(name);
    }
    if (kind == CatalogArgumentKind::UB) {
      auto ref = ubReference(arg);
      if (!ref)
        error("must be a declared UB object");
      return AdapterValue{ref->first,
                          CanonicalStorageKind::UB,
                          symbols_.ubDtypes.count(ref->first)
                              ? scalarDtype(symbols_.ubDtypes.at(ref->first))
                              : "",
                          {}};
    }
    if (kind == CatalogArgumentKind::Scalar ||
        kind == CatalogArgumentKind::RegisterOrScalar) {
      if (symbols_.registers.count(name)) {
        if (kind == CatalogArgumentKind::Scalar ||
            symbols_.registers.at(name) == "bool")
          error("must be scalar");
        return typedRegister(name);
      }
      if (symbols_.ubNames.count(name))
        error("cannot use a UB object as scalar");
      if (symbols_.scalars.count(name))
        return AdapterValue{name,
                            CanonicalStorageKind::Scalar,
                            scalarDtype(symbols_.scalarDtypes[name]),
                            {}};
      if (numericLiteral(arg))
        return AdapterValue{
            trim(arg), CanonicalStorageKind::Scalar, castScalarDtype(arg), {}};
      error("must be a declared scalar or literal");
    }
    if (kind == CatalogArgumentKind::Predicate) {
      std::smatch inlinePset;
      if (operand.direction == CatalogOperandDirection::Input &&
          std::regex_match(
              arg, inlinePset,
              std::regex(R"((pset_b(?:8|16|32))\s*\(([\s\S]*)\))"))) {
        name = "__predicate_inline_" + std::to_string(predicateSerial_++);
        symbols_.registers[name] = "bool";
        auto spec = catalog_.lookup(inlinePset[1]);
        auto args = splitArguments(inlinePset[2]);
        args.insert(args.begin(), name);
        auto bound = bindCall(inlinePset[1], *spec, args);
        AdapterNode producer;
        producer.name = spec->opcode;
        producer.form = spec->fixedForm;
        producer.src = std::move(bound.first);
        producer.dst = std::move(bound.second);
        predicatePrefix_.push_back(std::move(producer));
      }
      if (symbols_.registers.count(name) &&
          symbols_.registers.at(name) == "bool")
        return typedRegister(name);
      error("must be a predicate");
    }
    if (kind == CatalogArgumentKind::Config) {
      if (operand.name == "offset" && operand.allowIntegerExpression) {
        auto names = symbols_.induction;
        names.insert(symbols_.offsetScalars.begin(),
                     symbols_.offsetScalars.end());
        std::map<std::string, std::string> locals;
        for (const auto &[k, e] : symbols_.initializers)
          if (integerDtype(symbols_.scalarDtypes.at(k)))
            locals[k] = e;
        auto candidate = arg;
        std::smatch vag;
        if (std::regex_match(arg, vag,
                             std::regex(R"(vag_b(?:16|32)\s*\(([\s\S]*)\))")))
          candidate = vag[1];
        if (isAffine(candidate, params_, names, locals))
          return std::nullopt;
        error("has invalid offset expression");
      }
      bool integer =
          operand.allowIntegerExpression &&
          (evaluateInteger(arg, integerConstants()).has_value() ||
           evaluateUnsigned(arg, integerConstants(), symbols_.initializers)
               .has_value());
      if (!operand.allowedValues.empty() &&
          !operand.allowedValues.count(trim(arg)) && !integer)
        error("has unsupported configuration");
      if (std::regex_match(trim(arg),
                           std::regex(R"([A-Z][A-Z0-9_]*|[+-]?\d+)")) ||
          integer)
        return std::nullopt;
      error("must be a configuration token");
    }
    return std::nullopt;
  }
  std::pair<std::vector<AdapterValue>, std::vector<AdapterValue>>
  bindCall(const std::string &callee, const NativeInstructionSpec &spec,
           const std::vector<std::string> &args) {
    size_t expected = 0;
    for (const auto &o : spec.operands)
      expected = std::max(expected, static_cast<size_t>(o.argumentIndex + 1));
    if (!spec.callVariants.empty()) {
      bool match = false;
      for (const auto &variant : spec.callVariants) {
        if (static_cast<size_t>(variant.argumentCount) != args.size())
          continue;
        bool valid = true;
        for (const auto &[i, values] : variant.argumentValues)
          if (i < 0 || static_cast<size_t>(i) >= args.size() ||
              !values.count(trim(args[i])))
            valid = false;
        if (valid) {
          match = true;
          break;
        }
      }
      if (!match)
        throw std::runtime_error(
            callee + " arguments do not match a declared Catalog call variant");
    }
    if (args.size() > expected)
      throw std::runtime_error(callee + " has extra arguments");
    std::vector<AdapterValue> src, dst;
    for (const auto &o : spec.operands) {
      if (static_cast<size_t>(o.argumentIndex) >= args.size()) {
        if (o.optional)
          continue;
        throw std::runtime_error(callee + " is missing required argument " +
                                 o.name);
      }
      auto value = bindArgument(callee, args[o.argumentIndex], o);
      if (!value || o.direction == CatalogOperandDirection::Ignore)
        continue;
      if (o.direction == CatalogOperandDirection::Input ||
          o.direction == CatalogOperandDirection::ReadWrite)
        src.push_back(*value);
      if (o.direction == CatalogOperandDirection::Output ||
          o.direction == CatalogOperandDirection::ReadWrite)
        dst.push_back(*value);
    }
    return {src, dst};
  }
  std::optional<AdapterValue> genericArgument(const std::string &arg) {
    auto name = baseIdentifier(arg);
    if (symbols_.registers.count(name))
      return typedRegister(name);
    if (auto ref = ubReference(arg))
      return AdapterValue{ref->first, CanonicalStorageKind::UB, "", {}};
    if (symbols_.scalars.count(name))
      return AdapterValue{name,
                          CanonicalStorageKind::Scalar,
                          scalarDtype(symbols_.scalarDtypes[name]),
                          {}};
    return std::nullopt;
  }
  std::vector<AdapterMemoryAccess>
  memoryAccess(const NativeInstructionSpec &spec,
               const std::vector<std::string> &args, const std::string &form) {
    const NativeOperandSpec *memory = nullptr, *offset = nullptr,
                            *mode = nullptr, *update = nullptr,
                            *delta = nullptr;
    for (const auto &o : spec.operands) {
      if (o.kind == CatalogArgumentKind::UB &&
          static_cast<size_t>(o.argumentIndex) < args.size() && !memory)
        memory = &o;
      if (o.name == "offset")
        offset = &o;
      if ((o.name == "mode" || o.name == "config") && !mode)
        mode = &o;
      if (o.name == "update")
        update = &o;
      if (!o.postUpdateDeltaEncoding.empty() && !delta)
        delta = &o;
    }
    if (!memory)
      return {};
    auto ref = ubReference(args[memory->argumentIndex]);
    if (!ref)
      return {};
    auto expression = args[memory->argumentIndex],
         pointer = trim(stripUbWrappers(expression)
                            .substr(0, stripUbWrappers(expression).find('+')));
    auto state = symbols_.pointerStates.at(pointer);
    bool post = update &&
                static_cast<size_t>(update->argumentIndex) < args.size() &&
                trim(args[update->argumentIndex]) == "POST_UPDATE";
    std::optional<std::string> deltaBytes;
    auto implicit = spec.implicitPostUpdateBytes.find(form);
    if (implicit != spec.implicitPostUpdateBytes.end()) {
      deltaBytes = std::to_string(implicit->second);
      post = true;
    }
    if (post) {
      if (stripUbWrappers(expression) != pointer)
        throw std::runtime_error(
            "POST_UPDATE requires a pointer variable, not pointer arithmetic");
      if (loopPointers_.count(state))
        throw std::runtime_error("POST_UPDATE on a loop-local reinitialized "
                                 "pointer is not yet supported");
      if (!delta && implicit == spec.implicitPostUpdateBytes.end())
        throw std::runtime_error("POST_UPDATE has no declared delta rule");
      if (implicit == spec.implicitPostUpdateBytes.end()) {
        auto raw = trim(args.at(delta->argumentIndex));
        if (std::regex_search(raw, std::regex(R"(\bvag_b(?:16|32)\s*\()")))
          throw std::runtime_error(
              "POST_UPDATE with VAG requires address-generator modeling");
        if (delta->postUpdateDeltaEncoding == "unsigned_bit_field") {
          auto value =
              evaluateUnsigned(raw, integerConstants(), symbols_.initializers);
          if (!value)
            throw std::runtime_error(
                "POST_UPDATE packed configuration must resolve to an unsigned "
                "64-bit integer constant");
          auto mask = delta->postUpdateDeltaBitWidth == 64
                          ? UINT64_MAX
                          : (uint64_t{1} << delta->postUpdateDeltaBitWidth) - 1;
          auto decoded = ((*value >> delta->postUpdateDeltaBitOffset) & mask) *
                         delta->postUpdateDeltaUnitBytes;
          deltaBytes = std::to_string(decoded);
        } else {
          auto dtype = symbols_.ubDtypes[pointer];
          std::smatch cast;
          if (std::regex_search(
                  expression, cast,
                  std::regex(
                      R"(\(\s*(?:(?:const|volatile)\s+)*__ubuf__\s+(?:(?:const|volatile)\s+)*(\w+)\s*[*&]+\s*\))")))
            dtype = scalarDtype(cast[1]);
          else if (expression.find("__ubuf__") != std::string::npos)
            throw std::runtime_error("Unsupported POST_UPDATE pointer cast: " +
                                     expression);
          if (dtype.size() > 2 && dtype.substr(dtype.size() - 2) == "_t")
            dtype.resize(dtype.size() - 2);
          static const std::map<std::string, int> widths{
              {"fp32", 4},   {"fp16", 2},   {"bf16", 2},
              {"int32", 4},  {"uint32", 4}, {"int16", 2},
              {"uint16", 2}, {"int8", 1},   {"uint8", 1}};
          auto width = widths.find(dtype);
          if (width == widths.end())
            throw std::runtime_error(
                "Unknown POST_UPDATE pointer element width: " + dtype);
          deltaBytes = "(" + expandExpression(raw, symbols_.initializers) +
                       ")*" + std::to_string(width->second);
        }
      }
      updated_.insert(state);
      allUpdated_.insert(state);
    }
    auto address = ref->second;
    if (!post && offset &&
        static_cast<size_t>(offset->argumentIndex) < args.size()) {
      auto raw = trim(args[offset->argumentIndex]);
      if (!std::regex_match(raw, std::regex(R"(vag_b(?:16|32)\s*\([\s\S]*\))")))
        address =
            combine(address, expandExpression(raw, symbols_.initializers));
    }
    std::string memoryMode =
        mode && static_cast<size_t>(mode->argumentIndex) < args.size()
            ? trim(args[mode->argumentIndex])
            : "";
    std::optional<int64_t> span;
    if (auto s = spec.memorySpanByForm.find(form);
        s != spec.memorySpanByForm.end() && s->second)
      span = s->second;
    else if (spec.memorySpan)
      span = spec.memorySpan;
    else if (begins(memoryMode, "BRC_") || begins(memoryMode, "ONEPT_"))
      span = 1;
    return {{ref->first,
             spec.instructionClass == CatalogInstructionClass::Load
                 ? CanonicalAccessKind::Read
                 : CanonicalAccessKind::Write,
             address, span, state, post ? "post_update" : "none", deltaBytes}};
  }
  std::vector<AdapterNode> statement(const std::string &stmt, int64_t line) {
    predicatePrefix_.clear();
    if (stmt.empty())
      return {};
    std::smatch m;
    if (begins(stmt, "vector_")) {
      vectorDecl(stmt);
      if (std::regex_match(stmt, m, vectorDeclaration) && m[1] == "bool") {
        std::vector<AdapterNode> out;
        for (const auto &item : splitArguments(m[2]))
          if (auto equal = item.find('='); equal != std::string::npos) {
            auto nodes = statement(trim(item.substr(0, equal)) + "=" +
                                       trim(item.substr(equal + 1)) + ";",
                                   line);
            out.insert(out.end(), nodes.begin(), nodes.end());
          }
        return out;
      }
      return {};
    }
    if (std::regex_match(stmt, scalarDeclaration)) {
      scalarDecl(stmt);
      return {};
    }
    if (std::regex_match(stmt, pointerDeclaration)) {
      pointerDecl(stmt);
      return {};
    }
    if (std::regex_match(stmt,
                         std::regex(R"(\(\s*void\s*\)\s*[A-Za-z_]\w*\s*;)")))
      return {};
    if (std::regex_match(
            stmt, m,
            std::regex(R"(([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;)"))) {
      if (!symbols_.registers.count(m[1]) || !symbols_.registers.count(m[2]))
        throw std::runtime_error(
            "Register alias assignment requires declared vector values: " +
            stmt);
      AdapterNode n;
      n.kind = AdapterNode::Kind::Alias;
      n.src = {typedRegister(m[2])};
      n.dst = {typedRegister(m[1])};
      n.sourceLocation = location(line);
      return {n};
    }
    if (std::regex_match(
            stmt, m,
            std::regex(
                R"(([A-Za-z_]\w*)\s*=\s*(pset_b(?:8|16|32))\s*\(([\s\S]*)\)\s*;)")))
      return statement(m[2].str() + "(" + m[1].str() + ", " + m[3].str() + ");",
                       line);
    if (std::regex_match(stmt, m,
                         std::regex(R"(SMEM_BAR\s*\.\s*([A-Za-z_]\w*)\s*;)",
                                    std::regex::icase))) {
      AdapterNode n;
      n.kind = AdapterNode::Kind::Membar;
      n.name = normalizeMembar(m[1]);
      n.sourceLocation = location(line);
      return {n};
    }
    if (!std::regex_match(stmt, m, callExpression))
      throw std::runtime_error("Unsupported CCE statement in __VEC_SCOPE__: " +
                               stmt);
    auto callee = m[1].str();
    auto args = splitArguments(m[2]);
    auto low = lower(callee);
    if (low == "mem_bar" || low == "membar" ||
        low.find("barrier") != std::string::npos) {
      AdapterNode n;
      n.kind = AdapterNode::Kind::Membar;
      n.name = normalizeMembar(args.empty() ? "" : args[0]);
      n.sourceLocation = location(line);
      return {n};
    }
    auto op = catalog_.canonicalOpcode(callee);
    auto spec = catalog_.lookup(op);
    if (!spec) {
      if (!begins(low, "v") && !begins(low, "pset_"))
        throw std::runtime_error(
            "Unsupported CCE statement in __VEC_SCOPE__: " + stmt);
      if (args.empty())
        throw std::runtime_error(callee +
                                 " expects at least one destination operand");
      for (const auto &arg : args) {
        auto name = baseIdentifier(arg);
        if ((symbols_.registers.count(name) &&
             symbols_.registers.at(name) == "bool") ||
            arg.find("pset_") != std::string::npos)
          throw std::runtime_error(
              "Predicate semantics require a complete Catalog signature: " +
              callee);
      }
      AdapterNode n;
      n.name = op;
      n.dst = {typedRegister(baseIdentifier(args[0]))};
      n.sourceLocation = location(line);
      for (size_t i = 1; i < args.size(); ++i)
        if (auto v = genericArgument(args[i]))
          n.src.push_back(*v);
      n.form = inferForm(op, n.dst, n.src);
      return {n};
    }
    if (spec->virtualOpcode && spec->formRule == "conversion" &&
        args.size() >= 2) {
      auto dst = symbols_.registers.find(baseIdentifier(args[0])),
           src = symbols_.registers.find(baseIdentifier(args[1]));
      if (dst != symbols_.registers.end() && src != symbols_.registers.end()) {
        op = catalog_.specializeOpcode(op, compactDtype(src->second) + "_to_" +
                                               compactDtype(dst->second));
        spec = catalog_.lookup(op);
      }
    }
    std::string memoryMode;
    for (size_t i = 0; i < args.size(); ++i)
      if (auto variant = catalog_.lookupMemoryMode(op, trim(args[i]))) {
        for (const auto &o : variant->operands)
          if (o.name == "mode" && o.argumentIndex == static_cast<int>(i)) {
            memoryMode = trim(args[i]);
            spec = variant;
            break;
          }
        if (!memoryMode.empty())
          break;
      }
    auto bound = bindCall(callee, *spec, args);
    auto &src = bound.first, &dst = bound.second;
    if (dst.size() > 1) {
      std::set<std::string> ids, dtypes;
      for (const auto &v : dst)
        ids.insert(v.valueId);
      for (const auto &v : src)
        if (v.storage == CanonicalStorageKind::Register)
          dtypes.insert(v.dtype);
      for (const auto &v : dst)
        if (v.storage == CanonicalStorageKind::Register)
          dtypes.insert(v.dtype);
      if (ids.size() != dst.size())
        throw std::runtime_error("Multi-result destinations must be distinct");
      if (dtypes.size() > 1)
        throw std::runtime_error(
            "Multi-result register operands must have matching dtypes");
    }
    auto form = inferForm(op, dst, src);
    if (form.empty())
      for (const auto &o : spec->operands)
        if (o.kind == CatalogArgumentKind::UB &&
            static_cast<size_t>(o.argumentIndex) < args.size()) {
          auto ref = ubReference(args[o.argumentIndex]);
          if (ref && symbols_.ubDtypes.count(ref->first))
            form = symbols_.ubDtypes[ref->first];
          break;
        }
    if (spec->formRule == "fixed") {
      if (!form.empty() &&
          normalizeForm(form) != normalizeForm(spec->fixedForm))
        throw std::runtime_error("Unsupported form for " + op + ": " + form);
      form = spec->fixedForm;
    } else if (form.empty() ||
               (!spec->forms.count(form) && !spec->specializations.count(form)))
      throw std::runtime_error("Unsupported form for " + op + ": " + form);
    for (const auto &o : spec->operands)
      if (auto allowed = o.allowedValuesByForm.find(form);
          allowed != o.allowedValuesByForm.end() &&
          static_cast<size_t>(o.argumentIndex) < args.size() &&
          !allowed->second.count(trim(args[o.argumentIndex])))
        throw std::runtime_error(callee + " " + form + " argument " + o.name +
                                 " is outside its allowed values");
    AdapterNode n;
    n.name = op;
    n.form = form;
    n.instructionClass = instructionClass(spec->instructionClass);
    n.src = std::move(src);
    n.dst = std::move(dst);
    n.sourceLocation = location(line);
    if (!memoryMode.empty()) {
      if (!spec->forms.count(form))
        throw std::runtime_error("Unsupported destination types for " + op +
                                 " " + memoryMode);
      n.attributes["catalog_mode"] = memoryMode;
    }
    if (spec->alignStateArgumentIndex >= 0) {
      auto name = baseIdentifier(args.at(spec->alignStateArgumentIndex));
      n.attributes.clear();
      n.attributes["align_state_operation"] = spec->alignStateOperation;
      n.attributes["align_state_id"] = symbols_.align.at(name);
    }
    n.memoryAccesses = memoryAccess(*spec, args, form);
    for (auto &p : predicatePrefix_)
      p.sourceLocation = n.sourceLocation;
    auto result = std::move(predicatePrefix_);
    result.push_back(std::move(n));
    return result;
  }
  std::vector<AdapterNode> block(const std::string &text,
                                 const std::set<std::string> &induction,
                                 int64_t baseLine) {
    auto saved = symbols_;
    symbols_.induction = induction;
    auto restore = [&] {
      symbols_ = saved;
      for (auto it = updated_.begin(); it != updated_.end();) {
        bool present = false;
        for (const auto &[name, state] : symbols_.pointerStates)
          if (state == *it) {
            present = true;
            break;
          }
        if (!present)
          it = updated_.erase(it);
        else
          ++it;
      }
    };
    try {
      std::vector<AdapterNode> nodes;
      size_t pos = 0;
      int64_t unroll = 1;
      while (pos < text.size()) {
        while (pos < text.size() &&
               std::isspace(static_cast<unsigned char>(text[pos])))
          ++pos;
        if (pos == text.size())
          break;
        auto tail = text.substr(pos);
        std::smatch pragma;
        if (std::regex_search(
                tail, pragma,
                std::regex(
                    R"(^#\s*pragma\s+unroll\s*(?:\(\s*(\d+)\s*\)|(\d+)\b))"))) {
          unroll =
              std::stoll(pragma[1].matched ? pragma[1].str() : pragma[2].str());
          pos += pragma.length();
          continue;
        }
        if (text.compare(pos, 3, "for") == 0 &&
            (pos + 3 == text.size() ||
             (!std::isalnum(static_cast<unsigned char>(text[pos + 3])) &&
              text[pos + 3] != '_'))) {
          auto open = text.find('(', pos);
          if (open == std::string::npos)
            throw std::runtime_error("Malformed for-loop in __VEC_SCOPE__");
          auto close = matchingDelimiter(text, open, '(', ')');
          auto bodyOpen = text.find('{', close);
          if (bodyOpen == std::string::npos)
            throw std::runtime_error(
                "Only braced for-loops are supported in __VEC_SCOPE__");
          auto bodyClose = matchingDelimiter(text, bodyOpen, '{', '}');
          auto [count, var, start, step] =
              loopHeader(text.substr(open + 1, close - open - 1));
          auto variables = induction;
          variables.insert(var);
          AdapterNode n;
          n.kind = AdapterNode::Kind::Loop;
          n.count = count;
          n.unroll = unroll;
          n.inductionVariable = var;
          n.inductionStart = start;
          n.inductionStep = step;
          n.sourceLocation = location(baseLine + lineNumber(text, pos) - 1);
          n.body =
              block(text.substr(bodyOpen + 1, bodyClose - bodyOpen - 1),
                    variables, baseLine + lineNumber(text, bodyOpen + 1) - 1);
          nodes.push_back(std::move(n));
          unroll = 1;
          pos = bodyClose + 1;
          continue;
        }
        auto end = text.find(';', pos);
        if (end == std::string::npos) {
          if (!trim(tail).empty())
            throw std::runtime_error("Unsupported trailing CCE text: " +
                                     trim(tail).substr(0, 80));
          break;
        }
        auto items = statement(trim(text.substr(pos, end - pos + 1)),
                               baseLine + lineNumber(text, pos) - 1);
        nodes.insert(nodes.end(), std::make_move_iterator(items.begin()),
                     std::make_move_iterator(items.end()));
        pos = end + 1;
      }
      restore();
      return nodes;
    } catch (...) {
      restore();
      throw;
    }
  }

public:
  ScopeParser(const VFScope &scope, IntegerParams params)
      : scope_(scope), params_(std::move(params)) {
    for (const auto &[name, storage] : scope.paramStorage)
      if (storage == "UB") {
        symbols_.ubNames.insert(name);
        symbols_.ubAliases[name] = {name, "0"};
        symbols_.pointerStates[name] = newPointer(name);
        if (auto it = scope.paramDtypes.find(name);
            it != scope.paramDtypes.end())
          symbols_.ubDtypes[name] = it->second;
      } else {
        symbols_.scalars.insert(name);
        symbols_.offsetScalars.insert(name);
        symbols_.scalarDtypes[name] =
            scope.paramDtypes.count(name) ? scope.paramDtypes.at(name) : "";
      }
    size_t start = 0;
    for (size_t i = 0; i < scope.declarationSource.size(); ++i)
      if (scope.declarationSource[i] == ';') {
        auto stmt = trim(scope.declarationSource.substr(start, i - start + 1));
        if (std::regex_match(stmt, vectorDeclaration))
          vectorDecl(stmt);
        if (std::regex_match(stmt, scalarDeclaration))
          scalarDecl(stmt);
        start = i + 1;
      }
    start = 0;
    for (size_t i = 0; i < scope.declarationSource.size(); ++i)
      if (scope.declarationSource[i] == ';') {
        auto stmt = trim(scope.declarationSource.substr(start, i - start + 1));
        if (std::regex_match(stmt, pointerDeclaration))
          pointerDecl(stmt);
        start = i + 1;
      }
  }
  AdapterProgram run() {
    AdapterProgram p;
    p.params = params_;
    p.context = block(scope_.source, {}, scope_.startLine);
    for (const auto &[state, source] : loopPointerSources_) {
      auto ancestor = source;
      std::set<std::string> seen;
      while (loopPointerSources_.count(ancestor) &&
             seen.insert(ancestor).second)
        ancestor = loopPointerSources_.at(ancestor);
      if (allUpdated_.count(ancestor))
        throw std::runtime_error(
            "Cannot snapshot an updated pointer in a loop-local alias");
    }
    return normalizeProgram(std::move(p));
  }
};
IntegerParams inferCallConstants(const std::string &source,
                                 const VFScope &scope) {
  IntegerParams out;
  const std::regex assignments(
      R"(\b(?:int|uint16_t|uint32_t|int32_t)\s+([A-Za-z_]\w*)\s*=\s*(\d+)\s*;)");
  const std::regex calls("\\b" + scope.kernelName + R"(\s*\(([\s\S]*?)\)\s*;)");
  for (std::sregex_iterator it(source.begin(), source.end(), calls), end;
       it != end; ++it) {
    auto args = splitArguments((*it)[1]);
    auto prefix = source.substr(0, it->position());
    IntegerParams assigned;
    for (std::sregex_iterator a(prefix.begin(), prefix.end(), assignments), ae;
         a != ae; ++a)
      assigned[(*a)[1]] = std::stoll((*a)[2]);
    for (size_t i = 0; i < std::min(scope.params.size(), args.size()); ++i) {
      auto name = baseIdentifier(args[i]);
      if (assigned.count(name))
        out[scope.params[i]] = assigned[name];
      else if (std::regex_match(name, std::regex(R"(\d+)")))
        out[scope.params[i]] = std::stoll(name);
    }
  }
  return out;
}
} // namespace
AdapterProgram parseProgram(const std::filesystem::path &path,
                            const std::string &name,
                            const IntegerParams &loopParams) {
  auto source = stripComments(readSource(path));
  auto scopes = extractScopes(source, path);
  const auto &scope = selectScope(scopes, name);
  auto params = loopParams;
  for (const auto &[k, v] : inferCallConstants(source, scope))
    params[k] = v;
  return ScopeParser(scope, std::move(params)).run();
}
} // namespace vfsim::cce
