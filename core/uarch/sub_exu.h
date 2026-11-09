#pragma once
#include <algorithm>
#include <cstdint>

namespace vfsim {
struct SubEXU {
  int64_t readyCycle = -1000000000;
  void start(int64_t cycle, int64_t selfIi) {
    readyCycle = std::max(readyCycle, cycle + selfIi);
  }
};
} // namespace vfsim
