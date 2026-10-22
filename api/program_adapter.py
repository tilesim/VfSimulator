"""Translate the typed TileSim program into the master canonical frontend."""
from dataclasses import replace

from api.frontend.adapter_ir import (
    AdapterInstruction, AdapterLoop, AdapterMembar, AdapterMemoryAccess,
    AdapterProgram, AdapterValue,
)
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG, OperandDirection, ArgumentKind, FormRule
from api.frontend.schema import CanonicalStorageObject, StorageKind, OperandRole
from api.frontend.value_versioning import ValueVersioningPass
from api.input_symbols import normalize_opcode, compact_dtype, normalize_form
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

    PredicateRegister enables strict predicate dependencies for the entire program.
    Older programs without it retain v1 timing-only mask handling. Missing scalar
    inputs are errors in both modes. config includes canonical instruction
    attributes; typed memory_accesses supplies address-state metadata.
    """
    if not isinstance(program, VfSimProgram):
        raise TypeError('program must be a VfSimProgram')
    values = program.values
    predicate_model = any(v.storage is StorageKind.PREDICATE_REGISTER for v in values.values())

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
        if opcode in {'VBITSORT', 'VBS32', 'VMRGSORT4', 'VMS4', 'VMS4V2'}:
            raise ValueError(f'{opcode} is a legacy UB VEC instruction; use the legacy UB timing API, not VfSimProgram')
        if opcode == 'VLDSX2':
            raise ValueError('VLDSX2 must be lowered by the caller to VLDS with catalog_mode=DINTLV_B32')
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(opcode)
        attributes = dict(node.config or {})
        mode = attributes.get('catalog_mode')
        if mode is not None:
            if spec is None or mode not in spec.memory_modes:
                raise ValueError(f'{node.op}: unsupported catalog_mode {mode!r}')
            spec = spec.memory_modes[mode]
        if spec is not None:
            inputs = [p for p in spec.operands if p.direction is OperandDirection.INPUT]
            outputs = [p for p in spec.operands if p.direction is OperandDirection.OUTPUT]
            if not predicate_model:
                # Preserve the old timing-only contract, without fabricating PSET.
                if any(p.kind is ArgumentKind.PREDICATE for p in outputs):
                    raise ValueError(f'{node.op}: predicate results require PredicateRegister metadata and explicit producers/masks')
                predicates = [p for p in inputs if p.kind is ArgumentKind.PREDICATE]
                if predicates and (len(predicates) != 1 or inputs[-1] is not predicates[0]
                                   or len(inputs) == 1):
                    raise ValueError(f'{node.op}: predicate data requires PredicateRegister metadata')
                inputs = [p for p in inputs if p.kind is not ArgumentKind.PREDICATE]
                if predicates and len(src) == len(inputs) + 1:
                    mask = values[src[-1]]
                    if mask.storage is not StorageKind.REGISTER or mask.dtype != 'bool':
                        raise ValueError(f'{node.op}: trailing legacy predicate must be a bool register')
                    src.pop()
            if len(src) != len(inputs) or len(node.dst) != len(outputs):
                raise ValueError(f'{node.op}: expected {len(inputs)} sources and {len(outputs)} destinations; got {len(src)} and {len(node.dst)}')
            if len(node.dst) != len(set(node.dst)):
                raise ValueError(f'{node.op}: multi-result destinations must be distinct')
            if len(node.dst) > 1:
                register_types = {values[n].dtype for n in src + node.dst
                                  if values[n].storage is StorageKind.REGISTER}
                same_width_reinterpretation = (
                    opcode in {'VINTLV','VDINTLV'}
                    and attributes.get('bit_reinterpretation') == 'b32'
                    and register_types <= {'fp32','int32','uint32'}
                )
                if len(register_types) > 1 and not same_width_reinterpretation:
                    raise ValueError(f'{node.op}: multi-result register operands must have matching dtypes')
        elif predicate_model and any(values[n].storage is StorageKind.PREDICATE_REGISTER for n in src + node.dst):
            raise ValueError(f'{node.op}: predicate semantics require a Catalog signature')
        form = normalize_form(node.form)
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
        # ``vfsim_fallback`` is emitted by a source-faithful lowerer for an
        # instruction whose ISA form is not present in the catalog/timing
        # database.  Keep the original opcode and inferred form intact so
        # ParamDB can issue its documented unsupported-form/op fallback.
        # Validating here would reject e.g. ``VAND.s16`` before ParamDB has a
        # chance to create that fallback event.
        is_fallback = bool((node.config or {}).get("vfsim_fallback"))
        if form is not None and not is_fallback:
            opcode, form = DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(opcode, form)
        if spec is not None and form not in spec.forms and not spec.virtual and not is_fallback:
            raise ValueError(f'{node.op}: unsupported form {form!r} for selected Catalog signature')
        supplied = {access.value_id: access for access in node.memory_accesses}
        if len(supplied) != len(node.memory_accesses):
            raise ValueError(f'{node.op}: duplicate memory access metadata')
        accesses = []
        for names, kind in ((src, 'read'), (node.dst, 'write')):
            for name in names:
                if values[name].storage is not StorageKind.UB:
                    continue
                access = supplied.pop(name, None)
                span = (spec.memory_span_by_form.get(form) or spec.memory_span) if spec else None
                if access is None:
                    access = AdapterMemoryAccess(name, kind, offset=_element_offset(values[name]), span=span)
                elif access.access_kind != kind:
                    raise ValueError(f'{node.op}: memory access direction disagrees with src/dst')
                elif span is not None and access.span is None:
                    access = replace(access, span=span)
                accesses.append(access)
        if supplied:
            raise ValueError(f'{node.op}: memory access metadata must reference UB operands of this instruction')
        if opcode in {'VGATHER2', 'VSCATTER'}:
            if values[src[1]].dtype != 'uint16':
                raise ValueError(f'{opcode}.fp16 requires uint16 element indices')
            from core.indexed_memory import indexed_memory_timing
            indexed_memory_timing(opcode, form, attributes)
        if opcode == 'SPRCLR' and (type(attributes.get('spr')) is not int or attributes['spr'] != 74):
            raise ValueError('Only measured SPRCLR spr=74 is supported')
        if opcode == 'VSQZ' and attributes.get('mode', 'MODE_STORED') != 'MODE_STORED':
            raise ValueError('Only measured VSQZ MODE_STORED is supported')
        if opcode == 'VSTUR':
            # MODE_STORED makes the byte count data-dependent. The frontend
            # cannot replace it with a full-vector store or invent a pointer
            # increment. A source lowerer with resolved masks supplies both.
            if (len(accesses) != 1 or accesses[0].span is None
                    or accesses[0].update_mode != 'post_update'
                    or type(accesses[0].post_update_delta_bytes) is not int
                    or accesses[0].post_update_delta_bytes != accesses[0].span * 4):
                raise ValueError('VSTUR requires explicit selected span and POST_UPDATE byte count')
        scalar_addresses = attributes.pop('scalar_address_inputs', [])
        if not isinstance(scalar_addresses, (tuple, list)) or any(
            not isinstance(name, str) or name not in values or values[name].storage is not StorageKind.SCALAR
            for name in scalar_addresses
        ):
            raise ValueError('scalar_address_inputs requires declared Scalar values')
        if scalar_addresses and (spec is None or spec.instruction_class.value not in ('load','store')):
            raise ValueError('Scalar address inputs are supported only for memory instructions')
        for index, name in enumerate(scalar_addresses):
            attributes[f'scalar_address_input_{index}'] = name
        return AdapterInstruction(name=opcode, src=src, dst=list(node.dst),
                                  form=form, memory_accesses=tuple(accesses),
                                  attributes=attributes, supplemental_inputs=tuple(scalar_addresses))

    adapter = AdapterProgram(
        context=[convert(node) for node in program.body],
        values={name: AdapterValue(name, v.storage.value, v.dtype, v.shape) for name, v in values.items()},
        params=dict(program.params or {}), default_dtype=program.dtype,
        uarch=dict(program.uarch or {}),
        # Old program descriptions assumed a register entry for loop outputs.
        # Retain that v1 behavior only for the timing-only compatibility path.
        live_in_values=(() if predicate_model else tuple(
            name for name, v in values.items() if v.storage is StorageKind.REGISTER and v.dtype != 'bool'
        )),
    )
    return _ProgramValueVersioning(values).run(adapter, source={'adapter': 'tilesim_program', **(program.config or {})})
