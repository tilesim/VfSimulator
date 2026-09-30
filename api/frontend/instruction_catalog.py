from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .schema import InstructionClass, OperandRole, StorageKind


class FormRule(str, Enum):
    OPERAND_DTYPE = "operand_dtype"
    CONVERSION = "conversion"
    FIXED = "fixed"


class OperandDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"
    READ_WRITE = "read_write"
    IGNORE = "ignore"


class ArgumentKind(str, Enum):
    REGISTER = "register"
    UB = "ub"
    SCALAR = "scalar"
    PREDICATE = "predicate"
    CONFIG = "config"
    REGISTER_OR_SCALAR = "register_or_scalar"
    ALIGN_STATE = "align_state"


class PostUpdateDeltaEncoding(str, Enum):
    ELEMENT_COUNT = "element_count"
    UNSIGNED_BIT_FIELD = "unsigned_bit_field"


@dataclass(frozen=True)
class PostUpdateDeltaSpec:
    encoding: PostUpdateDeltaEncoding
    bit_offset: int = 0
    bit_width: int = 0
    unit_bytes: int = 0


@dataclass(frozen=True)
class OperandSpec:
    name: str
    argument_index: int
    direction: OperandDirection
    role: OperandRole
    kind: ArgumentKind
    optional: bool = False
    allowed_values: tuple[str, ...] = ()
    allowed_dtypes: tuple[str, ...] = ()
    allow_integer_expression: bool = False
    post_update_delta: PostUpdateDeltaSpec | None = None
    allowed_values_by_form: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: MappingProxyType({}))

    @property
    def storage(self) -> StorageKind | None:
        return {
            ArgumentKind.REGISTER: StorageKind.REGISTER,
            ArgumentKind.PREDICATE: StorageKind.PREDICATE_REGISTER,
            ArgumentKind.UB: StorageKind.UB,
            ArgumentKind.SCALAR: StorageKind.SCALAR,
        }.get(self.kind)


@dataclass(frozen=True)
class CallVariant:
    argument_count: int
    argument_values: Mapping[int, frozenset[str]] = field(
        default_factory=lambda: MappingProxyType({})
    )


@dataclass(frozen=True)
class InstructionSpec:
    opcode: str
    instruction_class: InstructionClass
    aliases: tuple[str, ...] = ()
    forms: frozenset[str] = frozenset()
    signature: str = ""
    operands: tuple[OperandSpec, ...] = ()
    form_rule: FormRule = FormRule.OPERAND_DTYPE
    fixed_form: str | None = None
    specializations: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    virtual: bool = False
    timing_optional: bool = False
    call_variants: tuple[CallVariant, ...] = ()
    align_state_operation: str | None = None
    align_state_argument_index: int | None = None
    memory_modes: Mapping[str, InstructionSpec] = field(default_factory=lambda: MappingProxyType({}))
    memory_span: int | None = None
    ub_transfer_bytes: int = 0
    forwarding_opcode: str | None = None
    implicit_post_update_bytes: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    memory_span_by_form: Mapping[str, int] = field(default_factory=lambda: MappingProxyType({}))
    memory_address_unit_bytes: int | None = None

    @property
    def input_operands(self) -> tuple[OperandSpec, ...]:
        return tuple(
            replace(op, direction=OperandDirection.INPUT, role=OperandRole.SOURCE)
            if op.direction == OperandDirection.READ_WRITE else op
            for op in self.operands
            if op.direction in (OperandDirection.INPUT, OperandDirection.READ_WRITE)
        )

    @property
    def output_operands(self) -> tuple[OperandSpec, ...]:
        return tuple(
            replace(op, direction=OperandDirection.OUTPUT)
            if op.direction == OperandDirection.READ_WRITE else op
            for op in self.operands
            if op.direction in (OperandDirection.OUTPUT, OperandDirection.READ_WRITE)
        )


