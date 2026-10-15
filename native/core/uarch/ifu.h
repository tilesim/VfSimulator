#pragma once
#include "native/core/execution/instruction.h"
namespace vfsim {
class IFU {
public:
  explicit IFU(const std::vector<DynamicInst> &instructions)
      : pending_(instructions.begin(), instructions.end()) {}
  bool done() const { return pending_.empty(); }
  DynamicInst nextInst() {
    auto inst = std::move(pending_.front());
    pending_.pop_front();
    return inst;
  }

private:
  std::deque<DynamicInst> pending_;
};
} // namespace vfsim
