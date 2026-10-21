// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/cce/parser.h"
#include "api/cce/affine.h"
#include "api/cce/normalization.h"
#include "api/cce/software.h"
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
      initializers, ubDtypes, pointerStates, addresses, addressStates;
  std::set<std::string> scalars, offsetScalars, ubNames, induction;
  std::map<std::string, std::pair<std::string, std::string>> ubAliases;
  std::map<std::string, std::vector<AdapterValue>> pairs;
};
class ScopeParser {
  const VFScope &scope_;
  IntegerParams params_;
  Symbols symbols_;
  const InstructionCatalog &catalog_ = defaultInstructionCatalog();
  int64_t alignSerial_ = 0, pointerSerial_ = 0, predicateSerial_ = 0,
          vectorSerial_ = 0, softwareScalarSerial_ = 0;
  bool collectingDeclarations_ = true;
  std::vector<std::tuple<std::string, int64_t, int64_t>> loopInductions_;
  std::map<int, std::string> addressBindings_;
  std::set<std::string> updated_, allUpdated_, loopPointers_;
  std::map<std::string, std::string> loopPointerSources_;
  std::vector<AdapterNode> instructionPrefix_;
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
      if (dtype == "address") {
        symbols_.registers.erase(id);
        auto equal = item.find('=');
        if (!collectingDeclarations_)
          symbols_.addressStates[id] = newPointer("address:" + id);
        symbols_.addresses[id] =
            equal == std::string::npos ? "" : trim(item.substr(equal + 1));
      } else if (dtype == "align") {
        symbols_.registers.erase(id);
        symbols_.align[id] = newAlign(id);
      } else {
        symbols_.align.erase(id);
        symbols_.registers[id] = dtype;
        if (!collectingDeclarations_ &&
            (dtype == "2xvl_s64" || dtype == "2xvl_u64"))
          declarePair(id, dtype);
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
    if (spec && spec->formRule == "source_dtype")
      return source;
    if (spec && spec->formRule == "conversion" && !source.empty() &&
        !destination.empty())
      return compactDtype(source) + "_to_" + compactDtype(destination);
    return destination.empty() ? source : destination;
  }
  AdapterValue temporaryRegister(const std::string &dtype) {
    const bool predicate = dtype == "bool";
    std::string name;
    do {
      name = predicate
                 ? "__vfsim_predicate_" + std::to_string(predicateSerial_++)
                 : "__vfsim_vector_" + std::to_string(vectorSerial_++);
    } while (symbols_.registers.count(name) || symbols_.scalars.count(name) ||
             symbols_.ubNames.count(name));
    symbols_.registers[name] = dtype;
    return typedRegister(name);
  }
  void declarePair(const std::string &name, const std::string &dtype) {
    const auto component = dtype == "2xvl_s64" ? "int32" : "uint32";
    symbols_.pairs[name] = {temporaryRegister(component),
                            temporaryRegister(component)};
  }
  AdapterValue temporaryScalar(const std::string &dtype) {
    std::string name;
    do {
      name = "__vfsim_scalar_" + std::to_string(softwareScalarSerial_++);
    } while (symbols_.registers.count(name) || symbols_.scalars.count(name) ||
             symbols_.ubNames.count(name));
    symbols_.scalars.insert(name);
    symbols_.scalarDtypes[name] = dtype;
    return {name, CanonicalStorageKind::Scalar, dtype, {}};
  }
  std::string pairMembers(const std::string &source) const {
    const std::regex member(R"(\b([A-Za-z_]\w*)\.val\[([^]]+)\])");
    std::string result;
    size_t previous = 0;
    for (std::sregex_iterator i(source.begin(), source.end(), member), end;
         i != end; ++i) {
      result += source.substr(previous, i->position() - previous);
      const auto name = (*i)[1].str();
      if (symbols_.pairs.count(name) && (*i)[2] != "0" && (*i)[2] != "1")
        throw std::runtime_error(
            "vector_2xvl component index must be zero or one");
      result += symbols_.pairs.count(name)
                    ? symbols_.pairs.at(name).at(std::stoi((*i)[2])).valueId
                    : i->str();
      previous = i->position() + i->length();
    }
    result += source.substr(previous);
    for (const auto &[name, dtype] : symbols_.registers)
      if (dtype == "s4x2")
        result = std::regex_replace(
            result, std::regex("\\b" + name + R"(\.val\b(?!\[))"), name);
    return result;
  }
  std::vector<AdapterNode> pairMemory(const std::string &api,
                                      const std::vector<std::string> &args,
                                      int64_t line) {
    const size_t expected = api == "vlds" ? 3 : 4;
    if (args.size() != expected)
      throw std::runtime_error(api + " vector_2xvl has invalid arguments");
    const auto pair = symbols_.pairs.at(baseIdentifier(args[0]));
    const auto ref = ubReference(args[1]);
    if (!ref)
      throw std::runtime_error("Pair memory requires a declared UB pointer");
    const auto base = ref->first;
    if (dtypeElementBytes(scalarDtype(symbols_.ubDtypes.at(base))) != 8)
      throw std::runtime_error("Pair memory requires a 64-bit element pointer");
    const auto offset = "((" + ref->second + ")+(" +
                        expandExpression(args[2], symbols_.initializers) +
                        "))*8";
    AdapterValue memory{base,
                        CanonicalStorageKind::UB,
                        scalarDtype(symbols_.ubDtypes.at(base)),
                        {}};
    auto access = [&](CanonicalAccessKind kind, const std::string &address,
                      int span) {
      AdapterMemoryAccess a;
      a.valueId = base;
      a.accessKind = kind;
      a.offset = address;
      a.span = span;
      a.addressUnitBytes = 1;
      return a;
    };
    if (api == "vlds") {
      AdapterNode n;
      n.name = "VLDS_PAIR";
      n.form = "b32";
      n.instructionClass = CanonicalInstructionClass::Load;
      n.src = {memory};
      n.dst = pair;
      n.sourceLocation = location(line);
      n.memoryAccesses = {access(CanonicalAccessKind::Read, offset, 512)};
      return {n};
    }
    const auto maskName = baseIdentifier(args[3]);
    if (!symbols_.registers.count(maskName) ||
        symbols_.registers.at(maskName) != "bool")
      throw std::runtime_error("Pair store requires a predicate");
    const auto mask = typedRegister(maskName);
    std::vector<AdapterValue> predicates = {temporaryRegister("bool"),
                                            temporaryRegister("bool")};
    std::vector<AdapterValue> vectors = {temporaryRegister("uint32"),
                                         temporaryRegister("uint32")};
    AdapterNode masks;
    masks.name = "PINTLV";
    masks.form = "b32";
    masks.src = {mask, mask};
    masks.dst = predicates;
    masks.sourceLocation = location(line);
    AdapterNode data;
    data.name = "VINTLV";
    data.form = "uint32";
    data.src = pair;
    data.dst = vectors;
    data.sourceLocation = location(line);
    std::vector<AdapterNode> nodes = {masks, data};
    for (int i = 0; i < 2; ++i) {
      AdapterNode n;
      n.name = "VSTS_BYTES";
      n.form = "b32";
      n.instructionClass = CanonicalInstructionClass::Store;
      n.src = {vectors[i], predicates[i]};
      n.dst = {memory};
      n.sourceLocation = location(line);
      n.memoryAccesses = {access(CanonicalAccessKind::Write,
                                 "(" + offset + ")+" + std::to_string(i * 256),
                                 256)};
      nodes.push_back(std::move(n));
    }
    return nodes;
  }
  std::vector<AdapterNode>
  pairIndexedMemory(const std::string &api,
                    const std::vector<std::string> &args, int64_t line) {
    if (args.size() != 4)
      throw std::runtime_error(api + " vector_2xvl expects four arguments");
    const auto pair = symbols_.pairs.at(baseIdentifier(args[0]));
    const auto indexName = baseIdentifier(args[2]);
    const auto maskName = baseIdentifier(args[3]);
    if (!symbols_.registers.count(indexName) ||
        symbols_.registers.at(indexName) != "uint32")
      throw std::runtime_error(
          "Pair indexed memory requires vector_u32 indices");
    if (!symbols_.registers.count(maskName) ||
        symbols_.registers.at(maskName) != "bool")
      throw std::runtime_error("Pair indexed memory requires a predicate");
    const auto ref = ubReference(args[1]);
    if (!ref)
      throw std::runtime_error(
          "Pair indexed memory requires a declared UB pointer");
    const auto base = ref->first;
    const auto dtype = scalarDtype(symbols_.ubDtypes.at(base));
    if (dtypeElementBytes(dtype) != 8)
      throw std::runtime_error(
          "Pair indexed memory requires a 64-bit element pointer");
    const AdapterValue memory{base, CanonicalStorageKind::UB, dtype, {}};
    const auto index = typedRegister(indexName), mask = typedRegister(maskName);
    const auto low = temporaryRegister("uint32"),
               high = temporaryRegister("uint32");
    auto compute = [&](const std::string &op,
                       const std::vector<AdapterValue> &src,
                       const AdapterValue &dst) {
      AdapterNode node;
      node.name = op;
      node.form = "uint32";
      node.instructionClass = CanonicalInstructionClass::Compute;
      node.src = src;
      node.dst = {dst};
      node.sourceLocation = location(line);
      return node;
    };
    std::vector<AdapterNode> nodes{
        compute(
            "VMULS",
            {index, {"2", CanonicalStorageKind::Scalar, "uint32", {}}, mask},
            low),
        compute("VADDS",
                {low, {"1", CanonicalStorageKind::Scalar, "uint32", {}}, mask},
                high)};
    const bool load = api == "vgather2";
    for (size_t i = 0; i < 2; ++i) {
      const auto &indices = i == 0 ? low : high;
      AdapterNode node;
      node.name = load ? "VGATHER2_BYTES" : "VSCATTER_BYTES";
      node.form = pair[i].dtype;
      node.instructionClass = load ? CanonicalInstructionClass::Load
                                   : CanonicalInstructionClass::Store;
      node.src = load ? std::vector<AdapterValue>{memory, indices, mask}
                      : std::vector<AdapterValue>{pair[i], indices, mask};
      node.dst = load ? std::vector<AdapterValue>{pair[i]}
                      : std::vector<AdapterValue>{memory};
      AdapterMemoryAccess access;
      access.valueId = base;
      access.accessKind =
          load ? CanonicalAccessKind::Read : CanonicalAccessKind::Write;
      access.offset = "(" + ref->second + ")*8";
      access.span = 256;
      access.addressUnitBytes = 1;
      node.memoryAccesses = {access};
      node.sourceLocation = location(line);
      node.attributes["cann_software_api"] = api;
      nodes.push_back(std::move(node));
    }
    return nodes;
  }
  std::vector<AdapterNode> memoryWrapper(const std::string &api,
                                         const std::vector<std::string> &args,
                                         int64_t line) {
    const bool load = api == "vload";
    const size_t minimum = load ? 2 : 3;
    if (args.size() < minimum || args.size() > minimum + 1)
      throw std::runtime_error(api + " has an invalid argument count");
    const auto mode =
        args.size() > minimum ? trim(args[minimum]) : "ADDRESS_UNALIGNED";
    if (mode != "ADDRESS_ALIGNED" && mode != "ADDRESS_UNALIGNED")
      throw std::runtime_error("Invalid " + api + " address type");
    const auto name = baseIdentifier(args[load ? 0 : 1]);
    const bool pair = symbols_.pairs.count(name) != 0;
    const auto dtype = symbols_.registers.at(name);
    if (!pair &&
        !std::set<std::string>{"int8", "uint8", "int16", "uint16", "int32",
                               "uint32", "fp16", "fp32", "bf16"}
             .count(dtype))
      throw std::runtime_error("Unsupported " + api + " vector type");
    const auto pointer = args[load ? 1 : 0];
    const auto ref = ubReference(pointer);
    if (!ref)
      throw std::runtime_error(api + " requires a declared UB pointer");
    const auto base = ref->first, offset = ref->second,
               rootDtype = scalarDtype(symbols_.ubDtypes.at(base));
    const auto rootWidth = dtypeElementBytes(rootDtype);
    if (!rootWidth)
      throw std::runtime_error("Unknown UB pointer width");
    const AdapterValue memory{base, CanonicalStorageKind::UB, rootDtype, {}};
    const auto state = newAlign(api);
    auto cursor = newPointer(api);
    const auto loc = location(line);
    std::vector<AdapterNode> nodes;
    auto emit = [&](const std::string &op, const std::string &form,
                    std::vector<AdapterValue> inputs,
                    std::vector<AdapterValue> outputs, const std::string &at,
                    std::optional<int64_t> advance = std::nullopt,
                    const std::string &alignment = "", bool byte = false,
                    std::optional<int64_t> span = std::nullopt) {
      AdapterNode n;
      n.name = op;
      n.form = form;
      n.instructionClass =
          instructionClass(catalog_.lookup(op)->instructionClass);
      n.src = std::move(inputs);
      n.dst = std::move(outputs);
      n.sourceLocation = loc;
      AdapterMemoryAccess a;
      a.valueId = base;
      a.offset = at;
      a.span = span;
      a.addressStateId = cursor;
      a.accessKind = n.instructionClass == CanonicalInstructionClass::Load
                         ? CanonicalAccessKind::Read
                         : CanonicalAccessKind::Write;
      if (byte)
        a.addressUnitBytes = 1;
      if (advance) {
        a.updateMode = "post_update";
        a.postUpdateDeltaBytes = std::to_string(*advance);
      }
      n.memoryAccesses = {a};
      n.attributes["cann_software_api"] = api;
      if (!alignment.empty()) {
        n.attributes["align_state_operation"] = alignment;
        n.attributes["align_state_id"] = state;
      }
      nodes.push_back(std::move(n));
    };
    auto compute = [&](const std::string &op, const std::string &form,
                       std::vector<AdapterValue> inputs,
                       std::vector<AdapterValue> outputs) {
      AdapterNode n;
      n.name = op;
      n.form = form;
      n.instructionClass = CanonicalInstructionClass::Compute;
      n.src = std::move(inputs);
      n.dst = std::move(outputs);
      n.sourceLocation = loc;
      n.attributes["cann_software_api"] = api;
      nodes.push_back(std::move(n));
    };
    if (load) {
      if (mode == "ADDRESS_ALIGNED") {
        if (pair)
          return pairMemory("vlds", {args[0], pointer, "0"}, line);
        emit("VLDS", dtype, {memory}, {typedRegister(name)}, offset);
      } else if (pair) {
        const auto at = "(" + offset + ")*" + std::to_string(rootWidth);
        emit("VLDAS_BYTES", "b32", {memory}, {}, at, std::nullopt, "load_init",
             true, 256);
        std::vector<AdapterValue> chunks{temporaryRegister("uint32"),
                                         temporaryRegister("uint32")};
        for (const auto &chunk : chunks)
          emit("VLDUS_BYTES", "b32", {memory}, {chunk}, at, 256, "load_use",
               true, 256);
        compute("VDINTLV", "uint32", chunks, symbols_.pairs.at(name));
      } else {
        emit("VLDAS", dtype, {memory}, {}, offset, std::nullopt, "load_init");
        emit("VLDUS", dtype, {memory}, {typedRegister(name)}, offset,
             std::nullopt, "load_use");
      }
      return nodes;
    }
    const auto countExpression =
        expandExpression(args[2], symbols_.initializers);
    auto count = evaluateInteger(countExpression, integerConstants());
    if (mode == "ADDRESS_ALIGNED") {
      auto mask = temporaryRegister("bool"),
           counter = temporaryScalar("uint32");
      const auto raw = baseIdentifier(args[2]);
      AdapterValue source;
      if (symbols_.scalars.count(raw))
        source = {raw, CanonicalStorageKind::Scalar, "uint32", {}};
      else if (count)
        source = {
            std::to_string(*count), CanonicalStorageKind::Scalar, "uint32", {}};
      else
        source = temporaryScalar("uint32");
      const auto width = pair ? 32 : dtypeElementBytes(dtype) * 8;
      compute("PLT", "b" + std::to_string(width), {source}, {mask, counter});
      if (pair) {
        auto stores =
            pairMemory("vsts", {args[1], pointer, "0", mask.valueId}, line);
        nodes.insert(nodes.end(), stores.begin(), stores.end());
      } else
        emit("VSTS", dtype, {typedRegister(name), mask}, {memory}, offset);
      return nodes;
    }
    if (!count)
      throw std::runtime_error("Unaligned vstore requires elementCount in "
                               "loop_params for its pointer advances");
    if (*count < 0 || *count > (pair ? 64 : 256 / dtypeElementBytes(dtype)))
      throw std::runtime_error("vstore elementCount exceeds one vector");
    if (pair) {
      const auto at = "(" + offset + ")*" + std::to_string(rootWidth);
      std::vector<AdapterValue> chunks{temporaryRegister("uint32"),
                                       temporaryRegister("uint32")};
      compute("VINTLV", "uint32", symbols_.pairs.at(name), chunks);
      emit("VSTUS_BYTES", "b32", {chunks[0]}, {memory}, at,
           std::min<int64_t>(32, *count) * 8, "append", true, 256);
      emit("VSTUS_BYTES", "b32", {chunks[1]}, {memory}, at,
           std::max<int64_t>(0, *count - 32) * 8, "append", true, 256);
      emit("VSTAS_BYTES", "b32", {}, {memory}, at, std::nullopt, "consume",
           true, 256);
    } else {
      emit("VSTUI", dtype, {typedRegister(name)}, {memory}, offset,
           *count * dtypeElementBytes(dtype), "append");
      cursor = newPointer(api + ":flush");
      emit("VSTAI", dtype, {}, {memory}, offset, std::nullopt, "consume");
      nodes.back().attributes["cann_pointer_result_discarded"] = true;
    }
    return nodes;
  }
  std::optional<std::vector<AdapterNode>>
  softwareCall(const std::string &api, const std::vector<std::string> &args,
               int64_t line) {
    std::vector<std::string> dtypes;
    bool pair = false;
    for (const auto &arg : args) {
      std::smatch cast;
      std::string dtype;
      if (std::regex_search(
              arg, cast,
              std::regex(
                  R"(^\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\))")))
        dtype = normalizeDtype(cast[1]);
      else if (symbols_.registers.count(baseIdentifier(arg)))
        dtype = symbols_.registers.at(baseIdentifier(arg));
      pair |= begins(dtype, "2xvl_");
      dtypes.push_back(std::move(dtype));
    }
    const bool unmaskedVcvt =
        api == "vcvt" && dtypes.size() >= 3 && dtypes[2] != "bool";
    if (!pair && !unmaskedVcvt && api != "vmod" &&
        !(api == "vdiv" && !dtypes.empty() &&
          std::set<std::string>{"int16", "uint16", "int32", "uint32"}.count(
              dtypes[0])))
      return std::nullopt;
    if (pair && (api == "vlds" || api == "vsts"))
      return pairMemory(api, args, line);
    if (pair && (api == "vgather2" || api == "vscatter"))
      return pairIndexedMemory(api, args, line);
    const auto *recipe = selectSoftwareRecipe(api, args, dtypes);
    if (!recipe)
      throw std::runtime_error("Unsupported CANN software signature: " + api);
    std::map<int, std::vector<AdapterValue>> arguments;
    for (const auto &operand : recipe->asObject().at("arguments").asArray()) {
      const auto &o = operand.asObject();
      if (o.at("config").asBool())
        continue;
      const int pos = o.at("position").asInt();
      const auto name = baseIdentifier(args.at(pos)),
                 type = o.at("type").asString();
      if (begins(type, "vector_2xvl_"))
        arguments[pos] = symbols_.pairs.at(name);
      else if (begins(type, "vector_"))
        arguments[pos] = {typedRegister(name)};
      else if (symbols_.scalars.count(name))
        arguments[pos] = {{name,
                           CanonicalStorageKind::Scalar,
                           scalarDtype(symbols_.scalarDtypes.at(name)),
                           {}}};
      else if (numericLiteral(args.at(pos)))
        arguments[pos] = {{trim(args[pos]),
                           CanonicalStorageKind::Scalar,
                           scalarDtype(type),
                           {}}};
      else
        throw std::runtime_error("Expected a scalar argument: " + args[pos]);
    }
    std::set<std::string> destinations;
    for (const auto &operand : recipe->asObject().at("arguments").asArray()) {
      const auto &o = operand.asObject();
      if (o.at("type").asString().find('&') == std::string::npos)
        continue;
      for (const auto &v :
           arguments.at(static_cast<int>(o.at("position").asInt())))
        if (!destinations.insert(v.valueId).second)
          throw std::runtime_error(
              "Multi-result destinations must be distinct");
    }
    return emitSoftwareRecipe(
        *recipe, arguments,
        [&](const auto &dtype) { return temporaryRegister(dtype); },
        [&](const auto &dtype) { return temporaryScalar(dtype); },
        location(line));
  }
  std::vector<AdapterNode>
  lowerConversionWrapper(const NativeInstructionSpec &spec,
                         const std::vector<std::string> &args,
                         const std::vector<AdapterValue> &src,
                         const std::vector<AdapterValue> &dst,
                         const std::string &form, int64_t line) {
    std::map<std::string, std::string> options;
    for (const auto &o : spec.operands)
      if (o.kind == CatalogArgumentKind::Config &&
          static_cast<size_t>(o.argumentIndex) < args.size())
        options[o.name] = trim(args[o.argumentIndex]);
    auto nodes = instructionPrefix_;
    const auto sourceLocation = location(line);
    auto emit = [&](const std::string &op, const std::string &key,
                    std::vector<AdapterValue> inputs,
                    std::vector<AdapterValue> outputs) {
      AdapterNode n;
      n.name = op;
      n.form = key;
      n.instructionClass = CanonicalInstructionClass::Compute;
      n.src = std::move(inputs);
      n.dst = std::move(outputs);
      n.sourceLocation = sourceLocation;
      nodes.push_back(std::move(n));
    };
    auto predicate = [&](const std::string &op, const std::string &key,
                         const std::vector<AdapterValue> &inputs) {
      auto value = temporaryRegister("bool");
      emit(op, key, inputs, {value});
      return value;
    };
    auto pset = [&](int width, const std::string &pattern = "PAT_ALL") {
      auto result = predicate("PSET_B" + std::to_string(width),
                              "b" + std::to_string(width), {});
      nodes.back().attributes["pattern"] = pattern;
      return result;
    };
    auto interleave = [&](const AdapterValue &left, const AdapterValue &right) {
      auto first = temporaryRegister("bool"),
           second = temporaryRegister("bool");
      emit("PINTLV", "b8", {left, right}, {first, second});
      return first;
    };
    bool masked = false;
    for (const auto &o : spec.operands)
      masked = masked || o.kind == CatalogArgumentKind::Predicate;
    const auto split = form.find("_to_");
    const auto sourceType = form.substr(0, split),
               destType = form.substr(split + 4);
    const int sourceWidth = sourceType.find("32") != std::string::npos   ? 32
                            : sourceType.find("16") != std::string::npos ? 16
                                                                         : 8;
    const auto family = spec.opcode.substr(0, spec.opcode.find('_'));
    const int maskWidth =
        family == "VSCVT" && (sourceType == "s32" || sourceType == "u32") &&
                destType == "u8"
            ? 32
        : sourceWidth <= 16 &&
                (destType == "f32" || destType == "s32" || destType == "f16") &&
                !masked
            ? sourceWidth
            : 8;
    const auto mask = masked ? src.at(1) : pset(maskWidth);
    const bool merging = options["mode"] == "MODE_MERGING";
    const auto result =
        merging ? temporaryRegister(normalizeDtype(destType)) : dst.at(0);
    std::string physical = "VCVT_" + form;
    std::transform(
        physical.begin(), physical.end(), physical.begin(),
        [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    emit(physical, form, {src.at(0), mask}, {result});
    if (merging) {
      AdapterValue mergeMask;
      if (masked)
        mergeMask = mask;
      else if (sourceWidth == 32 && destType == "u8") {
        const auto pp = options.count("pp") ? options.at("pp") : "PART_P0";
        const int part = pp.back() - '0';
        if (part == 0)
          mergeMask = pset(8, "PAT_M4");
        else {
          const auto all = pset(8), zeros = pset(8, "PAT_ALLF");
          const auto mid = part == 2 || part == 3 ? interleave(zeros, all)
                                                  : interleave(all, zeros);
          mergeMask =
              part == 2 ? interleave(mid, zeros) : interleave(zeros, mid);
        }
      } else {
        mergeMask = pset(8, sourceWidth == 16 ? "PAT_H" : "PAT_M4");
        if (sourceWidth == 16)
          mergeMask = predicate("PUNPACK", "b8", {mergeMask});
        if (options["part"] == "PART_ODD") {
          const auto all = maskWidth == 8 ? mask : pset(8);
          mergeMask = predicate("PNOT", "b8", {mergeMask, all});
        }
      }
      emit("VMOV_MERGE", normalizeDtype(destType),
           {dst.at(0), result, mergeMask}, dst);
    }
    for (auto &n : nodes)
      n.sourceLocation = sourceLocation;
    return nodes;
  }
  std::string conversionForm(const std::vector<std::string> &args) const {
    if (args.size() < 2)
      return "";
    std::vector<std::string> dtypes;
    for (size_t i = 0; i < 2; ++i) {
      std::smatch cast;
      if (std::regex_search(
              args[i], cast,
              std::regex(
                  R"(^\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\))")))
        dtypes.push_back(normalizeDtype(cast[1]));
      else {
        const auto found = symbols_.registers.find(baseIdentifier(args[i]));
        dtypes.push_back(found == symbols_.registers.end() ? ""
                                                           : found->second);
      }
    }
    return dtypes[0].empty() || dtypes[1].empty()
               ? ""
               : compactDtype(dtypes[1]) + "_to_" + compactDtype(dtypes[0]);
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
      if (numericLiteral(arg))
        return AdapterValue{
            trim(arg), CanonicalStorageKind::Scalar, castScalarDtype(arg), {}};
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
        instructionPrefix_.push_back(std::move(producer));
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
                             std::regex(R"(vag_b(?:8|16|32)\s*\(([\s\S]*)\))")))
          candidate = vag[1];
        if (std::regex_match(arg,
                             std::regex(R"(vag_b(?:8|16|32)\s*\([\s\S]*\))"))) {
          auto parts = splitArguments(candidate);
          candidate = "0";
          for (const auto &part : parts)
            candidate = combine(candidate, part);
        }
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
      if (operand.allowedValues.count(trim(arg)) ||
          std::regex_match(trim(arg),
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
  std::string vagOffset(const std::string &text, int width) const {
    auto strides = splitArguments(text);
    if (strides.empty() || strides.size() > 4)
      throw std::runtime_error("VAG requires one to four loop strides");
    std::string result = "0";
    for (size_t i = 0; i < strides.size(); ++i) {
      auto stride = expandExpression(strides[strides.size() - i - 1],
                                     symbols_.initializers);
      auto value = evaluateInteger(stride, integerConstants());
      if (value && *value == 0)
        continue;
      if (i >= loopInductions_.size())
        throw std::runtime_error("VAG stride has no enclosing hardware loop");
      const auto &[var, start, step] =
          loopInductions_[loopInductions_.size() - i - 1];
      if (step != 1) {
        if (!value || *value % step)
          throw std::runtime_error(
              "VAG stride is not affine in the source loop index");
        stride = std::to_string(*value / step);
      }
      for (const auto &[name, a, b] : loopInductions_)
        if (std::regex_search(stride, std::regex("\\b" + name + "\\b")))
          throw std::runtime_error("VAG strides must be loop invariant");
      result =
          combine(result, "((" + var + ")-(" + std::to_string(start) + "))*(" +
                              stride + ")*" + std::to_string(width));
    }
    return result;
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
    const auto distribution =
        mode && static_cast<size_t>(mode->argumentIndex) < args.size()
            ? trim(args[mode->argumentIndex])
            : "";
    int immediateUnit =
        (spec.opcode == "PLDI" || spec.opcode == "PSTI")
            ? (distribution == "US" || distribution == "PK" ? 16 : 32)
            : 1;
    if (offset && static_cast<size_t>(offset->argumentIndex) < args.size() &&
        !evaluateInteger(args[offset->argumentIndex], integerConstants()))
      immediateUnit = 1;
    auto expression = args[memory->argumentIndex],
         pointer = trim(stripUbWrappers(expression)
                            .substr(0, stripUbWrappers(expression).find('+')));
    auto state = symbols_.pointerStates.at(pointer);
    const auto binding =
        addressBindings_.find(offset ? offset->argumentIndex : -1);
    const bool addressState = binding != addressBindings_.end();
    if (addressState)
      state = binding->second;
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
      if (!addressState && stripUbWrappers(expression) != pointer)
        throw std::runtime_error(
            "POST_UPDATE requires a pointer variable, not pointer arithmetic");
      if (loopPointers_.count(state))
        throw std::runtime_error("POST_UPDATE on a loop-local reinitialized "
                                 "pointer is not yet supported");
      if (!delta && implicit == spec.implicitPostUpdateBytes.end())
        throw std::runtime_error("POST_UPDATE has no declared delta rule");
      if (implicit == spec.implicitPostUpdateBytes.end()) {
        auto raw = trim(args.at(delta->argumentIndex));
        if (std::regex_search(raw, std::regex(R"(\bvag_b(?:8|16|32)\s*\()")))
          throw std::runtime_error(
              "POST_UPDATE with VAG requires address-generator modeling");
        if (delta->postUpdateDeltaEncoding == "byte_count") {
          deltaBytes = expandExpression(raw, symbols_.initializers);
        } else if (delta->postUpdateDeltaEncoding == "unsigned_bit_field") {
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
          const auto width = dtypeElementBytes(dtype);
          if (!width)
            throw std::runtime_error(
                "Unknown POST_UPDATE pointer element width: " + dtype);
          deltaBytes = "(" + expandExpression(raw, symbols_.initializers) +
                       ")*" + std::to_string(width);
        }
      }
      if (immediateUnit != 1 && implicit == spec.implicitPostUpdateBytes.end())
        deltaBytes = "(" + *deltaBytes + ")*" + std::to_string(immediateUnit);
      updated_.insert(state);
      allUpdated_.insert(state);
    }
    auto address = ref->second;
    auto addressUnit = spec.memoryAddressUnitBytes;
    if (spec.memoryAddressUnitBytes) {
      if (spec.memoryAddressUnitBytes != 1)
        throw std::runtime_error(
            "Only byte-addressed fixed-unit CCE memory operands are supported");
      const auto width =
          dtypeElementBytes(scalarDtype(symbols_.ubDtypes.at(ref->first)));
      if (!width)
        throw std::runtime_error("Unknown pointer width for " + spec.opcode);
      address = "(" + address + ")*" + std::to_string(width);
    }
    if ((!post || spec.opcode == "VLDU" || spec.opcode == "VSTU") && offset &&
        static_cast<size_t>(offset->argumentIndex) < args.size()) {
      auto raw = trim(args[offset->argumentIndex]);
      std::smatch vag;
      if (std::regex_match(raw, vag,
                           std::regex(R"(vag_b(8|16|32)\s*\(([\s\S]*)\))"))) {
        if (!addressUnit)
          address = "(" + address + ")*" +
                    std::to_string(dtypeElementBytes(
                        scalarDtype(symbols_.ubDtypes.at(ref->first))));
        addressUnit = 1;
        address = combine(address, vagOffset(vag[2], std::stoi(vag[1]) / 8));
      } else {
        auto displacement = expandExpression(raw, symbols_.initializers);
        if (immediateUnit != 1)
          displacement =
              "(" + displacement + ")*" + std::to_string(immediateUnit);
        if (spec.opcode == "VSLDB")
          displacement = "(" + displacement + ")*32";
        else if (addressUnit == 1 &&
                 (spec.opcode == "VLDS" || spec.opcode == "VSTS"))
          displacement = "(" + displacement + ")*" +
                         std::to_string(dtypeElementBytes(
                             scalarDtype(symbols_.ubDtypes.at(ref->first))));
        address = combine(address, displacement);
      }
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
    if (spec.opcode == "VSSTB") {
      const auto config = evaluateUnsigned(memoryMode, integerConstants(),
                                           symbols_.initializers);
      if (!config)
        throw std::runtime_error(
            "VSSTB configuration must resolve to a uint64 constant");
      const auto width =
          dtypeElementBytes(scalarDtype(symbols_.ubDtypes.at(ref->first)));
      const auto stride = (*config >> 16) & 65535;
      span = (7 * stride + 1) * 32 / width;
      if (!post)
        address =
            combine(address, std::to_string((*config & 65535) * 32 / width));
    }
    return {{ref->first,
             spec.instructionClass == CatalogInstructionClass::Load
                 ? CanonicalAccessKind::Read
                 : CanonicalAccessKind::Write,
             address, span, state, post ? "post_update" : "none", deltaBytes,
             addressUnit ? std::optional<int64_t>(addressUnit) : std::nullopt}};
  }
  std::vector<AdapterNode> statement(const std::string &sourceStatement,
                                     int64_t line) {
    const auto stmt = pairMembers(sourceStatement);
    instructionPrefix_.clear();
    if (stmt.empty())
      return {};
    std::smatch m;
    if (begins(stmt, "vector_")) {
      vectorDecl(stmt);
      if (std::regex_match(stmt, m, vectorDeclaration) &&
          (m[1] == "bool" || m[1] == "2xvl_s64" || m[1] == "2xvl_u64")) {
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
    if (std::regex_match(stmt, m,
                         std::regex(R"(([A-Za-z_]\w*)\s*=\s*([\s\S]*);)")) &&
        symbols_.addresses.count(m[1])) {
      const auto rhs = trim(m[2]);
      symbols_.addresses[m[1]] =
          symbols_.addresses.count(rhs) ? symbols_.addresses.at(rhs) : rhs;
      return {};
    }
    if (std::regex_match(
            stmt, m,
            std::regex(R"(([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\s*;)"))) {
      if (symbols_.pairs.count(m[1]) || symbols_.pairs.count(m[2])) {
        if (!symbols_.pairs.count(m[1]) || !symbols_.pairs.count(m[2]))
          throw std::runtime_error(
              "Pair assignment requires two vector_2xvl values");
        std::vector<AdapterNode> out;
        for (int i = 0; i < 2; ++i) {
          AdapterNode n;
          n.kind = AdapterNode::Kind::Alias;
          n.dst = {symbols_.pairs.at(m[1])[i]};
          n.src = {symbols_.pairs.at(m[2])[i]};
          n.sourceLocation = location(line);
          n.attributes["register_bitcast"] = true;
          out.push_back(n);
        }
        return out;
      }
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
                R"(([A-Za-z_]\w*)\s*=\s*(pset_b(?:8|16|32)|pge_b(?:8|16|32)|pset_2xvl_b64|movp_b(?:16|32)|plt_b(?:8|16|32)|plt_2xvl_b64|pltm_b(?:8|16|32)|pltm_2xvl_b64)\s*\(([\s\S]*)\)\s*;)"))) {
      auto args = splitArguments(m[3]);
      std::string bound = m[1];
      for (const auto &arg : args)
        bound += ", " + arg;
      return statement(m[2].str() + "(" + bound + ");", line);
    }
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
    addressBindings_.clear();
    for (size_t i = 0; i < args.size(); ++i)
      if (symbols_.addressStates.count(trim(args[i])))
        addressBindings_[static_cast<int>(i)] =
            symbols_.addressStates.at(trim(args[i]));
    for (auto &arg : args)
      if (symbols_.addresses.count(trim(arg))) {
        auto expr = symbols_.addresses.at(trim(arg));
        if (expr.empty())
          throw std::runtime_error("Uninitialized vector_address");
        arg = expr;
      }
    std::smatch immediate;
    if (std::regex_match(low, immediate, std::regex(R"(vdupi_([su])(8|16))"))) {
      if (args.size() != 2)
        throw std::runtime_error("VDUPI requires a vector and an immediate");
      const auto dtype = std::string(immediate[1] == "s" ? "int" : "uint") +
                         immediate[2].str();
      auto value =
          std::regex_replace(args[1], std::regex(R"(^\s*\([^)]*\)\s*)"), "");
      auto literal = evaluateInteger(value, integerConstants());
      if (!literal)
        throw std::runtime_error("VDUPI requires a constant integer immediate");
      const auto target = typedRegister(baseIdentifier(args[0]));
      AdapterNode first;
      first.name = "VDUPI";
      first.form = dtype;
      first.instructionClass = CanonicalInstructionClass::Compute;
      first.sourceLocation = location(line);
      first.src = {{std::to_string(*literal & 255),
                    CanonicalStorageKind::Scalar,
                    dtype,
                    {}}};
      first.dst = {target};
      std::vector<AdapterNode> out{first};
      if (immediate[2] == "16") {
        first.name = "VDUPI_HI";
        first.src = {
            target,
            {std::to_string((static_cast<uint64_t>(*literal) >> 8) & 255),
             CanonicalStorageKind::Scalar,
             dtype,
             {}}};
        out.push_back(first);
      }
      return out;
    }
    auto op = catalog_.canonicalOpcode(callee);
    if (op == "VGATHERB" && args.size() == 3)
      op = "VGATHERB_UNMASKED";
    if (op == "VLD" && args.size() == 3) {
      op = "VLDS";
      auto destination = args[0];
      if (symbols_.registers.count(baseIdentifier(destination)) &&
          symbols_.registers.at(baseIdentifier(destination)) == "s4x2")
        destination = "(vector_s8 &)" + destination;
      args = {destination, args[1], "0", args[2]};
    } else if (op == "VLD" && args.size() == 4 &&
               symbols_.registers.count(baseIdentifier(args[1])) &&
               catalog_.lookupMemoryMode("VLD", trim(args.back()))) {
      op = "VLDS";
      args = {args[0], args[1], args[2], "0", args[3]};
    }
    if (low == "vload" || low == "vstore")
      return memoryWrapper(low, args, line);
    if (auto lowered = softwareCall(low, args, line))
      return *lowered;
    if (op == "VLDU" && args.size() == 5)
      args.push_back("POST_UPDATE");
    if (op == "VSTU" && (args.size() != 5 || trim(args[4]) != "POST_UPDATE"))
      throw std::runtime_error("VSTU requires POST_UPDATE");
    if (op == "VSTUR" && args.size() == 4) {
      const auto width =
          dtypeElementBytes(symbols_.registers.at(baseIdentifier(args[1])));
      if (!width ||
          (trim(args[3]) != "POST_UPDATE" && trim(args[3]) != "NO_POST_UPDATE"))
        throw std::runtime_error("Invalid vstur signature");
      const auto update = trim(args[3]);
      args = {args[0], std::to_string(256 / width), args[1], args[2]};
      if (update == "POST_UPDATE")
        args.push_back(update);
    }
    if (op == "VDUP" && args.size() >= 3) {
      const bool scalar =
          symbols_.registers.count(baseIdentifier(args[1])) == 0;
      const bool modeOk = trim(args.back()) == "MODE_ZEROING" ||
                          trim(args.back()) == "MODE_MERGING";
      const bool valid =
          (args.size() == 4 && scalar && modeOk) ||
          (args.size() == 5 && !scalar && modeOk &&
           (trim(args[3]) == "POS_LOWEST" || trim(args[3]) == "POS_HIGHEST"));
      if (!valid)
        throw std::runtime_error("Unsupported vdup call variant; expected "
                                 "scalar or data vector signature");
      const bool merging = trim(args.back()) == "MODE_MERGING";
      const bool highest = args.size() == 5 && trim(args[3]) == "POS_HIGHEST";
      op = std::string(scalar    ? "VDUPS"
                       : highest ? "VDUP_HIGHEST"
                                 : "VDUP") +
           (merging ? "_MERGE" : "");
      args.resize(3);
    }
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
    std::smatch calleeWidth;
    const bool hasCalleeWidth =
        std::regex_search(callee, calleeWidth, std::regex(R"(_b(8|16|32)$)"));
    if (callee == "plt_b8" || callee == "plt_b16" || callee == "plt_b32" ||
        callee == "plt_2xvl_b64") {
      if (args.size() != 3 || trim(args[2]) != "POST_UPDATE")
        throw std::runtime_error(callee +
                                 " requires a scalar count and POST_UPDATE");
      args[2] = args[1];
    }
    if (op == "VMOV" && (args.size() == 3 || args.size() == 4)) {
      op = "VMOV_MERGE";
      spec = catalog_.lookup(op);
    }
    if (spec->virtualOpcode && spec->formRule == "conversion" &&
        args.size() >= 2) {
      const auto form = conversionForm(args);
      if (!form.empty()) {
        op = catalog_.specializeOpcode(op, form);
        spec = catalog_.lookup(op);
        if (spec->virtualOpcode && !begins(spec->opcode, "VFCVT_") &&
            !begins(spec->opcode, "VSFCVT_") && !begins(spec->opcode, "VSCVT_"))
          throw std::runtime_error("Unsupported semantic form for " + callee +
                                   ": " + form);
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
    if (op == "VDUPS_MERGE" && dst.at(0).dtype == "bf16")
      src.at(0) = temporaryRegister("bf16");
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
    if (op == "VGATHER2") {
      const auto ref = ubReference(args.at(1));
      if (ref && symbols_.ubDtypes.count(ref->first))
        form = scalarDtype(symbols_.ubDtypes.at(ref->first));
    }
    if ((spec->instructionClass == CatalogInstructionClass::Compute ||
         op == "VLDS" || op == "VLD" || op == "VSTS" || op == "VST") &&
        (spec->formRule == "operand_dtype" ||
         spec->formRule == "source_dtype")) {
      const NativeOperandSpec *primary = nullptr;
      for (const auto &o : spec->operands)
        if (o.kind == CatalogArgumentKind::Register &&
            (o.direction == CatalogOperandDirection::Output ||
             o.direction == CatalogOperandDirection::ReadWrite)) {
          primary = &o;
          break;
        }
      if (!primary || spec->formRule == "source_dtype")
        for (const auto &o : spec->operands)
          if (o.kind == CatalogArgumentKind::Register &&
              o.direction == CatalogOperandDirection::Input) {
            primary = &o;
            break;
          }
      std::smatch view;
      if (primary &&
          static_cast<size_t>(primary->argumentIndex) < args.size() &&
          std::regex_search(
              args[primary->argumentIndex], view,
              std::regex(
                  R"(^\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\))")))
        form = normalizeDtype(view[1]);
    }
    if (spec->formRule == "conversion") {
      const auto actual = conversionForm(args);
      if (!actual.empty())
        form = actual;
    } else if (spec->fixedForm.find("_to_") != std::string::npos) {
      const auto actual = conversionForm(args);
      if (!actual.empty() && actual != spec->fixedForm)
        throw std::runtime_error(callee + " requires " + spec->fixedForm +
                                 ", got " + actual);
    }
    if (hasCalleeWidth && spec->forms.count("b" + calleeWidth[1].str()))
      form = "b" + calleeWidth[1].str();
    if (callee == "plt_2xvl_b64" || callee == "pltm_2xvl_b64")
      form = "b32";
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
    if (spec->virtualOpcode &&
        (begins(spec->opcode, "VFCVT_") || begins(spec->opcode, "VSFCVT_") ||
         begins(spec->opcode, "VSCVT_")))
      return lowerConversionWrapper(*spec, args, src, dst, form, line);
    AdapterNode n;
    n.name = catalog_.specializeOpcode(op, form);
    if (op == "PMOV") {
      // Both CCE predicate-copy forms lower to a single PAND.
      const auto source = src.at(0);
      src = {source, source, src.size() == 2 ? src[1] : source};
    }
    if (op == "VCADD") {
      const auto &source = src.at(0).dtype;
      const auto result = source == "int16"    ? "int32"
                          : source == "uint16" ? "uint32"
                                               : source;
      if (dst.at(0).dtype != result)
        throw std::runtime_error("VCADD " + source + " requires a " + result +
                                 " destination");
    }
    if (op == "VEXPDIF") {
      std::vector<std::string> views;
      for (size_t i = 0; i < 2; ++i) {
        std::smatch view;
        views.push_back(
            std::regex_search(
                args[i + 1], view,
                std::regex(R"(^\s*\(\s*vector_([A-Za-z0-9_]+)\s*&?\s*\))"))
                ? normalizeDtype(view[1])
                : src.at(i).dtype);
      }
      if (dst.at(0).dtype != "fp32" ||
          (views[0] != "fp16" && views[0] != "fp32") || views[1] != views[0])
        throw std::runtime_error(
            "Unsupported semantic form for VEXPDIF: requires matching "
            "FP16/FP32 sources and an FP32 destination");
    }
    if (op == "VMULS" && form == "bf16") {
      std::string temporary;
      do {
        temporary = "__vfsim_vector_" + std::to_string(vectorSerial_++);
      } while (symbols_.registers.count(temporary) ||
               symbols_.scalars.count(temporary) ||
               symbols_.ubNames.count(temporary));
      symbols_.registers[temporary] = "bf16";
      AdapterValue scalarVector{temporary, CanonicalStorageKind::Register,
                                "bf16"};
      AdapterNode broadcast;
      broadcast.name = "VDUPS";
      broadcast.form = "bf16";
      broadcast.src = {src.at(1), src.at(2)};
      broadcast.dst = {scalarVector};
      broadcast.instructionClass = CanonicalInstructionClass::Compute;
      instructionPrefix_.push_back(std::move(broadcast));
      src = {src.at(0), scalarVector, src.at(2)};
    }
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
    for (auto &p : instructionPrefix_)
      p.sourceLocation = n.sourceLocation;
    auto result = std::move(instructionPrefix_);
    bool merging = false;
    for (const auto &o : spec->operands)
      merging |= o.name == "mode" &&
                 static_cast<size_t>(o.argumentIndex) < args.size() &&
                 trim(args[o.argumentIndex]) == "MODE_MERGING";
    if (merging && spec->opcode != "VMOV_MERGE") {
      if (n.dst.size() != 1 ||
          n.dst[0].storage != CanonicalStorageKind::Register)
        throw std::runtime_error("Unsupported merging destination for " +
                                 callee);
      auto mask = std::find_if(n.src.rbegin(), n.src.rend(), [](const auto &v) {
        return v.storage == CanonicalStorageKind::PredicateRegister;
      });
      if (mask == n.src.rend())
        throw std::runtime_error("Merging requires a predicate for " + callee);
      auto destination = n.dst;
      auto temporary = temporaryRegister(destination[0].dtype);
      AdapterNode merge;
      merge.name = "VMOV_MERGE";
      merge.form = form;
      merge.instructionClass = CanonicalInstructionClass::Compute;
      merge.src = {destination[0], temporary, *mask};
      merge.dst = destination;
      merge.sourceLocation = n.sourceLocation;
      n.dst = {temporary};
      result.push_back(std::move(n));
      result.push_back(std::move(merge));
      return result;
    }
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
          loopInductions_.emplace_back(var, start, step);
          n.body =
              block(text.substr(bodyOpen + 1, bodyClose - bodyOpen - 1),
                    variables, baseLine + lineNumber(text, bodyOpen + 1) - 1);
          loopInductions_.pop_back();
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
    collectingDeclarations_ = false;
    std::vector<std::pair<std::string, std::string>> pairs;
    for (const auto &[name, dtype] : symbols_.registers)
      if (dtype == "2xvl_s64" || dtype == "2xvl_u64")
        pairs.push_back({name, dtype});
    for (const auto &[name, dtype] : pairs)
      declarePair(name, dtype);
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
