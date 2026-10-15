// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#include "api/cce/affine.h"
#include "api/cce/source.h"
#include <cctype>
#include <functional>
#include <limits>
#include <memory>
#include <stdexcept>
#include <type_traits>

namespace vfsim::cce {
namespace {
struct Expr {
  std::string token;
  std::shared_ptr<Expr> left, right;
};
class ExpressionParser {
  const std::string &text_;
  size_t pos_ = 0;
  bool suffix_;
  void ws() {
    while (pos_ < text_.size() &&
           std::isspace(static_cast<unsigned char>(text_[pos_])))
      ++pos_;
  }
  bool take(const std::string &token) {
    ws();
    if (text_.compare(pos_, token.size(), token) == 0) {
      pos_ += token.size();
      return true;
    }
    return false;
  }
  static int precedence(const std::string &op) {
    if (op == "|")
      return 1;
    if (op == "&")
      return 2;
    if (op == "<<" || op == ">>")
      return 3;
    if (op == "+" || op == "-")
      return 4;
    if (op == "*" || op == "/" || op == "//")
      return 5;
    return 0;
  }
  std::string nextOperator() {
    ws();
    for (const auto &op : {"<<", ">>", "//", "+", "-", "*", "/", "|", "&"})
      if (text_.compare(pos_, std::char_traits<char>::length(op), op) == 0)
        return op;
    return {};
  }
  std::shared_ptr<Expr> primary() {
    ws();
    if (take("+"))
      return std::make_shared<Expr>(Expr{"+", {}, primary()});
    if (take("-"))
      return std::make_shared<Expr>(Expr{"-", {}, primary()});
    if (take("(")) {
      auto expr = binary(1);
      if (!take(")"))
        throw std::runtime_error("Missing ')' in integer expression");
      return expr;
    }
    ws();
    size_t start = pos_;
    if (pos_ < text_.size() &&
        (std::isalpha(static_cast<unsigned char>(text_[pos_])) ||
         text_[pos_] == '_')) {
      while (pos_ < text_.size() &&
             (std::isalnum(static_cast<unsigned char>(text_[pos_])) ||
              text_[pos_] == '_'))
        ++pos_;
    } else if (pos_ < text_.size() &&
               std::isdigit(static_cast<unsigned char>(text_[pos_]))) {
      if (text_.compare(pos_, 2, "0x") == 0 ||
          text_.compare(pos_, 2, "0X") == 0) {
        pos_ += 2;
        while (pos_ < text_.size() &&
               std::isxdigit(static_cast<unsigned char>(text_[pos_])))
          ++pos_;
      } else
        while (pos_ < text_.size() &&
               std::isdigit(static_cast<unsigned char>(text_[pos_])))
          ++pos_;
      if (suffix_)
        while (pos_ < text_.size() &&
               (text_[pos_] == 'u' || text_[pos_] == 'U' ||
                text_[pos_] == 'l' || text_[pos_] == 'L'))
          ++pos_;
    }
    if (start == pos_)
      throw std::runtime_error("Invalid integer expression: " + text_);
    return std::make_shared<Expr>(
        Expr{text_.substr(start, pos_ - start), {}, {}});
  }
  std::shared_ptr<Expr> binary(int minimum) {
    auto lhs = primary();
    while (true) {
      auto op = nextOperator();
      int p = precedence(op);
      if (p < minimum)
        break;
      pos_ += op.size();
      auto rhs = binary(p + 1);
      lhs = std::make_shared<Expr>(Expr{op, lhs, rhs});
    }
    return lhs;
  }

public:
  explicit ExpressionParser(const std::string &s, bool suffix = false)
      : text_(s), suffix_(suffix) {}
  std::shared_ptr<Expr> parse() {
    auto expr = binary(1);
    ws();
    if (pos_ != text_.size())
      throw std::runtime_error("Unsupported integer expression: " + text_);
    return expr;
  }
};
int64_t add(int64_t a, int64_t b) {
  if ((b > 0 && a > INT64_MAX - b) || (b < 0 && a < INT64_MIN - b))
    throw std::runtime_error("Integer expression overflow");
  return a + b;
}
int64_t multiply(int64_t a, int64_t b) {
  if (a == 0 || b == 0)
    return 0;
  if ((a == -1 && b == INT64_MIN) || (b == -1 && a == INT64_MIN))
    throw std::runtime_error("Integer expression overflow");
  if (a > 0 ? (b > 0 ? a > INT64_MAX / b : b < INT64_MIN / a)
            : (b > 0 ? a < INT64_MIN / b : a < INT64_MAX / b))
    throw std::runtime_error("Integer expression overflow");
  return a * b;
}
uint64_t literal(std::string text) {
  while (!text.empty() && (text.back() == 'u' || text.back() == 'U' ||
                           text.back() == 'l' || text.back() == 'L'))
    text.pop_back();
  size_t n = 0;
  int base = text.size() > 2 &&
                     (text.substr(0, 2) == "0x" || text.substr(0, 2) == "0X")
                 ? 16
                 : 10;
  auto value = std::stoull(text, &n, base);
  if (n != text.size())
    throw std::runtime_error("Invalid integer literal");
  return value;
}
template <typename T>
T evaluate(const std::shared_ptr<Expr> &node, const IntegerParams &params) {
  if (!node->right) {
    if (std::isdigit(static_cast<unsigned char>(node->token[0]))) {
      auto value = literal(node->token);
      if constexpr (std::is_signed_v<T>)
        if (value > uint64_t(INT64_MAX))
          throw std::runtime_error("Integer literal overflow");
      return static_cast<T>(value);
    }
    auto found = params.find(node->token);
    if (found == params.end())
      throw std::runtime_error("Unresolved integer symbol: " + node->token);
    if constexpr (std::is_unsigned_v<T>)
      if (found->second < 0)
        throw std::runtime_error("Negative unsigned integer");
    return static_cast<T>(found->second);
  }
  // INT64_MIN is written as unary minus applied to a positive magnitude that
  // cannot itself be represented by int64_t.
  if constexpr (std::is_signed_v<T>) {
    if (!node->left && node->token == "-" && !node->right->right &&
        std::isdigit(static_cast<unsigned char>(node->right->token.front())) &&
        literal(node->right->token) == (uint64_t{1} << 63))
      return INT64_MIN;
  }
  auto rhs = evaluate<T>(node->right, params);
  if (!node->left) {
    if (node->token == "+")
      return rhs;
    if constexpr (std::is_signed_v<T>) {
      if (rhs == INT64_MIN)
        throw std::runtime_error("Integer expression overflow");
      return -rhs;
    } else {
      if (rhs)
        throw std::runtime_error("Negative unsigned integer");
      return 0;
    }
  }
  auto lhs = evaluate<T>(node->left, params);
  const auto &op = node->token;
  if (op == "+") {
    if constexpr (std::is_signed_v<T>)
      return add(lhs, rhs);
    else {
      if (lhs > UINT64_MAX - rhs)
        throw std::runtime_error("Unsigned expression overflow");
      return lhs + rhs;
    }
  }
  if (op == "-") {
    if constexpr (std::is_signed_v<T>) {
      if (rhs == INT64_MIN)
        throw std::runtime_error("Integer expression overflow");
      return add(lhs, -rhs);
    } else {
      if (lhs < rhs)
        throw std::runtime_error("Negative unsigned integer");
      return lhs - rhs;
    }
  }
  if (op == "*") {
    if constexpr (std::is_signed_v<T>)
      return multiply(lhs, rhs);
    else {
      if (rhs && lhs > UINT64_MAX / rhs)
        throw std::runtime_error("Unsigned expression overflow");
      return lhs * rhs;
    }
  }
  if (op == "/" || op == "//") {
    if (rhs == 0)
      throw std::runtime_error("Integer division by zero");
    if constexpr (std::is_signed_v<T>)
      if (lhs == INT64_MIN && rhs == -1)
        throw std::runtime_error("Integer expression overflow");
    if (op == "/" && lhs % rhs)
      throw std::runtime_error("Integer division must be exact");
    auto q = lhs / rhs;
    if constexpr (std::is_signed_v<T>)
      if (op == "//" && lhs % rhs && ((lhs < 0) != (rhs < 0)))
        --q;
    return q;
  }
  if (op == "|")
    return lhs | rhs;
  if (op == "&")
    return lhs & rhs;
  if (op == "<<" || op == ">>") {
    if (rhs < 0 || rhs >= 64)
      throw std::runtime_error("Invalid integer shift");
    if (op == ">>")
      return lhs >> rhs;
    if constexpr (std::is_signed_v<T>) {
      if (rhs == 63) {
        if (lhs == 0)
          return 0;
        if (lhs == -1)
          return INT64_MIN;
        throw std::runtime_error("Integer expression overflow");
      }
      return multiply(lhs, int64_t{1} << rhs);
    } else {
      if (lhs > (UINT64_MAX >> rhs))
        throw std::runtime_error("Unsigned expression overflow");
      return lhs << rhs;
    }
  }
  throw std::runtime_error("Unsupported integer operator");
}
std::string render(const std::shared_ptr<Expr> &node,
                   const std::map<std::string, std::string> &substitutions,
                   std::set<std::string> &resolving, bool suffix = false) {
  if (node->right)
    return "(" +
           (node->left ? render(node->left, substitutions, resolving, suffix)
                       : "") +
           node->token + render(node->right, substitutions, resolving, suffix) +
           ")";
  auto it = substitutions.find(node->token);
  if (it == substitutions.end() || resolving.count(node->token))
    return node->token;
  resolving.insert(node->token);
  auto result = render(ExpressionParser(it->second, suffix).parse(),
                       substitutions, resolving, suffix);
  resolving.erase(node->token);
  return result;
}
struct Affine {
  int64_t constant = 0;
  std::map<std::string, int64_t> terms;
};

// The parser checks the source expression's affine grammar before coefficient
// reduction. A variable product remains invalid even if one side cancels to
// zero.
std::pair<bool, bool> classify(const std::shared_ptr<Expr> &node,
                               const IntegerParams &params,
                               const std::set<std::string> &variables) {
  if (!node->right) {
    if (std::isdigit(static_cast<unsigned char>(node->token.front())))
      return {true, true};
    if (params.count(node->token))
      return {true, true};
    return {variables.count(node->token) != 0, false};
  }
  auto rhs = classify(node->right, params, variables);
  if (!node->left)
    return rhs;
  auto lhs = classify(node->left, params, variables);
  if (!lhs.first || !rhs.first)
    return {false, false};
  if (node->token == "+" || node->token == "-")
    return {true, lhs.second && rhs.second};
  if (node->token == "*")
    return {lhs.second || rhs.second, lhs.second && rhs.second};
  return {false, false};
}
Affine affine(const std::shared_ptr<Expr> &n, const IntegerParams &params,
              const std::set<std::string> &variables) {
  if (!n->left && n->right && n->token == "-" && !n->right->right &&
      std::isdigit(static_cast<unsigned char>(n->right->token.front())) &&
      literal(n->right->token) == (uint64_t{1} << 63))
    return {INT64_MIN, {}};
  if (!n->right) {
    if (std::isdigit(static_cast<unsigned char>(n->token[0])))
      return {evaluate<int64_t>(n, params), {}};
    if (auto it = params.find(n->token); it != params.end())
      return {it->second, {}};
    if (variables.count(n->token))
      return {0, {{n->token, 1}}};
    throw std::runtime_error("Unknown affine offset symbol: " + n->token);
  }
  auto rhs = affine(n->right, params, variables);
  if (!n->left) {
    if (n->token == "-") {
      rhs.constant = multiply(rhs.constant, -1);
      for (auto &[k, v] : rhs.terms)
        v = multiply(v, -1);
    }
    return rhs;
  }
  auto lhs = affine(n->left, params, variables);
  if (n->token == "+" || n->token == "-") {
    auto scale = n->token == "-" ? -1 : 1;
    lhs.constant = add(lhs.constant, multiply(rhs.constant, scale));
    for (const auto &[k, v] : rhs.terms) {
      lhs.terms[k] = add(lhs.terms[k], multiply(v, scale));
      if (lhs.terms[k] == 0)
        lhs.terms.erase(k);
    }
    return lhs;
  }
  if (n->token == "*") {
    if (!lhs.terms.empty() && !rhs.terms.empty())
      throw std::runtime_error("Non-affine variable multiplication");
    if (lhs.terms.empty())
      std::swap(lhs, rhs);
    lhs.constant = multiply(lhs.constant, rhs.constant);
    for (auto &[k, v] : lhs.terms)
      v = multiply(v, rhs.constant);
    return lhs;
  }
  throw std::runtime_error("Unsupported affine offset expression");
}
} // namespace
std::optional<int64_t> evaluateInteger(const std::string &s,
                                       const IntegerParams &p) {
  try {
    return evaluate<int64_t>(ExpressionParser(s, true).parse(), p);
  } catch (const std::exception &) {
    return std::nullopt;
  }
}
std::optional<uint64_t>
evaluateUnsigned(const std::string &s, const IntegerParams &p,
                 const std::map<std::string, std::string> &initializers) {
  try {
    std::set<std::string> resolving;
    auto expanded = render(ExpressionParser(s, true).parse(), initializers,
                           resolving, true);
    auto expression = ExpressionParser(expanded, true).parse();
    try {
      auto signedValue = evaluate<int64_t>(expression, p);
      if (signedValue >= 0)
        return static_cast<uint64_t>(signedValue);
      return std::nullopt;
    } catch (const std::exception &) {
    }
    return evaluate<uint64_t>(expression, p);
  } catch (const std::exception &) {
    return std::nullopt;
  }
}
std::string expandExpression(const std::string &s,
                             const std::map<std::string, std::string> &p) {
  std::set<std::string> resolving;
  return render(ExpressionParser(s).parse(), p, resolving);
}
CanonicalAffineExpression parseAffine(const std::string &s,
                                      const IntegerParams &p,
                                      const std::set<std::string> &v) {
  auto result = affine(ExpressionParser(s).parse(), p, v);
  CanonicalAffineExpression out;
  out.constant = result.constant;
  for (const auto &[k, c] : result.terms)
    out.terms.push_back({k, c});
  return out;
}
bool isAffine(const std::string &s, const IntegerParams &p,
              const std::set<std::string> &v,
              const std::map<std::string, std::string> &locals) {
  try {
    auto expanded = expandExpression(s, locals);
    return classify(ExpressionParser(expanded).parse(), p, v).first;
  } catch (const std::exception &) {
    return false;
  }
}
} // namespace vfsim::cce
