#include "api/cce/software.h"
#include "api/cce/source.h"
#include <stdexcept>

namespace vfsim::cce {
namespace {
#include "api/generated/CceLoweringData.inc"
const auto &recipes() {
  static const json::Value document = json::parseString(kCceLoweringJson);
  return document.asObject().at("recipes").asArray();
}
bool starts(const std::string &text, const std::string &prefix) {
  return text.compare(0, prefix.size(), prefix) == 0;
}
} // namespace
const json::Value *
selectSoftwareRecipe(const std::string &api,
                     const std::vector<std::string> &arguments,
                     const std::vector<std::string> &dtypes) {
  for (const auto &recipe : recipes()) {
    const auto &r = recipe.asObject();
    if (r.at("api").asString() != api)
      continue;
    const auto &operands = r.at("arguments").asArray();
    if (arguments.size() > operands.size())
      continue;
    bool matches = true;
    for (const auto &operand : operands) {
      const auto &o = operand.asObject();
      const auto pos = static_cast<size_t>(o.at("position").asInt());
      if (o.at("config").asBool()) {
        auto actual = pos < arguments.size()     ? trim(arguments[pos])
                      : o.at("default").isNull() ? ""
                                                 : o.at("default").asString();
        const auto expected =
            r.at("options").asObject().at(std::to_string(pos)).asString();
        if (starts(actual, "__cce_simd::"))
          actual = actual.substr(12);
        const auto def =
            o.at("default").isNull() ? "" : o.at("default").asString();
        if (actual == "MODE_UNKNOWN" && expected == "MODE_ZEROING" &&
            def == "MODE_UNKNOWN")
          actual = "MODE_ZEROING";
        matches &= actual == expected;
      } else {
        if (pos >= arguments.size()) {
          matches = false;
          break;
        }
        auto type = o.at("type").asString();
        if (starts(type, "vector_")) {
          type = trim(type.substr(7));
          if (!type.empty() && type.back() == '&')
            type = trim(type.substr(0, type.size() - 1));
          matches &= dtypes[pos] == normalizeDtype(type);
        } else
          matches &= dtypes[pos] != "bool" && !starts(dtypes[pos], "2xvl_");
      }
    }
    if (matches)
      return &recipe;
  }
  return nullptr;
}
std::vector<AdapterNode> emitSoftwareRecipe(
    const json::Value &recipe,
    const std::map<int, std::vector<AdapterValue>> &arguments,
    const std::function<AdapterValue(const std::string &)> &temporary,
    const std::function<AdapterValue(const std::string &)> &scalar,
    const std::optional<CanonicalSourceLocation> &location) {
  const auto &r = recipe.asObject();
  std::vector<AdapterNode> nodes;
  std::map<std::pair<int, int>, AdapterValue> captured;
  auto argument = [&](const json::Value &ref, bool snapshot) {
    const auto &o = ref.asObject();
    const int pos = o.at("argument").asInt(),
              part = o.count("part") ? o.at("part").asInt() : 0;
    const auto original = arguments.at(pos).at(part);
    const auto key = std::make_pair(pos, part);
    if (snapshot && renameable(original.storage)) {
      if (!captured.count(key)) {
        auto value = temporary(original.dtype);
        AdapterNode alias;
        alias.kind = AdapterNode::Kind::Alias;
        alias.src = {original};
        alias.dst = {value};
        alias.sourceLocation = location;
        nodes.push_back(std::move(alias));
        captured.emplace(key, value);
      }
      return captured.at(key);
    }
    return original;
  };
  std::function<void(const json::Value &)> capture =
      [&](const json::Value &ref) {
        if (!ref.isObject())
          return;
        const auto &o = ref.asObject();
        if (o.count("argument"))
          argument(ref, true);
        if (o.count("operands"))
          for (const auto &child : o.at("operands").asArray())
            capture(child);
      };
  for (const auto &step : r.at("steps").asArray())
    for (const auto &ref : step.asObject().at("inputs").asArray())
      capture(ref);
  for (const auto &assignment : r.at("assignments").asArray())
    capture(assignment.asObject().at("source"));
  std::map<std::string, AdapterValue> bindings;
  std::map<std::string, json::Value> destinations;
  for (const auto &assignment : r.at("assignments").asArray()) {
    const auto &a = assignment.asObject();
    const auto &source = a.at("source");
    if (source.isString() && !bindings.count(source.asString())) {
      const auto id = source.asString();
      bindings.emplace(id, argument(a.at("destination"), false));
      destinations.emplace(id, a.at("destination"));
    }
  }
  const auto &temps = r.at("temporaries").asObject();
  for (size_t i = 0; i < temps.size(); ++i) {
    const auto id = "t" + std::to_string(i);
    if (!bindings.count(id))
      bindings.emplace(
          id, temporary(temps.at(id).asObject().at("dtype").asString()));
  }
  auto resolve = [&](const json::Value &ref) {
    if (ref.isString())
      return bindings.at(ref.asString());
    if (ref.asObject().count("argument"))
      return argument(ref, true);
    return scalar(ref.asObject().at("dtype").asString());
  };
  for (const auto &step : r.at("steps").asArray()) {
    const auto &s = step.asObject();
    AdapterNode node;
    node.name = s.at("opcode").asString();
    node.form = s.at("form").asString();
    node.sourceLocation = location;
    for (const auto &ref : s.at("inputs").asArray())
      node.src.push_back(resolve(ref));
    for (const auto &id : s.at("outputs").asArray())
      node.dst.push_back(bindings.at(id.asString()));
    node.attributes["cann_intrinsic"] = s.at("intrinsic").asString();
    if (s.count("attributes"))
      for (const auto &[name, value] : s.at("attributes").asObject()) {
        if (value.isString())
          node.attributes[name] = value.asString();
        else if (value.isInt())
          node.attributes[name] = value.asInt();
        else if (value.isBool())
          node.attributes[name] = value.asBool();
        else
          throw std::runtime_error(
              "Software recipe attributes must be JSON scalars");
      }
    nodes.push_back(std::move(node));
  }
  for (const auto &assignment : r.at("assignments").asArray()) {
    const auto &a = assignment.asObject();
    const auto &source = a.at("source");
    if (source.isString() && destinations.count(source.asString()) &&
        destinations.at(source.asString()) == a.at("destination"))
      continue;
    AdapterNode alias;
    alias.kind = AdapterNode::Kind::Alias;
    alias.sourceLocation = location;
    alias.dst = {argument(a.at("destination"), false)};
    alias.src = {resolve(source)};
    alias.attributes["register_bitcast"] = true;
    nodes.push_back(std::move(alias));
  }
  return nodes;
}
} // namespace vfsim::cce
