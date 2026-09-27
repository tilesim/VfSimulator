# TileSim typed program API (0.3.0)

`predict_from_program(VfSimProgram)` converts the typed object through
`program_to_canonical`, then runs `CoreVfCostModel.run_vf_info`. It does not
parse MLIR/CCE or guess storage from operand names.

## Synchronization boundary

Python core, canonical frontend, Catalog and timing/configuration data are
synchronized from `vfinfo-core-api-unification` at
`52081ae4ca8a402f96e795c05733a47c2562a656`.
The integration branch retains its program API, `dump_results=False` option
and config JSON cache from `45160741b8704a26f01e36e13a8e32c9a65a751c`.
Native/C++ implementations and generated native tables are not synchronized.

The update includes independent predicate physical registers, renaming and
lifetime tracking; predicate producer/consumer dependencies; compare, predicate
logic, MOVVP, interleave/deinterleave, dual-result loads, unaligned loads and
predicate stores; IDU POST_UPDATE address dependencies and Catalog-derived UB
transaction bandwidth. Actual opcode/forms and measured timing coverage are
specified by `configs/instruction_catalog.json` and `configs/isa.json`.
Semantic support does not imply that every timing parameter is measured.

## Typed inputs

```python
from vfsimulator import (
    StorageKind, VfSimValue, VfSimInst, VfSimProgram, predict_from_program,
)

values = {
    'buffer': VfSimValue('buffer', StorageKind.UB, 'fp32', (128,), 'allocation'),
    'x': VfSimValue('x', StorageKind.REGISTER, 'fp32'),
    'y': VfSimValue('y', StorageKind.REGISTER, 'fp32'),
    'mask': VfSimValue('mask', StorageKind.PREDICATE_REGISTER, 'bool'),
    'p': VfSimValue('p', StorageKind.PREDICATE_REGISTER, 'bool'),
}
program = VfSimProgram(values=values, body=[
    VfSimInst('PSET_B32', [], ['mask']),
    VfSimInst('VLDS', ['buffer'], ['x']),
    VfSimInst('VCMP_GT', ['x', 'x', 'mask'], ['p']),
    VfSimInst('VSEL', ['x', 'x', 'p'], ['y']),
    VfSimInst('VSTS', ['y', 'mask'], ['buffer']),
])
result = predict_from_program(program, out_dir='results/tilesim_predicates')
print(result['cycles'])
```

- Every name in src/dst must have matching metadata in `values`.
- `src` and `dst` are already separated by the caller, ordered by the Catalog's
  INPUT and OUTPUT operands respectively. They are not raw intrinsic positional
  arguments. Multiple outputs are lists of distinct names.
- Scalar operands must be explicitly supplied; the adapter never invents them.
- Form may be explicit (including external `B16/B32`, normalized to lowercase),
  or inferred using Catalog rules and typed data operands. A bool result or scalar
  does not incorrectly determine a comparison/vector instruction's precision.
- UB views share `storage_object_id`. Shape is resolved; offsets are per-dimension
  affine **element** indices; `storage_shape` preserves the contiguous allocation
  strides. These are not byte addresses. Non-contiguous layouts are not represented.
- `VfSimMembar` remains an ordered node (`VST_VLD` or `VLD_VST`); program
  `config.has_barrier` does not insert a barrier.

### Predicate contract and compatibility

Any `PredicateRegister` metadata selects the real predicate model for the entire
program. Predicate dtype must be bool. Every predicate use must have a visible
producer (PSET, compare, MOVVP or predicate logic); external predicate live-ins are
rejected by the upstream validator. Supply every required predicate input, including
masks of otherwise ordinary vector arithmetic and stores. Canonical output uses v2;
register allocation and forwarding preserve predicate dependencies across loops.
No all-active PSET is synthesized to hide a missing mask.