@dataclass(frozen=True)
class CatalogTimingDifference:
    catalog_without_timing: frozenset[str]
    timing_without_catalog: frozenset[str]
    semantic_forms_without_timing: Mapping[str, frozenset[str]]
    timing_forms_without_semantics: Mapping[str, frozenset[str]]
    instruction_class_mismatches: Mapping[str, tuple[str, str]]

    @property
    def is_empty(self) -> bool:
        return not (
            self.catalog_without_timing
            or self.timing_without_catalog
            or self.semantic_forms_without_timing
            or self.timing_forms_without_semantics
            or self.instruction_class_mismatches
        )

    @property
    def has_semantic_conflicts(self) -> bool:
        return bool(
            self.timing_without_catalog
            or self.timing_forms_without_semantics
            or self.instruction_class_mismatches
        )


class InstructionCatalog:
    def __init__(self, specs: Iterable[InstructionSpec]) -> None:
        by_opcode: dict[str, InstructionSpec] = {}
        aliases: dict[str, str] = {}
        for spec in specs:
            self._validate_spec(spec)
            for mode, variant in spec.memory_modes.items():
                self._validate_spec(variant)
                mode_operands = [o for o in variant.operands if o.name == "mode"]
                if (variant.opcode != spec.opcode or type(variant.memory_span) is not int
                    or variant.memory_span <= 0
                    or variant.instruction_class not in (InstructionClass.LOAD, InstructionClass.STORE)
                    or variant.instruction_class != spec.instruction_class
                    or not variant.forms.issubset(spec.forms)
                    or len(mode_operands) != 1
                    or mode_operands[0].kind != ArgumentKind.CONFIG
                    or mode_operands[0].allowed_values != (mode,)):
                    raise ValueError(f"Invalid memory mode {spec.opcode}.{mode}")
            opcode = spec.opcode
            if opcode in by_opcode:
                raise ValueError(f"Duplicate canonical opcode: {opcode}")
            by_opcode[opcode] = spec
            for alias in (opcode, *spec.aliases):
                key = alias.strip().lower()
                previous = aliases.get(key)
                if not key or (previous is not None and previous != opcode):
                    raise ValueError(f"Conflicting opcode alias: {alias}")
                aliases[key] = opcode

        for spec in by_opcode.values():
            if spec.forwarding_opcode is not None:
                if not isinstance(spec.forwarding_opcode, str):
                    raise ValueError(f"Invalid forwarding_opcode for {spec.opcode}")
                target = by_opcode.get(spec.forwarding_opcode)
                if target is None or target.forwarding_opcode is not None or target.instruction_class != spec.instruction_class:
                    raise ValueError(f"Invalid forwarding_opcode for {spec.opcode}")
            for form, target in spec.specializations.items():
                if not form or target not in by_opcode:
                    raise ValueError(
                        f"Invalid specialization {spec.opcode}.{form} -> {target}"
                    )

        self._by_opcode = MappingProxyType(by_opcode)
        self._aliases = MappingProxyType(aliases)

    @staticmethod
    def _validate_spec(spec: InstructionSpec) -> None:
        if spec.memory_address_unit_bytes is not None and (
            type(spec.memory_address_unit_bytes) is not int
            or spec.memory_address_unit_bytes not in (1, 2, 4, 8)
        ):
            raise ValueError(f"Invalid memory_address_unit_bytes for {spec.opcode}")
        for name in ("implicit_post_update_bytes", "memory_span_by_form"):
            values = getattr(spec, name)
            if not isinstance(values, Mapping) or any(
                form not in spec.forms or type(n) is not int or not 0 < n <= 2**63 - 1
                for form, n in values.items()
            ):
                raise ValueError(f"Invalid {name} for {spec.opcode}")
        if type(spec.ub_transfer_bytes) is not int or not 0 <= spec.ub_transfer_bytes <= 2**63 - 1:
            raise ValueError(f"Invalid ub_transfer_bytes for {spec.opcode}")
        if spec.instruction_class in (InstructionClass.LOAD, InstructionClass.STORE) and spec.ub_transfer_bytes == 0:
            raise ValueError(f"Missing ub_transfer_bytes for {spec.opcode}")
        if not spec.opcode or spec.opcode != spec.opcode.upper():
            raise ValueError(f"Canonical opcode must be non-empty uppercase: {spec.opcode}")
        if not isinstance(spec.instruction_class, InstructionClass):
            raise ValueError(f"Invalid instruction class for {spec.opcode}")
        if not isinstance(spec.form_rule, FormRule):
            raise ValueError(f"Invalid form rule for {spec.opcode}")
        if spec.form_rule == FormRule.FIXED:
            if not spec.fixed_form or spec.fixed_form not in spec.forms:
                raise ValueError(
                    f"Fixed-form instruction {spec.opcode} must declare fixed_form in forms"
                )
        elif spec.fixed_form is not None:
            raise ValueError(f"Non-fixed instruction {spec.opcode} cannot set fixed_form")

        if spec.align_state_operation not in (None, "append", "consume", "load_init", "load_use"):
            raise ValueError(f"Invalid align state operation in {spec.opcode}")
        if spec.align_state_operation in ("load_init", "load_use") and spec.instruction_class != InstructionClass.LOAD:
            raise ValueError(f"Load align operation requires LOAD class: {spec.opcode}")
        if (spec.align_state_operation is None) != (
            spec.align_state_argument_index is None
        ):
            raise ValueError(f"Incomplete align state declaration in {spec.opcode}")

        indexes: set[int] = set()
        for operand in spec.operands:
            if not isinstance(operand.allowed_dtypes, tuple) or any(
                not isinstance(dtype, str) or not dtype for dtype in operand.allowed_dtypes
            ):
                raise ValueError(f"Invalid allowed_dtypes in {spec.opcode}")
            if not isinstance(operand.allowed_values_by_form, Mapping) or any(
                form not in spec.forms or not isinstance(values, tuple) or not values
                or any(not isinstance(v, str) or v not in operand.allowed_values for v in values)
                for form, values in operand.allowed_values_by_form.items()
            ):
                raise ValueError(f"Invalid form-specific allowed values in {spec.opcode}")
            if (
                isinstance(operand.argument_index, bool)
                or not isinstance(operand.argument_index, int)
                or operand.argument_index < 0
                or operand.argument_index in indexes
            ):
                raise ValueError(f"Invalid argument index in {spec.opcode}")
            indexes.add(operand.argument_index)
            if not operand.name or not isinstance(operand.direction, OperandDirection):
                raise ValueError(f"Invalid operand declaration in {spec.opcode}")
            if not isinstance(operand.role, OperandRole) or not isinstance(
                operand.kind, ArgumentKind
            ):
                raise ValueError(f"Invalid operand enum in {spec.opcode}")
            if not all(
                isinstance(value, str) and value for value in operand.allowed_values
            ):
                raise ValueError(f"Invalid allowed_values in {spec.opcode}")
            if not isinstance(operand.allow_integer_expression, bool):
                raise ValueError(
                    f"Invalid allow_integer_expression in {spec.opcode}"
                )
            if operand.allow_integer_expression and operand.kind != ArgumentKind.CONFIG:
                raise ValueError(
                    f"Only config operands may allow integer expressions in {spec.opcode}"
                )
            delta = operand.post_update_delta
            if delta is not None:
                if operand.kind != ArgumentKind.CONFIG or not operand.allow_integer_expression:
                    raise ValueError(
                        f"POST_UPDATE delta operand must be an integer config in {spec.opcode}"
                    )
                if delta.encoding == PostUpdateDeltaEncoding.ELEMENT_COUNT:
                    if delta.bit_offset or delta.bit_width or delta.unit_bytes:
                        raise ValueError(
                            f"Element-count POST_UPDATE delta has invalid fields in {spec.opcode}"
                        )
                elif delta.encoding == PostUpdateDeltaEncoding.UNSIGNED_BIT_FIELD:
                    if (
                        isinstance(delta.bit_offset, bool)
                        or not isinstance(delta.bit_offset, int)
                        or delta.bit_offset < 0
                        or isinstance(delta.bit_width, bool)
                        or not isinstance(delta.bit_width, int)
                        or delta.bit_width <= 0
                        or delta.bit_offset + delta.bit_width > 64
                        or isinstance(delta.unit_bytes, bool)
                        or not isinstance(delta.unit_bytes, int)
                        or delta.unit_bytes <= 0
                    ):
                        raise ValueError(
                            f"Invalid bit-field POST_UPDATE delta in {spec.opcode}"
                        )
            if operand.direction == OperandDirection.OUTPUT and operand.role not in {
                OperandRole.DESTINATION,
                OperandRole.MEMORY,
            }:
                raise ValueError(f"Output role mismatch in {spec.opcode}")
            if operand.direction == OperandDirection.READ_WRITE and (
                operand.kind != ArgumentKind.REGISTER
                or operand.role != OperandRole.DESTINATION
            ):
                raise ValueError(f"Read-write operand must be a vector destination in {spec.opcode}")
            if operand.direction == OperandDirection.INPUT and operand.role not in {
                OperandRole.SOURCE,
                OperandRole.SCALAR,
                OperandRole.MEMORY,
                OperandRole.PREDICATE,
            }:
                raise ValueError(f"Input role mismatch in {spec.opcode}")
            if operand.direction == OperandDirection.IGNORE and operand.role not in {
                OperandRole.PREDICATE,
                OperandRole.CONFIG,
            }:
                raise ValueError(f"Ignored operand role mismatch in {spec.opcode}")
        if indexes and indexes != set(range(max(indexes) + 1)):
            raise ValueError(f"Argument indexes must be contiguous in {spec.opcode}")
        if sum(operand.post_update_delta is not None for operand in spec.operands) > 1:
            raise ValueError(f"Multiple POST_UPDATE delta operands in {spec.opcode}")
        if spec.align_state_argument_index is not None:
            state_operands = [
                operand for operand in spec.operands
                if operand.argument_index == spec.align_state_argument_index
                and operand.kind == ArgumentKind.ALIGN_STATE
            ]
            if len(state_operands) != 1:
                raise ValueError(f"Invalid align state operand in {spec.opcode}")

        required_count = max(
            (
                operand.argument_index + 1
                for operand in spec.operands
                if not operand.optional
            ),
            default=0,
        )
        maximum_count = max(indexes, default=-1) + 1
        operands_by_index = {
            operand.argument_index: operand for operand in spec.operands
        }
        for variant in spec.call_variants:
            if (
                isinstance(variant.argument_count, bool)
                or not isinstance(variant.argument_count, int)
                or not required_count <= variant.argument_count <= maximum_count
            ):
                raise ValueError(f"Invalid call variant count in {spec.opcode}")
            for argument_index, values in variant.argument_values.items():
                operand = operands_by_index.get(argument_index)
                if (
                    isinstance(argument_index, bool)
                    or not isinstance(argument_index, int)
                    or argument_index < 0
                    or argument_index >= variant.argument_count
                    or operand is None
                    or operand.kind != ArgumentKind.CONFIG
                    or not values
                    or not all(isinstance(value, str) and value for value in values)
                ):
                    raise ValueError(f"Invalid call variant values in {spec.opcode}")
                if operand.allowed_values and not values <= set(operand.allowed_values):
                    raise ValueError(
                        f"Call variant exceeds allowed values in {spec.opcode}"
                    )

        tracked = [
            operand
            for operand in spec.operands
            if operand.direction != OperandDirection.IGNORE
        ]
        memory_inputs = [
            operand for operand in tracked
            if operand.kind == ArgumentKind.UB
            and operand.direction == OperandDirection.INPUT
        ]
        memory_outputs = [
            operand for operand in tracked
            if operand.kind == ArgumentKind.UB
            and operand.direction == OperandDirection.OUTPUT
        ]
        register_outputs = [
            operand for operand in tracked
            if operand.kind in (ArgumentKind.REGISTER, ArgumentKind.PREDICATE)
            and operand.direction in (OperandDirection.OUTPUT, OperandDirection.READ_WRITE)
        ]
        if spec.instruction_class == InstructionClass.LOAD:
            if len(memory_inputs) != 1 or memory_outputs or (not register_outputs and spec.align_state_operation != "load_init"):
                raise ValueError(f"Invalid load signature for {spec.opcode}")
        elif spec.instruction_class == InstructionClass.STORE:
            if memory_inputs or len(memory_outputs) != 1 or register_outputs:
                raise ValueError(f"Invalid store signature for {spec.opcode}")
        elif spec.instruction_class == InstructionClass.COMPUTE:
            if memory_inputs or memory_outputs or not register_outputs:
                raise ValueError(f"Invalid compute signature for {spec.opcode}")

    @property
    def specs(self) -> Mapping[str, InstructionSpec]:
        return self._by_opcode

    @property
    def aliases(self) -> Mapping[str, str]:
        return self._aliases

    def lookup(self, value: Any) -> InstructionSpec | None:
        return self._by_opcode.get(self.canonical_opcode(value))

    def canonical_opcode(self, value: Any) -> str:
        text = str(getattr(value, "value", value)).strip()
        if not text:
            return ""
        return self._aliases.get(text.lower(), text.upper())

    def specialize(self, opcode: Any, form: str | None) -> str:
        canonical = self.canonical_opcode(opcode)
        spec = self._by_opcode.get(canonical)
        if spec is None or form is None:
            return canonical
        return spec.specializations.get(form, canonical)

    def resolve_and_validate_form(
        self,
        opcode: Any,
        form: str | None,
    ) -> tuple[str, str]:
        canonical = self.canonical_opcode(opcode)
        spec = self._by_opcode.get(canonical)
        if spec is None:
            if not form:
                raise ValueError(f"Unknown opcode {canonical} requires an explicit form")
            return canonical, form
        resolved_form = form
        if not resolved_form:
            raise ValueError(f"Instruction {canonical} requires a form")
        allowed_forms = set(spec.forms) | set(spec.specializations)
        if resolved_form not in allowed_forms:
            raise ValueError(
                f"Unsupported semantic form {canonical}.{resolved_form}; "
                f"expected one of {sorted(allowed_forms)}"
            )
        return spec.specializations.get(resolved_form, canonical), resolved_form

    def compare_timing_config(self, payload: Mapping[str, Any]) -> CatalogTimingDifference:
        instructions = payload.get("instructions", {})
        if not isinstance(instructions, Mapping):
            raise ValueError("ISA timing config must contain an instructions mapping")
        normalized = {str(op).upper(): value for op, value in instructions.items()}
        timing_ops = set(normalized)
        required_ops = {
            op for op, spec in self._by_opcode.items()
            if not spec.virtual and not spec.timing_optional
        }
        missing_forms: dict[str, frozenset[str]] = {}
        extra_forms: dict[str, frozenset[str]] = {}
        class_mismatches: dict[str, tuple[str, str]] = {}
        for opcode in set(self._by_opcode) & timing_ops:
            timing_spec = normalized[opcode]
            if not isinstance(timing_spec, Mapping):
                raise ValueError(f"ISA timing entry {opcode} must be a mapping")
            semantic_spec = self._by_opcode[opcode]
            timing_forms = set((timing_spec.get("forms") or {}).keys())
            missing = semantic_spec.forms - timing_forms
            extra = timing_forms - semantic_spec.forms
            if missing:
                missing_forms[opcode] = frozenset(missing)
            if extra:
                extra_forms[opcode] = frozenset(extra)
            timing_class = str(timing_spec.get("op_class", "")).lower()
            if timing_class and timing_class != semantic_spec.instruction_class.value:
                class_mismatches[opcode] = (
                    semantic_spec.instruction_class.value,
                    timing_class,
                )
        return CatalogTimingDifference(
            catalog_without_timing=frozenset(required_ops - timing_ops),
            timing_without_catalog=frozenset(timing_ops - set(self._by_opcode)),
            semantic_forms_without_timing=MappingProxyType(missing_forms),
            timing_forms_without_semantics=MappingProxyType(extra_forms),
            instruction_class_mismatches=MappingProxyType(class_mismatches),
        )


