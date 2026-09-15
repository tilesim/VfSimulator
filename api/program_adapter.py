"""Translate the typed TileSim program into the master canonical frontend."""
from dataclasses import replace

from api.frontend.adapter_ir import (
    AdapterInstruction, AdapterLoop, AdapterMembar, AdapterMemoryAccess,
    AdapterProgram, AdapterValue,
)
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG, OperandDirection, ArgumentKind, FormRule
from api.frontend.schema import CanonicalStorageObject, StorageKind, OperandRole
from api.frontend.value_versioning import ValueVersioningPass
from api.input_symbols import normalize_opcode, compact_dtype
from core.program_ir import VfSimInst, VfSimLoop, VfSimMembar, VfSimProgram


class _ProgramValueVersioning(ValueVersioningPass):
    """Reuse master definition/loop handling, retaining explicit UB aliases."""

    def __init__(self, values):
        super().__init__()
        self.program_values = values

    def _new_definition(self, logical_id, *, producer_node_id):
        metadata = self.program_values[logical_id]
        existing_objects = dict(self._storage_objects) if metadata.storage is StorageKind.UB else None
        definition_id = super()._new_definition(logical_id, producer_node_id=producer_node_id)
        if metadata.storage is StorageKind.UB:
            old = self._values[definition_id]
            # The master pass creates a per-name object; this API supplies the
            # actual allocation identity, shared by all views of that allocation.
            self._storage_objects = existing_objects
            self._storage_objects[metadata.storage_object_id] = CanonicalStorageObject(
                object_id=metadata.storage_object_id, storage=StorageKind.UB,
            )
            self._values[definition_id] = replace(old, storage_object_id=metadata.storage_object_id)
        return definition_id


def _element_offset(value):
    offset = 0
    for dimension, index in zip(value.storage_shape or value.shape, value.offsets):
        if isinstance(offset, int) and isinstance(index, int):
            offset = offset * dimension + index
        else:
            offset = f"({offset}) * {dimension} + ({index})"
    return offset


def program_to_canonical(program: VfSimProgram):
    """Names are opaque; every referenced operand must have explicit metadata.

    src/dst contain catalog data operands, optionally followed by a bool mask
    on src for instructions whose catalog marks predicates as ignored. Missing
    required operands, including scalar inputs, are errors. config carries source annotations only;
    scheduling controls belong to uarch and barriers are explicit body nodes.
    """
    if not isinstance(program, VfSimProgram):
        raise TypeError('program must be a VfSimProgram')
    values = program.values

    def convert(node):
        if isinstance(node, VfSimLoop):
            return AdapterLoop(count=node.count, unroll=node.unroll,
                               induction_variable=node.name, body=[convert(n) for n in node.body])
        if isinstance(node, VfSimMembar):
            return AdapterMembar(node.barrier)
        if not isinstance(node, VfSimInst):
            raise TypeError(f'Unsupported program node: {type(node).__name__}')
        for name in node.src + node.dst:
            if name not in values:
                raise ValueError(f'{node.op}: operand {name!r} requires explicit metadata')
        src = list(node.src)
        opcode = normalize_opcode(node.op)
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(opcode)
        if spec is not None:
            inputs = [p for p in spec.operands if p.direction is OperandDirection.INPUT]
            outputs = [p for p in spec.operands if p.direction is OperandDirection.OUTPUT]
            ignores_mask = any(p.direction is OperandDirection.IGNORE and p.kind is ArgumentKind.PREDICATE for p in spec.operands)
            missing_scalar_with_mask = (len(src) == len(inputs) and inputs and inputs[-1].kind is ArgumentKind.SCALAR and src and values[src[-1]].dtype == "bool")
            if ignores_mask and (len(src) == len(inputs) + 1 or missing_scalar_with_mask):
                mask = values[src[-1]]
                if mask.storage is not StorageKind.REGISTER or mask.dtype != 'bool':
                    raise ValueError(f'{node.op}: trailing predicate must be a bool register')
                src.pop()
            if len(src) != len(inputs) or len(node.dst) != len(outputs):
                raise ValueError(f'{node.op}: expected {len(inputs)} sources and {len(outputs)} destinations; got {len(src)} and {len(node.dst)}')
        accesses = tuple(
            AdapterMemoryAccess(name, kind, offset=_element_offset(values[name]))
            for names, kind in ((src, 'read'), (node.dst, 'write'))
            for name in names if values[name].storage is StorageKind.UB
        )
        form = node.form
        if form is None and spec is not None:
            if spec.fixed_form:
                form = spec.fixed_form
            elif spec.form_rule is FormRule.CONVERSION:
                form = f"{compact_dtype(values[src[0]].dtype)}_to_{compact_dtype(values[node.dst[0]].dtype)}"
            else:
                # Follow catalog data roles: memory element type first, then
                # numeric register result/input types. A bool result is a mask,
                # not the data precision (the catalog may call it a destination).
                # Scalars likewise do not set the width of a register broadcast.
                operands = list(zip(outputs, node.dst)) + list(zip(inputs, src))
                candidates = [
                    values[name]
                    for role in (OperandRole.MEMORY, OperandRole.DESTINATION, OperandRole.SOURCE)
                    for operand, name in operands
                    if operand.role is role
                    and values[name].storage in (StorageKind.UB, StorageKind.REGISTER)
                    and values[name].dtype != 'bool'
                ]
                if not candidates:
                    raise ValueError(f'{node.op}: cannot infer form without a typed data operand; supply form explicitly')
                form = candidates[0].dtype
                if form not in spec.forms:
                    form = {"bf16": "b16", "int32": "b32", "uint32": "b32"}.get(form, form)
        if form is not None:
            opcode, form = DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(opcode, form)
        return AdapterInstruction(name=opcode, src=src, dst=list(node.dst),
                                  form=form, memory_accesses=accesses,
                                  attributes=dict(node.config or {}))

    adapter = AdapterProgram(
        context=[convert(node) for node in program.body],
        values={name: AdapterValue(name, v.storage.value, v.dtype, v.shape) for name, v in values.items()},
        params=dict(program.params or {}), default_dtype=program.dtype,
        uarch=dict(program.uarch or {}),
    )
    return _ProgramValueVersioning(values).run(adapter, source={'adapter': 'tilesim_program', **(program.config or {})})
