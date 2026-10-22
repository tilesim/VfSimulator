"""Normalize declared CCE values and instruction spellings before versioning."""

from vfsimulator.api.common.symbols import (
    compact_dtype,
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
)
from .ir import (
    AdapterAlias,
    AdapterInstruction,
    AdapterLoop,
    AdapterMembar,
    AdapterNode,
    AdapterProgram,
    AdapterValue,
    AdapterValueRef,
)


def _merge_value(existing: AdapterValue | None, incoming: AdapterValue) -> AdapterValue:
    if existing is None:
        return incoming
    if existing.storage != incoming.storage:
        raise ValueError(
            f"Conflicting storage for value {incoming.value_id}: "
            f"{existing.storage} vs {incoming.storage}"
        )
    if existing.dtype and incoming.dtype and existing.dtype != incoming.dtype:
        raise ValueError(
            f"Conflicting dtype for value {incoming.value_id}: "
            f"{existing.dtype} vs {incoming.dtype}"
        )
    if existing.shape and incoming.shape and existing.shape != incoming.shape:
        raise ValueError(
            f"Conflicting shape for value {incoming.value_id}: "
            f"{existing.shape} vs {incoming.shape}"
        )
    return AdapterValue(
        existing.value_id,
        existing.storage,
        existing.dtype or incoming.dtype,
        existing.shape or incoming.shape,
    )


def _infer_form(
    inst: AdapterInstruction,
    values: dict[str, AdapterValue],
    default_dtype: str,
) -> str:
    if inst.form:
        return str(normalize_form(inst.form))
    src_dtypes = [
        values[str(item)].dtype for item in inst.src if values[str(item)].dtype
    ]
    dst_dtypes = [
        values[str(item)].dtype for item in inst.dst if values[str(item)].dtype
    ]
    if src_dtypes and dst_dtypes and src_dtypes[0] != dst_dtypes[0]:
        return f"{compact_dtype(src_dtypes[0])}_to_{compact_dtype(dst_dtypes[0])}"
    return str(normalize_dtype((dst_dtypes or src_dtypes or [default_dtype])[0]))


def normalize_adapter_program(program: AdapterProgram) -> AdapterProgram:
    """Normalize parser IR before canonical definition construction."""

    values = dict(program.values)

    def register(ref: AdapterValueRef) -> str:
        if isinstance(ref, AdapterValue):
            values[ref.value_id] = _merge_value(values.get(ref.value_id), ref)
            return ref.value_id
        value_id = str(ref)
        if value_id not in values:
            raise ValueError(
                f"Adapter operand {value_id!r} has no declared storage and dtype"
            )
        return value_id

    def visit(nodes: list[AdapterNode]) -> list[AdapterNode]:
        normalized: list[AdapterNode] = []
        for node in nodes:
            if isinstance(node, AdapterInstruction):
                src = [register(item) for item in node.src]
                dst = [register(item) for item in node.dst]
                current = AdapterInstruction(
                    name=normalize_opcode(node.name),
                    src=src,
                    dst=dst,
                    form=normalize_form(node.form) if node.form else None,
                    instruction_class=node.instruction_class,
                    memory_accesses=tuple(node.memory_accesses),
                    source_location=node.source_location,
                    attributes=dict(node.attributes),
                )
                current.form = _infer_form(current, values, program.default_dtype)
                normalized.append(current)
            elif isinstance(node, AdapterLoop):
                normalized.append(
                    AdapterLoop(
                        node.count,
                        node.unroll,
                        visit(node.body),
                        node.loop_id,
                        node.induction_variable,
                        node.induction_start,
                        node.induction_step,
                        node.source_location,
                    )
                )
            elif isinstance(node, AdapterAlias):
                normalized.append(
                    AdapterAlias(
                        register(node.destination),
                        register(node.source),
                        node.source_location,
                        node.allow_bitcast,
                    )
                )
            elif isinstance(node, AdapterMembar):
                normalized.append(
                    AdapterMembar(
                        normalize_membar_type(node.type), node.source_location
                    )
                )
            else:
                raise TypeError(f"Unsupported adapter node: {type(node).__name__}")
        return normalized

    context = visit(program.context)
    for value_id, value in tuple(values.items()):
        values[value_id] = AdapterValue(
            value.value_id,
            value.storage,
            value.dtype or program.default_dtype,
            value.shape,
        )
    return AdapterProgram(
        context=context,
        values=values,
        params=dict(program.params),
        default_dtype=str(normalize_dtype(program.default_dtype, default="fp32")),
        uarch=dict(program.uarch),
        live_in_values=tuple(program.live_in_values),
    )
