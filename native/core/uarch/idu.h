#pragma once
#include "native/core/execution/loop_control.h"
#include <limits>
#include <map>
#include <set>
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
      throw std::runtime_error(
          "idu_post_update_ready_latency must be positive");
  }

  std::vector<AddressDependency>
  dependencies(const std::vector<CanonicalMemoryAccess> &accesses) const {
    std::set<std::string> states;
    for (const auto &access : accesses)
      if (access.addressStateId)
        states.insert(*access.addressStateId);
    std::vector<AddressDependency> result;
    for (const auto &state : states)
      if (auto it = updates_.find(state); it != updates_.end())
        result.push_back(it->second);
    return result;
  }

  bool canDispatch(const std::vector<CanonicalMemoryAccess> &accesses,
                   int64_t cycle) const {
    for (const auto &d : dependencies(accesses))
      if (cycle < d.readyCycle)
        return false;
    return true;
  }

  void notifyDispatch(int64_t instId,
                      const std::vector<CanonicalMemoryAccess> &accesses,
                      int64_t cycle) {
    std::set<std::string> states;
    for (const auto &access : accesses)
      if (access.addressStateId && access.updateMode == "post_update")
        states.insert(*access.addressStateId);
    if (!states.empty() &&
        cycle > std::numeric_limits<int64_t>::max() - updateLatency_)
      throw std::runtime_error("Address ready_cycle exceeds int64");
    for (const auto &state : states)
      updates_.insert_or_assign(
          state,
          AddressDependency{state, instId, cycle, cycle + updateLatency_});
  }

private:
  int64_t updateLatency_;
  std::map<std::string, AddressDependency> updates_;
};
struct IDUDispatchBudget {
  int64_t freePreg = 0;
  int64_t freeShqQueue = 0;
  int64_t freeLsq = 0;
  int64_t freeShq = 0;
  int64_t issueBudget = 0;
  bool theoreticalLimitMode = false;
  bool theoreticalLimitVloopOnly = false;
  int64_t freePredicate = 32;
};

struct IDUDispatchRecord {
  int64_t cycle = 0;
  int64_t instId = 0;
  std::string op;
  std::vector<std::string> dst;
  std::vector<std::string> src;
  int64_t topBlockId = 0;
  int64_t vreg = 0;
  int64_t shqQueue = 0;
  int64_t lsq = 0;
  int64_t shq = 0;
  std::string staticInstructionId;
  std::vector<IterationIdentity> iterationPath;
  int64_t streamSeq = -1;
  std::vector<AddressDependency> addressDependencies;
  std::string event = "dispatch";
  int64_t predicatePhysFree = 0;
  std::string blockedReason = "address_state";
};

class IDU {
public:
  IDU(const UarchConfig &cfg, const ParamDB &db, LoopControl &loops,
      const ValueStorageLookup &values)
      : cfg_(cfg), loops_(loops), valueStorage_(values),
        addressStates_(cfg.iduPostUpdateReadyLatency),
        vfStartupCost_(db.isaDefaults().vfStartupCost) {}
  bool empty() const { return window_.empty(); }
  bool canAccept() const {
    return cfg_.theoreticalLimitMode ||
           static_cast<int64_t>(window_.size()) < cfg_.iduWindowWidth;
  }
  void accept(const DynamicInst &inst) {
    if (!canAccept())
      throw std::runtime_error("IDU window is full");
    window_.push_back(inst);
  }
  std::vector<DynamicInst> dispatch(int64_t cycle,
                                    const IDUDispatchBudget &budget);
  const std::deque<DynamicInst> &window() const { return window_; }
  const std::vector<IDUDispatchRecord> &dispatchLog() const {
    return dispatchLog_;
  }
  const std::vector<IDUDispatchRecord> &addressBlockLog() const {
    return addressBlockLog_;
  }
  const std::vector<IDUDispatchRecord> &resourceBlockLog() const {
    return resourceBlockLog_;
  }

private:
  const UarchConfig &cfg_;
  LoopControl &loops_;
  ValueStorageLookup valueStorage_;
  AddressStateTracker addressStates_;
  int64_t vfStartupCost_;
  std::deque<DynamicInst> window_;
  std::vector<IDUDispatchRecord> dispatchLog_, addressBlockLog_,
      resourceBlockLog_;
};
} // namespace vfsim
