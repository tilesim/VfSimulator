// Copyright (c) 2026 Huawei Technologies Co., Ltd.
// SPDX-License-Identifier: CANN-1.0
#pragma once
#include <cstdint>
#include <deque>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <vector>
namespace vfsim {
// Physical register bank.
//
// The public string API below is the ORIGINAL implementation (same names,
// signatures, freelist and allocated-set semantics), so external callers keep
// byte-for-byte behaviour: names are compared as strings, explicit dynamic ids
// do not advance the implicit counter, duplicates throw, releases return the
// name to the free FIFO.
//
// The simulator hot path uses the separate *Slot API: dense int64 slots with a
// FIFO deque and a bitmap, no string/hash work for the bounded configuration,
// and sparse int64 slots above the bounded range for the theoretical one.
class PhysicalRegisterBank {
public:
  PhysicalRegisterBank(int64_t capacity, std::string prefix)
      : capacity_(capacity), prefix_(std::move(prefix)) {
    if (capacity <= 0)
      throw std::runtime_error("Physical register capacity must be positive");
    for (int64_t i = 0; i < capacity; ++i)
      free_.push_back(prefix_ + std::to_string(i));
    for (int64_t i = 0; i < capacity; ++i)
      slotFree_.push_back(i);
    slotAllocated_.assign(static_cast<size_t>(capacity), 0);
  }
  // ---- original public string API (unchanged behaviour) ----
  std::string allocate(bool unlimited = false, int64_t dynamicId = -1) {
    std::string id;
    if (unlimited)
      id = dynamicId >= 0 ? "p" + std::to_string(dynamicId)
                          : prefix_ + std::to_string(next_++);
    else {
      if (free_.empty())
        throw std::runtime_error("Physical register pool exhausted");
      id = free_.front();
      free_.pop_front();
    }
    legacyUsed_ = true;
    if (!allocated_.insert(id).second)
      throw std::runtime_error("Corrupt physical register freelist");
    return id;
  }
  bool allocated(const std::string &id) const {
    legacyUsed_ = true;
    return allocated_.count(id);
  }
  void release(const std::string &id) {
    if (!allocated_.erase(id))
      throw std::runtime_error("Duplicate physical register release");
    free_.push_back(id);
  }
  // Credit queries follow whichever API the caller uses: simulator credit
  // checks go through the numeric path, the legacy string API keeps the
  // historical string freelist view (unit tests rely on it).
  int64_t freeCount() const {
    return static_cast<int64_t>(legacyUsed_ ? free_.size() : slotFree_.size());
  }

  // ---- numeric hot path ----
  int64_t allocateSlot(bool unlimited = false, int64_t dynamicId = -1) {
    if (unlimited) {
      const int64_t id = capacity_ + nextUnnamed_++;
      const std::string name = dynamicId >= 0
                                   ? "p" + std::to_string(dynamicId)
                                   : prefix_ + std::to_string(nextUnnamed_ - 1);
      // Duplicate names must throw, including collisions with a live bounded
      // slot name (the original allocated_ set spans both modes).
      if (!dynamicNameSlots_.emplace(name, id).second ||
          boundedNameLive(name))
        throw std::runtime_error("Corrupt physical register freelist");
      dynamicSlots_.insert(id);
      dynamicNames_[id] = name;
      return id;
    }
    if (slotFree_.empty())
      throw std::runtime_error("Physical register pool exhausted");
    const int64_t id = slotFree_.front();
    slotFree_.pop_front();
    if (slotAllocated_[static_cast<size_t>(id)])
      throw std::runtime_error("Corrupt physical register freelist");
    slotAllocated_[static_cast<size_t>(id)] = 1;
    return id;
  }
  bool allocatedSlot(int64_t id) const {
    if (id < 0)
      return false;
    if (dynamicSlots_.count(id) != 0)
      return true;
    return static_cast<size_t>(id) < slotAllocated_.size() &&
           slotAllocated_[static_cast<size_t>(id)] != 0;
  }
  void releaseSlot(int64_t id) {
    if (dynamicSlots_.erase(id) != 0) {
      dynamicNameSlots_.erase(dynamicNames_[id]);
      dynamicNames_.erase(id);
      return;
    }
    if (id < 0 || static_cast<size_t>(id) >= slotAllocated_.size() ||
        slotAllocated_[static_cast<size_t>(id)] == 0)
      throw std::runtime_error("Duplicate physical register release");
    slotAllocated_[static_cast<size_t>(id)] = 0;
    slotFree_.push_back(id);
  }
  std::string name(int64_t id) const {
    const auto found = dynamicNames_.find(id);
    if (found != dynamicNames_.end())
      return found->second;
    return prefix_ + std::to_string(id);
  }

private:
  // True when the name denotes a live bounded slot (used to reject a duplicate
  // dynamic allocation of an existing bounded name).
  bool boundedNameLive(const std::string &id) const {
    if (id.size() <= prefix_.size() ||
        id.compare(0, prefix_.size(), prefix_) != 0)
      return false;
    int64_t value = 0;
    for (size_t i = prefix_.size(); i < id.size(); ++i) {
      const char c = id[i];
      if (c < '0' || c > '9')
        return false;
      value = value * 10 + (c - '0');
      if (value >= capacity_)
        return false;
    }
    return slotAllocated_[static_cast<size_t>(value)] != 0;
  }
  int64_t capacity_, next_ = 0, nextUnnamed_ = 0;
  mutable bool legacyUsed_ = false;
  std::string prefix_;
  // original string state
  std::deque<std::string> free_;
  std::unordered_set<std::string> allocated_;
  // numeric state
  std::deque<int64_t> slotFree_;
  std::vector<uint8_t> slotAllocated_;
  std::unordered_set<int64_t> dynamicSlots_;
  std::unordered_map<int64_t, std::string> dynamicNames_;
  std::unordered_map<std::string, int64_t> dynamicNameSlots_;
};
} // namespace vfsim
