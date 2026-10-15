#pragma once
#include "api/common/schema.h"
#include <map>
#include <set>
#include <unordered_map>

namespace vfsim::cce {
using IntegerParams = std::unordered_map<std::string, int64_t>;
struct AdapterValue {
  std::string valueId;
  CanonicalStorageKind storage = CanonicalStorageKind::Register;
  std::string dtype;
  std::vector<int64_t> shape;
};
struct AdapterMemoryAccess {
  std::string valueId;
  CanonicalAccessKind accessKind = CanonicalAccessKind::Read;
  std::string offset = "0";
  std::optional<int64_t> span;
  std::optional<std::string> addressStateId;
  std::string updateMode = "none";
  std::optional<std::string> postUpdateDeltaBytes;
};
struct AdapterNode {
  enum class Kind { Instruction, Loop, Alias, Membar };
  Kind kind = Kind::Instruction;
  std::string name, form;
  CanonicalInstructionClass instructionClass =
      CanonicalInstructionClass::Compute;
  std::vector<AdapterValue> src, dst;
  std::vector<AdapterMemoryAccess> memoryAccesses;
  std::map<std::string, CanonicalScalar> attributes;
  std::optional<CanonicalSourceLocation> sourceLocation;
  std::vector<AdapterNode> body;
  int64_t count = 1, unroll = 1, inductionStart = 0, inductionStep = 1;
  std::string loopId, inductionVariable;
};
struct AdapterProgram {
  std::vector<AdapterNode> context;
  std::map<std::string, AdapterValue> values;
  IntegerParams params;
  std::string defaultDtype = "fp32";
  std::map<std::string, CanonicalScalar> uarch;
  std::vector<std::string> liveInValues;
};
bool renameable(CanonicalStorageKind);
} // namespace vfsim::cce