Older TileSim descriptions with no `PredicateRegister` retain their timing-only
v1 path: ordinary arithmetic may omit its trailing predicate, and a trailing
`Register/bool` mask is accepted and discarded as before. The legacy loop-entry
convention is retained in this path. This is **not** predicate-register modeling.
A legacy compare producing `Register/bool` is now rejected: the current Catalog
requires `PredicateRegister` output plus an explicit mask producer/input. Migrate
such callers before using them with 0.3.0; do not claim old bool-register compares
have acquired real predicate timing automatically. No TileSim DSL or MLIR input
changes are included in this synchronization, and its bundled wheel is unchanged.

## Dual-result VLDS and MLIR boundary

The frontend responsible for MLIR must lower `VLDSX2` to the equivalent intrinsic
`vlds(dst0, dst1, ptr, offset, DINTLV_B32)`.
The program API receives that **already lowered** description:

```python
VfSimInst('VLDS', src=['buffer'], dst=['x', 'y'],
          config={'catalog_mode': 'DINTLV_B32'})
```

The adapter selects the Catalog memory-mode signature, validates two distinct fp32
outputs and supplies the 128-element span; the core uses the Catalog's 512-byte UB
transaction. Offsets come from the UB view or explicit memory access below.
`VLDSX2` is rejected with a diagnostic rather than silently treated as an unknown
compute instruction. This branch does not implement the MLIR rewrite.
`VINTLV` / `VDINTLV` likewise take two sources and two destinations.

## Explicit address and alignment metadata

For POST_UPDATE, a caller can supply `VfSimInst.memory_accesses` containing
`VfSimMemoryAccess` objects (the public alias of the existing AdapterMemoryAccess
struct). For example:

```python
from vfsimulator import VfSimMemoryAccess
load = VfSimInst('VLDS', ['buffer'], ['x'], memory_accesses=(
    VfSimMemoryAccess(
        'buffer', 'read', offset=0, address_state_id='ptr',
        update_mode='post_update', post_update_delta_bytes=256,
    ),
))
```

An explicit access overrides the view-derived offset for that operand. `offset`
and `span` are in elements; `post_update_delta_bytes` is in bytes. The caller must
provide the correct pointer-state identity and already decoded delta; the program
API does not decode raw packed CCE arguments. Extra/non-UB access records, duplicate
records and directions conflicting with src/dst are rejected.

Alignment instructions use canonical attributes in config:
`align_state_id` and Catalog-defined `align_state_operation` (for example PSTU
`append`, VSTAS `consume`, VLDAS `load_init`, VLDUS `load_use`). PSTU also requires
its implicit pointer update (8 bytes for b32/uint32, 16 for b16/uint16); omission
is an error. Catalog-defined memory spans are supplied when not explicitly set.
Use the same state identity only for operations sharing that pointer/alignment state.

Other config fields are source annotations, not raw intrinsic argument parsing;
`uarch` holds validated runtime controls. TileSim retains responsibility for
filtering/reporting its placeholders. Adapter errors propagate; no new timing
fallback is introduced.

## Outputs, installation and validation

`cycles`, `model`, `raw`, `trace_path` remain result fields.
`dump_trace_path` writes canonical JSON, not a timeline.
`out_dir/trace.json` is the instruction timeline when `dump_results=True` (default).
`dump_results=False` skips all per-run output files; an explicitly requested
canonical `dump_trace_path` still works independently.

After editing root Python sources:

```bash
python tools/sync_python_package.py
python tools/sync_python_package.py --check
python -m pytest -q tests -k 'not generated_cpp'
python -m pip install build wheel
python -m build --wheel
python -m pip install --force-reinstall dist/vfsimulator-0.3.0-py3-none-any.whl
```

Use a Python environment with pytest and optional jsonschema for the full tests.
The two generated-C++ consistency tests are excluded because native tables were
intentionally not synchronized. Native executable comparisons are skipped unless
a matching `VFSIM_NATIVE_RUNNER` is supplied; an old runner is not compatible.

The wheel contains the `vfsimulator` namespace, including Catalog JSON and schema
resources. Never add root `core`/`api` packages to TileSim's import path. Install and
validate the new wheel in an isolated environment before replacing TileSim's wheel,
especially for legacy comparison descriptions. The typed program path itself does
not require jsonschema; JSON schema validation optionally uses `vfsimulator[json]`.
