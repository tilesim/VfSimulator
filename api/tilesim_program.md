# TileSim typed program API (0.2)

`predict_from_program` takes a `VfSimProgram` object, converts it with
`program_to_canonical`, then calls the master `CoreVfCostModel.run_vf_info`.
No legacy trace dictionary or operand-prefix inference is used.

```python
from vfsimulator import StorageKind, VfSimValue, VfSimInst, VfSimProgram, predict_from_program
program = VfSimProgram(
    values={
        'input': VfSimValue('input', StorageKind.UB, 'fp32', (64,), 'buffer'),
        'reg': VfSimValue('reg', StorageKind.REGISTER, 'fp32'),
        'scale': VfSimValue('scale', StorageKind.SCALAR, 'fp32'),
    },
    body=[VfSimInst('VLDS', ['input'], ['reg']),
          VfSimInst('VMULS', ['reg', 'scale'], ['reg'])],
)
cycles = predict_from_program(program)['cycles']
```

- Names are opaque and preserved. Each referenced name must have typed metadata.
- `src`/`dst` follow the master catalog's data operands. Required scalar operands
  must be explicitly supplied by TileSim; the adapter never invents them.
- A trailing bool register predicate is accepted only where the catalog marks a
  predicate as ignored by the timing model. It is not a canonical data operand.
- `form` is forwarded explicitly or derived from typed inputs/outputs by the
  master frontend. Mixed-type conversion uses distinct typed input/output names.
- UB `storage_object_id` identifies the actual allocation and may be shared by
  multiple views. Offsets are element indices, not bytes. Shapes must be resolved.
  Sliced views carry `storage_shape` (the contiguous allocation shape) so a
  shortened view width does not change its row stride. Non-contiguous layouts
  are not represented by this API.
  Access span stays unspecified, preserving conservative memory dependencies.
- `VfSimMembar` accepts `VST_VLD` and `VLD_VST`; it is an ordered body node.
  `config.has_barrier` is source metadata, not an implicit memory barrier.
- `config` stores annotations; `uarch` stores validated master runtime controls.
  Loop unroll and dynamic instruction limits use the master rules.
- TileSim filters and reports its own placeholders. Adapter errors propagate;
  the master timing fallback is unchanged.
- `dump_trace_path` now writes canonical JSON. `cycles`, `model`, `raw`, and
  `trace_path` remain result fields; legacy `payload`/`to_payload` are removed.

## Source and package maintenance

The Python core, canonical frontend and configs are synchronized from master
`d9b19f3fd5d32b50b680f4d3e9782e824d0849bc`. Native sources are not synchronized.
Run `python tools/sync_python_package.py` after root Python edits and
`python tools/sync_python_package.py --check` before building the wheel.
The generator changes absolute imports to the `vfsimulator` namespace and copies
JSON resources. JSON loading optionally requires `pip install vfsimulator[json]`;
the typed program path does not require jsonschema.
