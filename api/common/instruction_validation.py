"""Catalog-specific Canonical instruction checks, independent of graph traversal."""
from typing import Callable

from .instruction_catalog import ArgumentKind, DEFAULT_INSTRUCTION_CATALOG, InstructionSpec
from .schema import CanonicalInstruction, CanonicalVfInfo, OperandRole, StorageKind


def validate_instruction_form(
    vf_info: CanonicalVfInfo, node: CanonicalInstruction, node_path: str, error: Callable,
) -> InstructionSpec | None:
    catalog_spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.opcode)
    if "catalog_mode" in node.attributes:
        mode = node.attributes["catalog_mode"]
        mode_spec = catalog_spec.memory_modes.get(mode) if catalog_spec and isinstance(mode, str) else None
        if mode_spec is None:
            error("unsupported_catalog_mode", "Unknown Catalog memory mode", path=node_path)
        else:
            catalog_spec = mode_spec
            if any(o.memory_access is not None and o.memory_access.span != mode_spec.memory_span for o in (*node.inputs, *node.outputs)):
                error("catalog_memory_span_mismatch", "Memory span conflicts with Catalog mode", path=node_path)
    has_predicate = any(
        vf_info.values.get(operand.value_id) is not None
        and vf_info.values[operand.value_id].storage == StorageKind.PREDICATE_REGISTER
        for operand in (*node.inputs, *node.outputs)
    )
    if catalog_spec is None and has_predicate:
        error("unsupported_predicate_semantics", "Predicate instructions require a complete Catalog signature", path=node_path)
    if "MODE_MERGING" in node.attributes.values():
        error("unsupported_merging_mode", "MODE_MERGING requires an explicit old destination input", path=node_path)
    if catalog_spec is not None:
        if node.opcode != catalog_spec.opcode:
            error(
                "noncanonical_opcode",
                "Known opcode must use its canonical Catalog name",
                path=node_path,
            )
        if node.instruction_class != catalog_spec.instruction_class:
            error(
                "catalog_instruction_class_mismatch",
                "Instruction class conflicts with Catalog semantics",
                path=node_path,
            )
        try:
            resolved_opcode, _ = (
                DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(
                    node.opcode, node.form
                )
            )
            if resolved_opcode != node.opcode:
                error(
                    "catalog_specialization_required",
                    "Virtual opcode/form must use its specialized opcode",
                    path=node_path,
                )
        except ValueError:
            if not catalog_spec.virtual:
                error(
                    "catalog_instruction_form_mismatch",
                    "Instruction form conflicts with Catalog semantics",
                    path=node_path,
                )
        if catalog_spec.align_state_operation is not None:
            if (
                node.attributes.get("align_state_operation")
                != catalog_spec.align_state_operation
                or not isinstance(
                    node.attributes.get("align_state_id"), str
                )
                or not node.attributes.get("align_state_id")
            ):
                error(
                    "catalog_align_state_mismatch",
                    "Instruction must declare its Catalog align-state operation and state ID",
                    path=node_path,
                )
        implicit_delta = catalog_spec.implicit_post_update_bytes.get(node.form)
        expected_span = catalog_spec.memory_span_by_form.get(node.form)
        for operand in (*node.inputs, *node.outputs):
            memory = operand.memory_access
            if memory is None:
                continue
            if implicit_delta is not None and (
                memory.update_mode != "post_update" or not memory.address_state_id
                or memory.post_update_delta_bytes is None
                or memory.post_update_delta_bytes.constant != implicit_delta
                or memory.post_update_delta_bytes.terms
            ):
                error("catalog_implicit_update_mismatch", "Memory access must declare its Catalog implicit pointer update", path=node_path)
            if expected_span is not None and memory.span != expected_span:
                error("catalog_memory_span_mismatch", "Memory span conflicts with Catalog form", path=node_path)
            if memory.address_unit_bytes != catalog_spec.memory_address_unit_bytes:
                error("catalog_memory_address_unit_mismatch", "Address unit conflicts with Catalog", path=node_path)
    return catalog_spec


def validate_instruction_operands(
    vf_info: CanonicalVfInfo, node: CanonicalInstruction,
    catalog_spec: InstructionSpec | None, node_path: str, error: Callable,
) -> None:
    if catalog_spec is not None:
        expected_inputs = [
            operand
            for operand in catalog_spec.input_operands
            if (vf_info.schema_version == 2 or operand.kind != ArgumentKind.PREDICATE)
        ]
        expected_outputs = [
            operand
            for operand in catalog_spec.output_operands
        ]
        actual_inputs = [
            operand
            for operand in node.inputs
            if operand.role
            != OperandRole.CONFIG
        ]
        actual_outputs = [
            operand
            for operand in node.outputs
            if operand.role
            not in (OperandRole.PREDICATE, OperandRole.CONFIG)
        ]

        def validate_catalog_operands(actual, expected, direction):
            if len(actual) != len(expected):
                error(
                    "catalog_operand_count_mismatch",
                    "Operand count conflicts with Catalog signature",
                    path=node_path,
                    direction=direction,
                    expected=len(expected),
                    actual=len(actual),
                )
                return
            for operand_index, (operand, operand_spec) in enumerate(
                zip(actual, expected)
            ):
                operand_path = (
                    f"{node_path}.{direction}[{operand_index}]"
                )
                if operand.role != operand_spec.role:
                    error(
                        "catalog_operand_role_mismatch",
                        "Operand role conflicts with Catalog signature",
                        path=operand_path,
                    )
                value = vf_info.values.get(operand.value_id)
                if value is None:
                    continue
                if operand_spec.allowed_dtypes and value.dtype not in operand_spec.allowed_dtypes:
                    error("catalog_operand_dtype_mismatch", "Operand dtype conflicts with Catalog signature", path=operand_path)
                if (catalog_spec.memory_span is not None
                    and value.storage in (StorageKind.REGISTER, StorageKind.UB)
                    and value.dtype not in catalog_spec.forms):
                    error("catalog_operand_dtype_mismatch", "Operand dtype conflicts with Catalog memory mode", path=operand_path)
                allowed_storage = {
                    ArgumentKind.REGISTER: {StorageKind.REGISTER},
                    ArgumentKind.PREDICATE: {StorageKind.PREDICATE_REGISTER},
                    ArgumentKind.UB: {StorageKind.UB},
                    ArgumentKind.SCALAR: {StorageKind.SCALAR},
                    ArgumentKind.REGISTER_OR_SCALAR: {
                        StorageKind.REGISTER,
                        StorageKind.SCALAR,
                    },
                }.get(operand_spec.kind)
                if (
                    allowed_storage is not None
                    and value.storage not in allowed_storage
                ):
                    error(
                        "catalog_operand_storage_mismatch",
                        "Operand storage conflicts with Catalog signature",
                        path=operand_path,
                    )

        validate_catalog_operands(actual_inputs, expected_inputs, "inputs")
        validate_catalog_operands(
            actual_outputs, expected_outputs, "outputs"
        )
