// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "native/api/cce/source.h"
#include <algorithm>
#include <cctype>
#include <fstream>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>

namespace vfsim::cce {
std::string trim(const std::string &s) {
  auto first = s.find_first_not_of(" \t\r\n");
  return first == std::string::npos
             ? ""
             : s.substr(first, s.find_last_not_of(" \t\r\n") - first + 1);
}
std::string lower(std::string s) {
  std::transform(s.begin(), s.end(), s.begin(),
                 [](unsigned char c) { return std::tolower(c); });
  return s;
}
std::string normalizeDtype(const std::string &s) {
  static const std::map<std::string, std::string> aliases{
      {"s16", "int16"},     {"i16", "int16"},    {"s64", "int64"},
      {"i64", "int64"},     {"u64", "uint64"},   {"f32", "fp32"},
      {"float32", "fp32"},  {"f16", "fp16"},     {"float16", "fp16"},
      {"bfloat16", "bf16"}, {"s32", "int32"},    {"i32", "int32"},
      {"u32", "uint32"},    {"u16", "uint16"},   {"s8", "int8"},
      {"u8", "uint8"},      {"boolean", "bool"}, {"predicate", "bool"}};
  auto value = lower(trim(s));
  auto it = aliases.find(value);
  return it == aliases.end() ? value : it->second;
}
std::string scalarDtype(const std::string &s) {
  static const std::map<std::string, std::string> aliases{
      {"int", "int32"},
      {"unsigned", "uint32"},
      {"unsigned int", "uint32"},
      {"float", "fp32"},
      {"half", "fp16"},
      {"__cce_half", "fp16"},
      {"bfloat16_t", "bf16"},
      {"float8_e4m3_t", "f8e4m3"},
      {"float8_e5m2_t", "f8e5m2"},
      {"float8_e8m0_t", "f8e8m0"},
      {"hifloat8_t", "hif8"},
      {"float4_e1m2x2_t", "f4e1m2x2"},
      {"float4_e2m1x2_t", "f4e2m1x2"},
      {"int4x2_t", "s4x2"}};
  auto value = lower(trim(s));
  auto it = aliases.find(value);
  if (it != aliases.end())
    return it->second;
  if (std::regex_match(value, std::regex(R"(u?int(?:8|16|32|64)_t)")))
    value.resize(value.size() - 2);
  return normalizeDtype(value);
}
std::string compactDtype(const std::string &s) {
  static const std::map<std::string, std::string> aliases{
      {"fp32", "f32"},   {"fp16", "f16"},  {"int32", "s32"}, {"uint32", "u32"},
      {"uint16", "u16"}, {"int16", "s16"}, {"int8", "s8"},   {"uint8", "u8"},
      {"int64", "s64"},  {"uint64", "u64"}};
  auto value = normalizeDtype(s);
  auto it = aliases.find(value);
  return it == aliases.end() ? value : it->second;
}
int dtypeElementBytes(const std::string &s) {
  static const std::map<std::string, int> widths{
      {"fp32", 4},     {"fp16", 2},   {"bf16", 2},   {"int8", 1},
      {"uint8", 1},    {"int16", 2},  {"uint16", 2}, {"int32", 4},
      {"uint32", 4},   {"int64", 8},  {"uint64", 8}, {"f8e4m3", 1},
      {"f8e5m2", 1},   {"f8e8m0", 1}, {"hif8", 1},   {"f4e1m2x2", 1},
      {"f4e2m1x2", 1}, {"s4x2", 1}};
  const auto it = widths.find(normalizeDtype(s));
  return it == widths.end() ? 0 : it->second;
}
std::string normalizeForm(const std::string &s) {
  auto value = lower(trim(s));
  auto pos = value.find("_to_");
  return pos == std::string::npos
             ? normalizeDtype(value)
             : compactDtype(value.substr(0, pos)) + "_to_" +
                   compactDtype(value.substr(pos + 4));
}
std::string normalizeMembar(const std::string &s) {
  auto value = trim(s);
  if (value.empty())
    value = "VST_VLD";
  if (auto p = value.rfind('.'); p != std::string::npos)
    value = value.substr(p + 1);
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return std::toupper(c); });
  return value;
}
std::string readSource(const std::filesystem::path &path) {
  std::ifstream input(path);
  if (!input)
    throw std::runtime_error("Cannot read CCE source: " + path.string());
  std::ostringstream out;
  out << input.rdbuf();
  return out.str();
}
std::string stripComments(const std::string &source) {
  std::string out;
  for (size_t i = 0; i < source.size();) {
    if (source.compare(i, 2, "//") == 0) {
      i = source.find('\n', i);
      if (i == std::string::npos)
        break;
    } else if (source.compare(i, 2, "/*") == 0) {
      auto end = source.find("*/", i + 2);
      if (end == std::string::npos) {
        out += source.substr(i);
        break;
      }
      for (; i < end + 2; ++i)
        if (source[i] == '\n')
          out += '\n';
    } else
      out += source[i++];
  }
  return out;
}
std::vector<std::string> splitArguments(const std::string &text) {
  std::vector<std::string> out;
  int depth = 0;
  size_t start = 0;
  for (size_t i = 0; i < text.size(); ++i) {
    depth += text[i] == '(';
    depth -= text[i] == ')';
    if (text[i] == ',' && depth == 0) {
      out.push_back(trim(text.substr(start, i - start)));
      start = i + 1;
    }
  }
  auto tail = trim(text.substr(start));
  if (!tail.empty())
    out.push_back(tail);
  return out;
}
size_t matchingDelimiter(const std::string &s, size_t pos, char open,
                         char close) {
  if (pos >= s.size() || s[pos] != open)
    throw std::runtime_error("Expected delimiter in CCE source");
  int depth = 0;
  for (size_t i = pos; i < s.size(); ++i) {
    depth += s[i] == open;
    depth -= s[i] == close;
    if (depth == 0)
      return i;
  }
  throw std::runtime_error(std::string("Unmatched '") + open +
                           "' in CCE source");
}
std::string stripUbWrappers(std::string value) {
  value = trim(value);
  while (!value.empty() && value.front() == '(') {
    size_t close;
    try {
      close = matchingDelimiter(value, 0, '(', ')');
    } catch (...) {
      break;
    }
    if (close == value.size() - 1)
      value = trim(value.substr(1, close - 1));
    else {
      auto cast = value.substr(1, close - 1);
      if (cast.find("__ubuf__") == std::string::npos ||
          cast.find_first_of("*&") == std::string::npos)
        break;
      value = trim(value.substr(close + 1));
    }
  }
  return value;
}
std::string baseIdentifier(const std::string &arg) {
  static const std::regex identifier(R"([A-Za-z_]\w*)");
  static const std::set<std::string> types{
      "__ubuf__",   "bool",       "const",      "float",       "half",
      "int",        "int16_t",    "int32_t",    "int64_t",     "int8_t",
      "long",       "short",      "signed",     "uint16_t",    "uint32_t",
      "uint64_t",   "uint8_t",    "unsigned",   "vector_bool", "vector_f16",
      "vector_f32", "vector_s16", "vector_s32", "vector_u16",  "vector_u32",
      "volatile"};
  std::vector<std::string> names;
  for (std::sregex_iterator it(arg.begin(), arg.end(), identifier), end;
       it != end; ++it)
    names.push_back(it->str());
  for (auto it = names.rbegin(); it != names.rend(); ++it)
    if (!types.count(*it))
      return *it;
  return names.empty() ? trim(arg) : names.back();
}
int64_t lineNumber(const std::string &s, size_t pos) {
  return 1 + std::count(s.begin(), s.begin() + std::min(pos, s.size()), '\n');
}
std::vector<VFScope> extractScopes(const std::string &clean,
                                   const std::filesystem::path &path) {
  static const std::regex function(
      R"(\bvoid\s+([A-Za-z_]\w*)\s*\(([\s\S]*?)\)\s*\{)");
  static const std::regex vec(R"(__VEC_SCOPE__\s*\{)");
  std::vector<VFScope> scopes;
  for (std::sregex_iterator it(clean.begin(), clean.end(), function), end;
       it != end; ++it) {
    auto open = static_cast<size_t>(it->position() + it->length() - 1);
    auto close = matchingDelimiter(clean, open, '{', '}');
    auto body = clean.substr(open + 1, close - open - 1);
    std::smatch found;
    if (!std::regex_search(body, found, vec))
      continue;
    auto vecOpen = open + 1 + found.position() + found.length() - 1;
    auto vecClose = matchingDelimiter(clean, vecOpen, '{', '}');
    VFScope scope;
    scope.kernelName = (*it)[1];
    scope.startLine = lineNumber(clean, vecOpen);
    scope.endLine = lineNumber(clean, vecClose);
    scope.source = clean.substr(vecOpen + 1, vecClose - vecOpen - 1);
    scope.declarationSource = body.substr(0, found.position());
    scope.sourcePath = path.string();
    for (const auto &raw : splitArguments((*it)[2])) {
      std::smatch name;
      if (!std::regex_search(raw, name,
                             std::regex(R"(([A-Za-z_]\w*)\s*(?:=[^,]+)?$)")))
        continue;
      auto param = name[1].str();
      scope.params.push_back(param);
      scope.paramStorage[param] =
          raw.find("__ubuf__") != std::string::npos ? "UB" : "Scalar";
      std::smatch dtype;
      if (std::regex_search(
              raw, dtype,
              std::regex(
                  R"(__ubuf__\s+([A-Za-z_]\w*)\s*\*\s*&?\s*([A-Za-z_]\w*))")) ||
          std::regex_match(
              raw, dtype,
              std::regex(
                  R"((?:(?:const|volatile)\s+)*(u?int(?:8|16|32|64)_t|size_t|unsigned|signed|short|int|long|float|double|half)\s+([A-Za-z_]\w*)(?:\s*=\s*.+)?)")))
        scope.paramDtypes[dtype[2]] = scalarDtype(dtype[1]);
    }
    scopes.push_back(std::move(scope));
  }
  return scopes;
}
const VFScope &selectScope(const std::vector<VFScope> &scopes,
                           const std::string &name) {
  if (scopes.empty())
    throw std::runtime_error("No __VEC_SCOPE__ kernel found in CCE file");
  if (name.empty() && scopes.size() == 1)
    return scopes.front();
  for (const auto &scope : scopes)
    if (scope.kernelName == name)
      return scope;
  std::string names;
  for (const auto &s : scopes)
    names += (names.empty() ? "" : ", ") + s.kernelName;
  throw std::runtime_error(
      name.empty()
          ? "Multiple __VEC_SCOPE__ kernels found; select one of: " + names
          : "Kernel '" + name + "' not found. Available kernels: " + names);
}
std::vector<std::string> listKernels(const std::filesystem::path &p) {
  std::vector<std::string> out;
  for (const auto &s : extractScopes(stripComments(readSource(p)), p))
    out.push_back(s.kernelName);
  return out;
}
} // namespace vfsim::cce
