#pragma once
#include <memory>
#include <vector>
#include "core/execution/prepare.h"

namespace vfsim {

// Diagnostic split of the per-event lowering cost. Only filled when the
// diagnostic switch is on; timed scoring runs leave it untouched.
struct LoweringProfile {
  int64_t events = 0;
  int64_t expandNs = 0;
  int64_t schedulingNs = 0;
  int64_t blockBoundaryNs = 0;
  int64_t lifetimeNs = 0;
};

class CanonicalCursor {
public:
  virtual ~CanonicalCursor() = default;
  virtual bool next(DynamicInst &out) = 0;
  virtual bool exhausted() const = 0;
  virtual const char *declineReason() const = 0;
  virtual const LoweringProfile *loweringProfile() const { return nullptr; }
};

}  // namespace vfsim
