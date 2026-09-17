#ifndef VFSIM_NATIVE_ADDRESS_STATE_H
#define VFSIM_NATIVE_ADDRESS_STATE_H

#include "api/native/CanonicalVfInfo.h"
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <vector>

namespace vfsim {

struct AddressDependency {
  std::string stateId;
  int64_t producerInstId;
  int64_t producerDispatchCycle;
  int64_t readyCycle;
};

// Only the latest successful IDU update is needed. No LSU event or WAR readers.
class AddressStateTracker {
public:
  explicit AddressStateTracker(int64_t latency = 1) : updateLatency_(latency) {
    if (latency <= 0)
      throw std::runtime_error("idu_post_update_ready_latency must be positive");
  }

  std::vector<AddressDependency> dependencies(
      const std::vector<CanonicalMemoryAccess> &accesses) const {
    std::set<std::string> states;
    for (const auto &access : accesses)
      if (access.addressStateId) states.insert(*access.addressStateId);
    std::vector<AddressDependency> result;
    for (const auto &state : states)
      if (auto it = updates_.find(state); it != updates_.end())
        result.push_back(it->second);
    return result;
  }

  bool canDispatch(const std::vector<CanonicalMemoryAccess> &accesses,
                   int64_t cycle) const {
    for (const auto &d : dependencies(accesses))
      if (cycle < d.readyCycle) return false;
    return true;
  }

  void notifyDispatch(int64_t instId,
                      const std::vector<CanonicalMemoryAccess> &accesses,
                      int64_t cycle) {
    std::set<std::string> states;
    for (const auto &access : accesses)
      if (access.addressStateId && access.updateMode == "post_update")
        states.insert(*access.addressStateId);
    if (!states.empty() && cycle > std::numeric_limits<int64_t>::max() - updateLatency_)
      throw std::runtime_error("Address ready_cycle exceeds int64");
    for (const auto &state : states)
      updates_.insert_or_assign(state, AddressDependency{
          state, instId, cycle, cycle + updateLatency_});
  }

private:
  int64_t updateLatency_;
  std::map<std::string, AddressDependency> updates_;
};
} // namespace vfsim
#endif
