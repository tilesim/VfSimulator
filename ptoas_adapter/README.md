# PTOAS Adapters

The adapters accept MLIR operations through generic operation names, attributes,
and printed types. They do not link the PTO dialect. Both construct
CanonicalVfInfo and use the C++ core directly; Python and CCE files are not part
of this integration.

## Layout And Entry Points

| Directory | Entry point | Input | Output attributes |
| --- | --- | --- | --- |
| `tileop/` | `planTileFusionIR()` | TileOps carrying fusion group/order and TileLib candidates | `pto.fusion.row_unroll_factor`, `pto.fusion.col_unroll_factor`, both i64 |
| `vmi/` | `planVmiUnrollIR()` | Low-level VPTO vector scopes containing marked fused loops | `pto.vfsim.unroll_factor`, i32 |

`PlannerOptions.h` holds shared search options. `IRPlanner.h` is an umbrella
header; consumers can instead include the individual adapter header.

## Modeling Contract

TileOp selects the first non-VMI TileLib candidate and models its 1D or 2D
traversal. It respects valid shapes and DPS write instances, including
intermediate results used outside the fusion group. Unsupported types or
operations are diagnosed and skipped. The legacy search evaluates divisible
factors in both ABCABC and AABBCC order.

VMI selects `scf.for` operations marked with both `pto.vmi.loop_fused` and
`pto.vmi.loop_fusion.id`, with a static trip count greater than one. Each
candidate models the enclosing vector scope. ABCABC and AABBCC factors include
non-divisors, with a residual loop. The adapter writes one factor regardless of
the winning ordering and does not overwrite the handwritten `pto.unroll_factor`.
The PTOAS wrapper retains responsibility for fusion-marker cleanup, unrolling,
and scheduling. This migration does not introduce an IR-reordering interface.

Both searches cap the factor at 8 and at the modeled trip count. Register
bitcasts remain zero-instruction views: a view shares the emitted value's
logical ID, producer and total bit width. Validation checks that relationship;
execution resolves the view to the same dynamic register definition.

Each planner reuses one CoreVfCostModel across its candidates. Candidates pass
through InputAPI validation before simulation. Logs are disabled in the core
unless explicitly requested elsewhere; `dumpCandidates` prints planner results.
A failed baseline skips that group/loop; a failed alternative skips only that
candidate. Existing annotations are unchanged when planning is skipped.

## Build And Integration

Configure with `VFSIM_ENABLE_MLIR_PLANNER=ON` and an LLVM/MLIR CMake package.
The adapters are optional, so standalone simulator builds need no MLIR.

PTOAS must add this repository's root directory, not the old `native/` directory,
and include `ptoas_adapter/tileop/TileOpPlanner.h` or
`ptoas_adapter/vmi/VmiUnrollPlanner.h`. Link `vfsim::tileop_adapter` or
`vfsim::vmi_adapter`; `vfsim::ir_planner` links both for existing consumers.

With `VFSIM_BUILD_TESTS=ON`, CTest also runs `vfsim_ptoas_adapter_test`.
It uses generic PTO operations without loading the PTO dialect, checking the
adapter boundary separately from PTOAS's real dialect and pass-pipeline tests.