def _enum(enum_type, value: Any, path: str):
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {path}: {value}") from exc


def instruction_catalog_from_dict(payload: Mapping[str, Any]) -> InstructionCatalog:
    if (
        isinstance(payload.get("schema_version"), bool)
        or payload.get("schema_version") != 1
    ):
        raise ValueError("Unsupported instruction catalog schema_version")
    raw_signatures = payload.get("signatures")
    raw_instructions = payload.get("instructions")
    if not isinstance(raw_signatures, Mapping) or not isinstance(
        raw_instructions, Mapping
    ):
        raise ValueError("Instruction catalog requires signatures and instructions")

    signatures: dict[str, tuple[OperandSpec, ...]] = {}
    for name, raw_operands in raw_signatures.items():
        if not isinstance(name, str) or not isinstance(raw_operands, list):
            raise ValueError("Invalid signature declaration")
        operands = []
        for raw in raw_operands:
            if not isinstance(raw, Mapping):
                raise ValueError(f"Invalid operand in signature {name}")
            operand_name = raw.get("name")
            optional = raw.get("optional", False)
            allowed_values = raw.get("allowed_values", [])
            allow_integer_expression = raw.get("allow_integer_expression", False)
            raw_post_update_delta = raw.get("post_update_delta")
            form_values = raw.get("allowed_values_by_form", {})
            if not isinstance(form_values, Mapping) or any(
                not isinstance(k, str) or not isinstance(v, list)
                for k, v in form_values.items()
            ):
                raise ValueError(f"{name}.allowed_values_by_form must map forms to arrays")
            if not isinstance(operand_name, str) or not operand_name:
                raise ValueError(f"{name}.name must be a non-empty string")
            if not isinstance(optional, bool):
                raise ValueError(f"{name}.optional must be boolean")
            if not isinstance(allowed_values, list) or not all(
                isinstance(value, str) and value for value in allowed_values
            ):
                raise ValueError(f"{name}.allowed_values must be an array of strings")
            if not isinstance(allow_integer_expression, bool):
                raise ValueError(
                    f"{name}.allow_integer_expression must be boolean"
                )
            allowed_dtypes = raw.get("allowed_dtypes", [])
            if not isinstance(allowed_dtypes, list) or any(not isinstance(d, str) or not d for d in allowed_dtypes):
                raise ValueError(f"{name}.allowed_dtypes must be an array of strings")
            post_update_delta = None
            if raw_post_update_delta is not None:
                if not isinstance(raw_post_update_delta, Mapping):
                    raise ValueError(f"{name}.post_update_delta must be an object")
                post_update_delta = PostUpdateDeltaSpec(
                    encoding=_enum(
                        PostUpdateDeltaEncoding,
                        raw_post_update_delta.get("encoding"),
                        f"{name}.post_update_delta.encoding",
                    ),
                    bit_offset=raw_post_update_delta.get("bit_offset", 0),
                    bit_width=raw_post_update_delta.get("bit_width", 0),
                    unit_bytes=raw_post_update_delta.get("unit_bytes", 0),
                )
            operands.append(OperandSpec(
                name=operand_name,
                argument_index=raw.get("argument_index"),
                direction=_enum(
                    OperandDirection, raw.get("direction"), f"{name}.direction"
                ),
                role=_enum(OperandRole, raw.get("role"), f"{name}.role"),
                kind=_enum(ArgumentKind, raw.get("kind"), f"{name}.kind"),
                optional=optional,
                allowed_values=tuple(allowed_values),
                allowed_dtypes=tuple(allowed_dtypes),
                allow_integer_expression=allow_integer_expression,
                post_update_delta=post_update_delta,
                allowed_values_by_form=MappingProxyType({k: tuple(v) for k, v in form_values.items()}),
            ))
        signatures[name] = tuple(operands)

    specs = []
    for opcode, raw in raw_instructions.items():
        if not isinstance(opcode, str) or not opcode or not isinstance(raw, Mapping):
            raise ValueError(f"Invalid instruction declaration: {opcode}")
        signature = raw.get("signature")
        if not isinstance(signature, str):
            raise ValueError(f"{opcode}.signature must be a string")
        if signature not in signatures:
            raise ValueError(f"Unknown signature {signature} for {opcode}")
        aliases = raw.get("aliases", [])
        forms = raw.get("forms", [])
        specializations = raw.get("specializations", {})
        virtual = raw.get("virtual", False)
        timing_optional = raw.get("timing_optional", False)
        raw_call_variants = raw.get("call_variants", [])
        if not isinstance(aliases, list) or not all(
            isinstance(value, str) and value for value in aliases
        ):
            raise ValueError(f"{opcode}.aliases must be an array of strings")
        if not isinstance(forms, list) or not all(
            isinstance(value, str) and value for value in forms
        ):
            raise ValueError(f"{opcode}.forms must be an array of strings")
        if not isinstance(specializations, Mapping) or not all(
            isinstance(form, str) and form and isinstance(target, str) and target
            for form, target in specializations.items()
        ):
            raise ValueError(f"{opcode}.specializations must map strings to strings")
        if not isinstance(virtual, bool) or not isinstance(timing_optional, bool):
            raise ValueError(f"{opcode}.virtual/timing_optional must be boolean")
        if not isinstance(raw_call_variants, list):
            raise ValueError(f"{opcode}.call_variants must be an array")
        call_variants = []
        for raw_variant in raw_call_variants:
            if not isinstance(raw_variant, Mapping):
                raise ValueError(f"Invalid call variant for {opcode}")
            argument_count = raw_variant.get("argument_count")
            raw_values = raw_variant.get("argument_values", {})
            if isinstance(argument_count, bool) or not isinstance(
                argument_count, int
            ):
                raise ValueError(f"{opcode}.argument_count must be an integer")
            if not isinstance(raw_values, Mapping):
                raise ValueError(f"{opcode}.argument_values must be an object")
            argument_values = {}
            for raw_index, values in raw_values.items():
                try:
                    argument_index = int(raw_index)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{opcode}.argument_values index must be an integer"
                    ) from exc
                if (
                    isinstance(raw_index, bool)
                    or str(argument_index) != str(raw_index)
                    or not isinstance(values, list)
                    or not all(isinstance(value, str) and value for value in values)
                ):
                    raise ValueError(f"Invalid argument values for {opcode}")
                argument_values[argument_index] = frozenset(values)
            call_variants.append(
                CallVariant(
                    argument_count=argument_count,
                    argument_values=MappingProxyType(argument_values),
                )
            )
        fixed_form = raw.get("fixed_form")
        if fixed_form is not None and (
            not isinstance(fixed_form, str) or not fixed_form
        ):
            raise ValueError(f"{opcode}.fixed_form must be a non-empty string")
        align_state_operation = raw.get("align_state_operation")
        align_state_argument_index = raw.get("align_state_argument_index")
        if align_state_operation is not None and not isinstance(align_state_operation, str):
            raise ValueError(f"{opcode}.align_state_operation must be a string")
        if align_state_argument_index is not None and (
            isinstance(align_state_argument_index, bool)
            or not isinstance(align_state_argument_index, int)
        ):
            raise ValueError(f"{opcode}.align_state_argument_index must be an integer")
        memory_form_maps = {}
        for name in ("implicit_post_update_bytes", "memory_span_by_form"):
            mapping = raw.get(name, {})
            if not isinstance(mapping, Mapping):
                raise ValueError(f"{opcode}.{name} must be an object")
            memory_form_maps[name] = MappingProxyType(dict(mapping))
        spec = InstructionSpec(
            opcode=opcode,
            instruction_class=_enum(
                InstructionClass, raw.get("class"), f"{opcode}.class"
            ),
            aliases=tuple(aliases),
            forms=frozenset(forms),
            signature=signature,
            operands=signatures[signature],
            form_rule=_enum(
                FormRule,
                raw.get("form_rule", FormRule.OPERAND_DTYPE.value),
                f"{opcode}.form_rule",
            ),
            fixed_form=fixed_form,
            specializations=MappingProxyType({
                str(form): str(target)
                for form, target in specializations.items()
            }),
            virtual=virtual,
            timing_optional=timing_optional,
            call_variants=tuple(call_variants),
            align_state_operation=align_state_operation,
            align_state_argument_index=align_state_argument_index,
            ub_transfer_bytes=raw.get("ub_transfer_bytes", 0),
            forwarding_opcode=raw.get("forwarding_opcode"),
            implicit_post_update_bytes=memory_form_maps["implicit_post_update_bytes"],
            memory_span_by_form=memory_form_maps["memory_span_by_form"],
            memory_address_unit_bytes=raw.get("memory_address_unit_bytes"),
        )
        modes = raw.get("memory_modes", {})
        if not isinstance(modes, Mapping):
            raise ValueError(f"{opcode}.memory_modes must be an object")
        variants = {}
        for mode, declaration in modes.items():
            if not isinstance(mode, str) or not isinstance(declaration, Mapping):
                raise ValueError(f"Invalid memory mode for {opcode}")
            mode_signature = declaration.get("signature")
            mode_forms = declaration.get("forms")
            span = declaration.get("span")
            if (not isinstance(mode_signature, str) or mode_signature not in signatures or not isinstance(mode_forms, list)
                or not mode_forms or not all(isinstance(f, str) for f in mode_forms)
                or type(span) is not int or span <= 0):
                raise ValueError(f"Invalid memory mode declaration: {opcode}.{mode}")
            variants[mode] = replace(spec, signature=mode_signature,
                operands=signatures[mode_signature], forms=frozenset(mode_forms),
                call_variants=(), memory_span=span,
                ub_transfer_bytes=declaration.get("ub_transfer_bytes", spec.ub_transfer_bytes))
        specs.append(replace(spec, memory_modes=MappingProxyType(variants)))
    return InstructionCatalog(specs)


def load_instruction_catalog(path: str | Path) -> InstructionCatalog:
    return instruction_catalog_from_dict(json.loads(Path(path).read_text()))


DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parents[2] / "configs" / "instruction_catalog.json"
)
DEFAULT_INSTRUCTION_CATALOG = load_instruction_catalog(DEFAULT_CATALOG_PATH)


__all__ = [
    "ArgumentKind",
    "CatalogTimingDifference",
    "DEFAULT_CATALOG_PATH",
    "DEFAULT_INSTRUCTION_CATALOG",
    "FormRule",
    "InstructionCatalog",
    "InstructionSpec",
    "OperandDirection",
    "OperandSpec",
    "PostUpdateDeltaEncoding",
    "PostUpdateDeltaSpec",
    "instruction_catalog_from_dict",
    "load_instruction_catalog",
]
