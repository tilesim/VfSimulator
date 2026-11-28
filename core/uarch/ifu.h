#pragma once
#include "core/execution/instruction.h"
#include <functional>
#include <memory>
namespace vfsim {
class IFU {
public:
  using DoneFn = std::function<bool()>;
  using FetchFn = std::function<const DynamicInst &()>;
  using FetchIntoFn = std::function<void(DynamicInst &)>;

  explicit IFU(const std::vector<DynamicInst> &instructions)
      : pending_(instructions.begin(), instructions.end()) {}
  struct Borrowed {};
  IFU(const std::vector<DynamicInst> &instructions, Borrowed)
      : borrowed_(&instructions) {}
  void setProvider(DoneFn done, FetchIntoFn fetch) {
    providerDone_ = std::move(done);
    providerFetch_ = std::move(fetch);
    hasProvider_ = true;
    borrowed_ = nullptr;
  }
  bool hasProvider() const { return hasProvider_; }
  // Detaches the provider. The callbacks reference caller state that only lives
  // for the duration of one run, so the owner must detach before that state goes
  // out of scope; the IFU then behaves like an empty program (and the caller is
  // expected to restore the identity mode the eager path needs).
  void clearProvider() {
    providerDone_ = {};
    providerFetch_ = {};
    hasProvider_ = false;
  }
  bool done() const {
    if (hasProvider_)
      return providerDone_();
    return borrowed_ ? next_ == borrowed_->size() : pending_.empty();
  }
  const DynamicInst &nextBorrowed() {
    if (hasProvider_)
      throw std::runtime_error("owned provider cannot borrow");
    return borrowed_->at(next_++);
  }
  void nextInto(DynamicInst &out) { providerFetch_(out); }
  DynamicInst nextInst() {
    auto inst = std::move(pending_.front());
    pending_.pop_front();
    return inst;
  }

private:
  std::deque<DynamicInst> pending_;
  const std::vector<DynamicInst> *borrowed_ = nullptr;
  size_t next_ = 0;
  bool hasProvider_ = false;
  DoneFn providerDone_;
  FetchIntoFn providerFetch_;
};
} // namespace vfsim
