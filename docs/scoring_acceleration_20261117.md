# Scoring acceleration validation (2026-11-17)

## Scope

- `wch/VfSimulator-refactor`: Python and native source-readiness cache; skip
  trace event construction only for headless public scoring.
- `wch/VfSim-tilesim`: same Python changes, including generated
  `vfsimulator/` package sources.
- `wch/vfsim-cpp-only`: already contains the broader acceleration PR.
  Local branch fast-forwarded to `64e0ec1`; no additional code edits.

No timing parameters, forwarding/II formulas, dispatch policies, or queue
capacities were changed. Direct Engine use retains tracing by default.
Only immutable producer readiness is cached; pending producers and queue
admission readiness are still checked.

## Measurements

12 canonical inputs, warm-up plus five timed headless runs per input,
median per input, sum of medians below. Parsing is excluded. Runs are
serial on the same machine; filesystem and timing noise remain.

| Implementation | Before ms | After ms | Speedup |
|---|---:|---:|---:|
| Refactor Python | 3137.40 | 2821.79 | 1.11x |
| TileSim Python | 3240.53 | 2561.81 | 1.26x |
| Refactor native | 588.44 | 403.69 | 1.46x |
| C++-only PR | 500.34 | 98.94 | 5.06x |

C++-only compares pre-PR `7e89afc` against `64e0ec1` (same tree as
`681dbc2`), not an additional improvement over the already merged PR.
The two Python cores use the same changes; their timing difference should
not be interpreted as a branch-specific algorithmic improvement.

## Equivalence and tests

Inputs: GeLU I16 U1, GeLU I96 U1/U4/U8, Online Update I64,
source fanout, branch live range, store-capture reuse, SwiGLU I96,
Softmax expdif VCVT U1/U4, and a predicate/memory/barrier loop.

- All 12 inputs: before/after prediction results match in all four runs.
- Python: complete event-history/start/done hashes match in both branches.
- Refactor native: 109 output files across 12 cases match byte-for-byte.
- Refactor unittest: 509 tests, 20 skipped, no failures.
- Refactor native CTest: 8/8 passed.
- TileSim pytest: 463 passed, 15 skipped; after adding the packaged
  headless-scoring regression, the 12 package/acceleration tests also passed.
- C++-only CTest: 10/10 passed (build from identical PR source tree).

Tests explicitly cover unresolved sources, cached resolved prefixes,
changing queue admission floors, and traced/headless result equality.
These checks are regression evidence, not a proof for every possible input.

Local raw runs and scripts:
`/tmp/vfsim-branch-accel-20261117/`.
Python baseline commits: refactor `97c1e122`, TileSim `e3b6859b`.
