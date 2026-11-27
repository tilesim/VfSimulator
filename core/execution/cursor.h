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

// Applies the frozen LICM rewrite once and prepares the cursor on the result.
std::unique_ptr<CanonicalCursor> makeCanonicalCursor(
    const CanonicalVfInfo &, const ParamDB &, const UarchConfig &,
    bool diagnostics = true);

// Prepares the cursor for a caller that already applied the frozen LICM rewrite.
// The cursor takes ownership of `context`, which must be the very context the
// caller expands (prepareProgramForStream(..., skipHoist=true)), so the program
// and the plan share one post-LICM context and LICM runs exactly once.
// With diagnostics=false the cursor lowers in scoring mode: compact value
// identities ("@<n>"), no per-instance provenance, and no per-instruction
// block-key strings that only the diagnostic oracle consumes. Every field the
// Engine reads (storage arrays, destination counts, lifetime bits, scheduling
// keys, block boundaries) is still produced.
std::unique_ptr<CanonicalCursor> makeCanonicalCursorForContext(
    const CanonicalVfInfo &, std::vector<CanonicalNode> context,
    const ParamDB &, const UarchConfig &, bool diagnostics = true);

}  // namespace vfsim
