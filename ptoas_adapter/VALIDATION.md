# Migration Validation

Validated on the remote development server on 2026-10-15/16. No local builds
or simulator tests were run.

## Versions And Scope

- Branch: `vfsim-ptoas-vmi`, based on `wch/vfsim-cpp-only` at `e08eb3c`.
- Adapter source: the PTOAS-pinned VfSimulator at `f4ba4ec`.
- PTOAS baseline: `95492c371`, in an isolated source/build directory.
- Toolchain: GCC 10.3.1, LLVM/MLIR 19.1.7, Python 3.10 for PTOAS only.
- Parallel build limit: `-j16`.

PTOAS compatibility changes are limited to the root CMake entry and the two
adapter header includes. No pass ordering or existing PTOAS test was changed.
The main checkout and its submodule were not switched.

## Results

| Validation | Result |
| --- | --- |
| Standalone C++ core and optional MLIR adapter build | Passed |
| PTOAS Python CLI package and `pto-test-opt` build | Passed |
| CTest | 10/10 passed |
| Existing focused PTOAS lit regressions | 7/7 passed |
| VMI end-to-end, costmodel disabled | Passed |
| VMI end-to-end, costmodel enabled, scheduler off/on | Passed |
| Legacy end-to-end unroll test | Passed |

The VMI six-op chain evaluates 15 candidates: baseline and both orderings for
factors 2 through 8. It selects AABBCC factor 7 at 309 cycles, versus baseline
313. The trip-count-2 pair evaluates baseline, ABCABC(2), and AABBCC(2), and
retains factor 1 at 60 cycles. The selected cycle was checked against the
minimum logged candidate cycle, the i32 attr was present in the planner dump,
and the downstream unroller consumed the attr. Both scheduler settings passed.

Legacy selects factor 1 for the two-op pair and AABBCC factor 8 for the six-op
chain. Existing lit checks verify the row/col attrs, fusion-region propagation,
loop step, and downstream consumption.

The adapter C++ tests additionally cover first non-VMI candidate selection,
1D/2D traversal, compact valid shapes, externally visible intermediate stores,
UB cast pointers, bitcast views, non-divisible tails, factor bounds, preservation
of handwritten attrs, duplicate reduction entries and UB pointer iter_args.
ABCABC and AABBCC dynamic instruction orders must differ, while retaining every
original iteration. A separate core test checks that bit views retain their
dynamic producer, reject invalid produced views, and share external registers.

## Compatibility Fixes

The old bitcast adapter emitted typed register views sharing a logical ID and
producer. The new core initially rejected these as non-emitted definitions.
Validation and expansion now recognize equal-bit-width views and resolve them
to the original dynamic register without adding an instruction or cycle.

An unused CCE parser helper was removed because PTOAS's existing `-Werror`
policy rejected it. No compiler warnings were suppressed.

`git diff --check` passes. The changed-code prefilter's brace findings were
reviewed against balanced control conditions: its regex incorrectly matches
nested function calls in already-braced statements. No true brace violation
remains. This does not replace a full static analyzer or sanitizer run.

This is compiler/IR integration validation, not an NPU numerical-accuracy or
hardware timing calibration run. It does not implement the future combined
unroll-and-schedule IR optimization interface.
