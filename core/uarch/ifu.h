#pragma once
#include "core/execution/instruction.h"
namespace vfsim {
class IFU {
public:
  explicit IFU(const std::vector<DynamicInst> &instructions)
      : pending_(instructions.begin(), instructions.end()) {}
  struct Borrowed {};
  IFU(const std::vector<DynamicInst> &instructions, Borrowed)
      : borrowed_(&instructions) {}
  bool done() const {
    return borrowed_ ? next_ == borrowed_->size() : pending_.empty();
  }
  const DynamicInst &nextBorrowed() { return borrowed_->at(next_++); }
  DynamicInst nextInst() {
    auto inst = std::move(pending_.front());
    pending_.pop_front();
    return inst;
  }

private:
  std::deque<DynamicInst> pending_;
  const std::vector<DynamicInst> *borrowed_ = nullptr;
  size_t next_ = 0;
};
} // namespace vfsim
