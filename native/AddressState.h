#ifndef VFSIM_NATIVE_ADDRESS_STATE_H
#define VFSIM_NATIVE_ADDRESS_STATE_H

#include "api/native/CanonicalVfInfo.h"
#include <algorithm>
#include <memory>
#include <optional>
#include <unordered_map>
#include <vector>

namespace vfsim {

struct AddressEvent {
  int64_t instId;
  std::optional<int64_t> startCycle;
};

struct AddressBinding {
  std::string stateId;
  std::shared_ptr<AddressEvent> event;
  std::vector<std::pair<std::shared_ptr<AddressEvent>, int64_t>> dependencies;

  bool canIssue(int64_t cycle) const {
    for (const auto &[producer, delay] : dependencies)
      if (!producer->startCycle || cycle < *producer->startCycle ||
          cycle - *producer->startCycle < delay)
        return false;
    return true;
  }
};

// Shared events survive queue copies and producer retirement. Only the latest
// update and not-yet-started readers are retained by each pointer state.
class AddressStateTracker {
public:
  int64_t updateLatency = 1;

  std::vector<AddressBinding> bind(
      int64_t instId, const std::vector<CanonicalMemoryAccess> &accesses) {
    std::unordered_map<std::string, bool> modes;
    for (const auto &access : accesses)
      if (access.addressStateId)
        modes[*access.addressStateId] = modes[*access.addressStateId] ||
                                       access.updateMode == "post_update";
    std::vector<AddressBinding> result;
    for (const auto &[state, updates] : modes) {
      auto event = std::make_shared<AddressEvent>(AddressEvent{instId, std::nullopt});
      AddressBinding binding{state, event, {}};
      if (auto it = updates_.find(state); it != updates_.end())
        binding.dependencies.emplace_back(it->second, updateLatency);
      auto &readers = readers_[state];
      prune(readers);
      if (updates) {
        for (const auto &reader : readers)
          binding.dependencies.emplace_back(reader, 0);
        updates_[state] = event;
        readers.clear();
      } else {
        readers.push_back(event);
      }
      result.push_back(std::move(binding));
    }
    return result;
  }

  void notifyStart(const std::vector<AddressBinding> &bindings, int64_t cycle) {
    for (const auto &binding : bindings) {
      binding.event->startCycle = cycle;
      prune(readers_[binding.stateId]);
    }
  }

private:
  static void prune(std::vector<std::shared_ptr<AddressEvent>> &readers) {
    readers.erase(std::remove_if(readers.begin(), readers.end(),
                                [](const auto &r) { return r->startCycle.has_value(); }),
                  readers.end());
  }
  std::unordered_map<std::string, std::shared_ptr<AddressEvent>> updates_;
  std::unordered_map<std::string, std::vector<std::shared_ptr<AddressEvent>>> readers_;
};
} // namespace vfsim
#endif
