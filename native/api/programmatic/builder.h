#pragma once
#include "native/api/common/validator.h"
#include <exception>
#include <set>
namespace vfsim {
// Explicit construction of the Canonical input contract. build() validates
// once.
class VfInfoBuilder {
public:
  explicit VfInfoBuilder(std::unordered_map<std::string, int64_t> params = {},
                         std::map<std::string, CanonicalScalar> uarch = {},
                         std::map<std::string, CanonicalScalar> source = {});
  CanonicalStorageObject registerStorageObject(CanonicalStorageObject value);
  CanonicalValue registerValue(CanonicalValue value);
  CanonicalInstruction addInstruction(CanonicalInstruction instruction);
  CanonicalMembar addMembar(CanonicalMembar membar);
  CanonicalLoop addLoop(CanonicalLoop loop);
  CanonicalVfInfo build() const;
  class LoopScope {
  public:
    LoopScope(LoopScope &&other) noexcept;
    LoopScope(const LoopScope &) = delete;
    LoopScope &operator=(const LoopScope &) = delete;
    ~LoopScope() noexcept(false);
    void close();

  private:
    friend class VfInfoBuilder;
    LoopScope(VfInfoBuilder &builder, CanonicalLoop definition);
    void finish(bool commit);
    VfInfoBuilder *builder_;
    CanonicalLoop definition_;
    CanonicalVfInfo before_;
    std::set<std::string> idsBefore_;
    size_t depth_;
    int exceptionCount_;
  };
  LoopScope loop(CanonicalLoop definition) {
    return LoopScope(*this, std::move(definition));
  }

private:
  void reserve(const std::string &id);
  CanonicalVfInfo vf_;
  std::set<std::string> ids_;
  std::vector<std::vector<CanonicalNode>> bodies_{1};
};
} // namespace vfsim
