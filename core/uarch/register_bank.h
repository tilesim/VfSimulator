#pragma once
#include "core/execution/instruction.h"
#include <stdexcept>
namespace vfsim {
class PhysicalRegisterBank {
public:
  PhysicalRegisterBank(int64_t capacity, std::string prefix)
      : capacity_(capacity), prefix_(std::move(prefix)) {
    if (capacity <= 0)
      throw std::runtime_error("Physical register capacity must be positive");
    for (int64_t i = 0; i < capacity; ++i)
      free_.push_back(prefix_ + std::to_string(i));
  }
  std::string allocate(bool unlimited = false) {
    std::string id;
    if (unlimited)
      id = prefix_ + std::to_string(next_++);
    else {
      if (free_.empty())
        throw std::runtime_error("Physical register pool exhausted");
      id = free_.front();
      free_.pop_front();
    }
    if (!allocated_.insert(id).second)
      throw std::runtime_error("Corrupt physical register freelist");
    return id;
  }
  bool allocated(const std::string &id) const { return allocated_.count(id); }
  void release(const std::string &id) {
    if (!allocated_.erase(id))
      throw std::runtime_error("Duplicate physical register release");
    free_.push_back(id);
  }
  int64_t freeCount() const { return free_.size(); }

private:
  int64_t capacity_, next_ = 0;
  std::string prefix_;
  std::deque<std::string> free_;
  std::unordered_set<std::string> allocated_;
};
} // namespace vfsim
