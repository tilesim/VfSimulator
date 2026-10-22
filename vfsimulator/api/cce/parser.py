from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence

from vfsimulator.api.cce.source import (
    CCEVFScope,
    _base_identifier,
    _cce_scalar_dtype_to_form,
    _find_matching_brace,
    _find_matching_paren,
    _is_token_boundary,
    _select_scope,
    _skip_ws,
    _split_args,
    _strip_comments,
    _strip_ub_reference_wrappers,
    _vector_dtype_to_form,
    _extract_cce_vf_scopes,
)
from vfsimulator.api.common.symbols import (
    compact_dtype,
    dtype_element_bytes,
    normalize_dtype,
    normalize_membar_type,
    normalize_opcode,
)
from vfsimulator.api.common.instruction_catalog import (
    ArgumentKind,
    DEFAULT_INSTRUCTION_CATALOG,
    FormRule,
    InstructionSpec,
    OperandDirection,
    OperandSpec,
    PostUpdateDeltaEncoding,
)
from vfsimulator.api.common.schema import SourceLocation
from vfsimulator.api.cce.normalization import normalize_adapter_program
from vfsimulator.api.cce.ir import (
    AdapterAlias,
    AdapterInstruction,
    AdapterLoop,
    AdapterMemoryAccess,
    AdapterMembar,
    AdapterNode,
    AdapterProgram,
    AdapterValue,
)


_PRAGMA_UNROLL_RE = re.compile(r"#\s*pragma\s+unroll\s*(?:\(\s*(\d+)\s*\)|(\d+)\b)")
_VECTOR_DECL_STMT_RE = re.compile(r"^\s*vector_([A-Za-z0-9_]+)\s+([^;]+)\s*$")
_LOCAL_SCALAR_DECL_RE = re.compile(
    r"^\s*(?:(?:const|constexpr|volatile|static)\s+)*"
    r"(?P<dtype>u?int(?:8|16|32|64)_t|size_t|unsigned(?:\s+(?:short|int|long))?|"
    r"signed(?:\s+(?:short|int|long))?|short|int|long|float|double|half)"
    r"\s+(?P<declarations>.+?)\s*;\s*$"
)
_LOCAL_UB_POINTER_DECL_RE = re.compile(
    r"^\s*(?:(?:const|volatile|static)\s+)*__ubuf__\s+"
    r"(?P<dtype>[A-Za-z_]\w*)\s*\*\s*(?P<name>[A-Za-z_]\w*)\s*=\s*"
    r"(?P<initializer>.+?)\s*;\s*$",
    re.DOTALL,
)
_REGISTER_ALIAS_ASSIGN_RE = re.compile(
    r"^\s*(?P<dst>[A-Za-z_]\w*)\s*=\s*(?P<src>[A-Za-z_]\w*)\s*;\s*$"
)
_VOID_USE_RE = re.compile(r"^\s*\(\s*void\s*\)\s*[A-Za-z_]\w*\s*;\s*$")
_CALL_RE = re.compile(r"([A-Za-z_]\w*)\s*\((.*)\)\s*;", re.DOTALL)


def _parse_cce_adapter_program(
    path: str | Path,
    kernel_name: str | None = None,
    loop_params: Optional[Dict[str, int]] = None,
) -> AdapterProgram:
    """
    Parse one CCE ``__VEC_SCOPE__`` kernel into private adapter IR.

    ``loop_params`` can provide concrete values for symbolic loop bounds such as
    ``repeat_times``. If omitted, the adapter tries a small whole-file inference
    pass for calls like ``int repeat_times = 96; gelu_simd_ub(..., repeat_times)``.
    """

    source_path = Path(path)
    source = _strip_comments(source_path.read_text(encoding="utf-8"))
    scope = _select_scope(_extract_cce_vf_scopes(source, source_path), kernel_name)
    resolved_loop_params = dict(loop_params or {})
    resolved_loop_params.update(_infer_call_argument_constants(source, scope))
    parser = _VFScopeParser(scope, resolved_loop_params)
    return normalize_adapter_program(
        AdapterProgram(
            context=parser.parse(),
            params=resolved_loop_params,
        )
    )


class _VFScopeParser:
    def __init__(self, scope: CCEVFScope, loop_params: Dict[str, int]) -> None:
        self.scope = scope
        self.loop_params = loop_params
        declared_vectors = _extract_vector_decls(scope.declaration_source)
        self.align_state_names = {
            name for name, dtype in declared_vectors.items() if dtype == "align"
        }
        self._align_state_serial = 0
        self.align_state_ids = {
            name: self._new_align_state_id(name) for name in self.align_state_names
        }
        self.register_dtypes = {
            name: dtype
            for name, dtype in declared_vectors.items()
            if dtype not in {"align", "address"}
        }
        self.register_names = set(self.register_dtypes)
        self.address_expressions = {}
        self.address_state_ids = {}
        self._address_bindings = {}
        self._loop_inductions = []
        self._predicate_serial = 0
        self._vector_serial = 0
        self._instruction_prefix: list[AdapterInstruction] = []
        self.ub_names = {
            name for name, storage in scope.param_storage.items() if storage == "UB"
        }
        self.ub_aliases: Dict[str, tuple[str, str]] = {
            name: (name, "0") for name in self.ub_names
        }
        self._pointer_serial = 0
        self.pointer_states = {
            name: self._new_pointer_state(name) for name in sorted(self.ub_names)
        }
        self.updated_pointer_states: set[str] = set()
        self.all_updated_pointer_states: set[str] = set()
        self.loop_pointer_states: set[str] = set()
        self.loop_pointer_sources: dict[str, str] = {}
        self._block_induction_variables = frozenset()
        self.ub_dtypes: Dict[str, str] = {
            name: dtype
            for name, dtype in scope.param_dtypes.items()
            if name in self.ub_names
        }
        self.scalar_names = {
            name for name, storage in scope.param_storage.items() if storage == "Scalar"
        }
        self.offset_scalar_names = set(self.scalar_names)
        self.local_scalar_dtypes: Dict[str, str] = {
            name: dtype
            for name, dtype in scope.param_dtypes.items()
            if name in self.scalar_names
        }
        self.local_scalar_initializers: Dict[str, str] = {}
        self._record_function_scope_scalar_declarations(scope.declaration_source)
        self._record_function_scope_ub_pointer_declarations(scope.declaration_source)
        self.pair_registers = {}
        self._software_scalar_serial = 0
        for name, dtype in sorted(self.register_dtypes.items()):
            if dtype in {"2xvl_s64", "2xvl_u64"}:
                self._declare_pair(name, dtype)

    def parse(self) -> List[AdapterNode]:
        nodes = self._parse_block(
            self.scope.source,
            frozenset(),
            self.scope.start_line,
        )
        for state in self.loop_pointer_sources:
            source = self.loop_pointer_sources[state]
            while source in self.loop_pointer_sources:
                source = self.loop_pointer_sources[source]
            if source in self.all_updated_pointer_states:
                raise ValueError(
                    "Cannot snapshot an updated pointer in a loop-local alias"
                )
        return nodes

    def _parse_block(
        self,
        text: str,
        induction_variables: frozenset[str],
        base_line: int,
    ) -> List[AdapterNode]:
        saved_dtypes = dict(self.register_dtypes)
        saved_pairs = dict(self.pair_registers)
        saved_addresses = dict(self.address_expressions)
        saved_address_ids = dict(self.address_state_ids)
        saved_names = set(self.register_names)
        saved_align_state_names = set(self.align_state_names)
        saved_align_state_ids = dict(self.align_state_ids)
        saved_scalar_names = set(self.scalar_names)
        saved_offset_scalar_names = set(self.offset_scalar_names)
        saved_scalar_dtypes = dict(self.local_scalar_dtypes)
        saved_scalar_initializers = dict(self.local_scalar_initializers)
        saved_ub_names = set(self.ub_names)
        saved_ub_aliases = dict(self.ub_aliases)
        saved_ub_dtypes = dict(self.ub_dtypes)
        saved_pointer_states = dict(self.pointer_states)
        saved_induction_variables = self._block_induction_variables
        self._block_induction_variables = induction_variables
        try:
            return self._parse_block_contents(text, induction_variables, base_line)
        finally:
            self.register_dtypes = saved_dtypes
            self.pair_registers = saved_pairs
            self.address_expressions = saved_addresses
            self.address_state_ids = saved_address_ids
            self.register_names = saved_names
            self.align_state_names = saved_align_state_names
            self.align_state_ids = saved_align_state_ids
            self.scalar_names = saved_scalar_names
            self.offset_scalar_names = saved_offset_scalar_names
            self.local_scalar_dtypes = saved_scalar_dtypes
            self.local_scalar_initializers = saved_scalar_initializers
            self.ub_names = saved_ub_names
            self.ub_aliases = saved_ub_aliases
            self.ub_dtypes = saved_ub_dtypes
            self.pointer_states = saved_pointer_states
            self._block_induction_variables = saved_induction_variables
            # Updates to outer pointers survive lexical block exit.
            self.updated_pointer_states.intersection_update(
                self.pointer_states.values()
            )

    def _parse_block_contents(
        self,
        text: str,
        induction_variables: frozenset[str],
        base_line: int,
    ) -> List[AdapterNode]:
        nodes: List[AdapterNode] = []
        pos = 0
        pending_unroll = 1

        while pos < len(text):
            pos = _skip_ws(text, pos)
            if pos >= len(text):
                break

            pragma = _PRAGMA_UNROLL_RE.match(text, pos)
            if pragma:
                pending_unroll = int(pragma.group(1) or pragma.group(2))
                pos = pragma.end()
                continue

            if text.startswith("for", pos) and _is_token_boundary(text, pos + 3):
                header_start = text.find("(", pos)
                if header_start < 0:
                    raise ValueError("Malformed for-loop in __VEC_SCOPE__")
                header_end = _find_matching_paren(text, header_start)
                body_open = text.find("{", header_end)
                if body_open < 0:
                    raise ValueError(
                        "Only braced for-loops are supported in __VEC_SCOPE__"
                    )
                body_close = _find_matching_brace(text, body_open)
                count, variable, start, step = self._loop_count_from_header(
                    text[header_start + 1 : header_end]
                )
                self._loop_inductions.append((variable, start, step))
                try:
                    body = self._parse_block(
                        text[body_open + 1 : body_close],
                        induction_variables | {variable},
                        base_line + text[: body_open + 1].count("\n"),
                    )
                finally:
                    self._loop_inductions.pop()
                nodes.append(
                    AdapterLoop(
                        count=count,
                        unroll=pending_unroll,
                        body=body,
                        induction_variable=variable,
                        induction_start=start,
                        induction_step=step,
                        source_location=self._source_location(
                            base_line + text[:pos].count("\n")
                        ),
                    )
                )
                pending_unroll = 1
                pos = body_close + 1
                continue

            stmt_end = text.find(";", pos)
            if stmt_end < 0:
                trailing = text[pos:].strip()
                if trailing:
                    raise ValueError(f"Unsupported trailing CCE text: {trailing[:80]}")
                break

            stmt = text[pos : stmt_end + 1].strip()
            node = self._parse_statement(
                stmt,
                induction_variables,
                base_line + text[:pos].count("\n"),
            )
            if node is not None:
                nodes.extend(node if isinstance(node, list) else [node])
            pos = stmt_end + 1

        return nodes

    def _parse_statement(
        self,
        stmt: str,
        induction_variables: frozenset[str],
        line: int,
    ) -> AdapterNode | None:
        if not stmt:
            return None
        self._instruction_prefix = []
        for match in re.finditer(r"\b([A-Za-z_]\w*)\.val\[([^]]+)\]", stmt):
            if match[1] in self.pair_registers and match[2] not in {"0", "1"}:
                raise ValueError("vector_2xvl component index must be zero or one")
        stmt = re.sub(
            r"\b([A-Za-z_]\w*)\.val\[([01])\]",
            lambda m: self.pair_registers[m[1]][int(m[2])].value_id
            if m[1] in self.pair_registers
            else m[0],
            stmt,
        )
        stmt = re.sub(
            r"\b([A-Za-z_]\w*)\.val\b(?!\[)",
            lambda m: m[1] if self.register_dtypes.get(m[1]) == "s4x2" else m[0],
            stmt,
        )
        if stmt.startswith("vector_address "):
            declaration = _VECTOR_DECL_STMT_RE.fullmatch(stmt.strip().rstrip(";"))
            for item in _split_args(declaration[2]):
                name, _, initializer = item.partition("=")
                self.address_expressions[name.strip()] = initializer.strip() or None
                self.address_state_ids[name.strip()] = self._new_pointer_state(
                    "address:" + name.strip()
                )
            return None
        address_assignment = re.fullmatch(
            r"([A-Za-z_]\w*)\s*=\s*(.*);", stmt, re.DOTALL
        )
        if address_assignment and address_assignment[1] in self.address_expressions:
            rhs = address_assignment[2].strip()
            self.address_expressions[address_assignment[1]] = (
                self.address_expressions.get(rhs, rhs)
            )
            return None
        if stmt.startswith("vector_"):
            self._record_vector_decl_statement(stmt)
            declaration = _VECTOR_DECL_STMT_RE.fullmatch(
                stmt.strip().rstrip(";").strip()
            )
            if declaration and declaration.group(1) in {"bool", "2xvl_s64", "2xvl_u64"}:
                result = []
                for item in _split_args(declaration.group(2)):
                    if "=" in item:
                        name, initializer = item.split("=", 1)
                        node = self._parse_statement(
                            f"{name.strip()} = {initializer.strip()};",
                            induction_variables,
                            line,
                        )
                        result.extend(node if isinstance(node, list) else [node])
                return result
            return None
        if _LOCAL_SCALAR_DECL_RE.fullmatch(stmt):
            self._record_local_scalar_decl_statement(stmt)
            return None
        if _LOCAL_UB_POINTER_DECL_RE.fullmatch(stmt):
            self._record_local_ub_pointer_decl_statement(stmt)
            return None
        if _VOID_USE_RE.fullmatch(stmt):
            return None
        register_alias = _REGISTER_ALIAS_ASSIGN_RE.fullmatch(stmt)
        if register_alias:
            dst = register_alias.group("dst")
            src = register_alias.group("src")
            if dst in self.pair_registers or src in self.pair_registers:
                if dst not in self.pair_registers or src not in self.pair_registers:
                    raise ValueError("Pair assignment requires two vector_2xvl values")
                return [
                    AdapterAlias(a, b, self._source_location(line), allow_bitcast=True)
                    for a, b in zip(self.pair_registers[dst], self.pair_registers[src])
                ]
            if dst not in self.register_names or src not in self.register_names:
                raise ValueError(
                    f"Register alias assignment requires declared vector values: {stmt}"
                )
            return AdapterAlias(
                destination=self._typed_register(dst),
                source=self._typed_register(src),
                source_location=self._source_location(line),
            )
        assignment = re.fullmatch(
            r"([A-Za-z_]\w*)\s*=\s*(pset_b(?:8|16|32)|pge_b(?:8|16|32)|pset_2xvl_b64|movp_b(?:16|32)|plt_b(?:8|16|32)|plt_2xvl_b64|pltm_b(?:8|16|32)|pltm_2xvl_b64)\s*\((.*)\)\s*;",
            stmt,
            re.DOTALL,
        )
        if assignment:
            name, call, arguments = assignment.groups()
            bound_arguments = ", ".join([name, *_split_args(arguments)])
            return self._parse_statement(
                f"{call}({bound_arguments});", induction_variables, line
            )
        smem_bar = re.match(r"SMEM_BAR\s*\.\s*([A-Za-z_]\w*)\s*;", stmt, re.IGNORECASE)
        if smem_bar:
            return AdapterMembar(
                normalize_membar_type(smem_bar.group(1)),
                self._source_location(line),
            )

        match = _CALL_RE.fullmatch(stmt)
        if not match:
            raise ValueError(f"Unsupported CCE statement in __VEC_SCOPE__: {stmt}")

        callee = match.group(1)
        args = _split_args(match.group(2))
        low = callee.lower()
        if low in {"mem_bar", "membar"} or "barrier" in low:
            barrier = args[0] if args else None
            return AdapterMembar(
                normalize_membar_type(barrier),
                self._source_location(line),
            )
        self._address_bindings = {
            i: self.address_state_ids[a.strip()]
            for i, a in enumerate(args)
            if a.strip() in self.address_state_ids
        }
        args = [self.address_expressions.get(a.strip(), a) or a for a in args]
        immediate = re.fullmatch(r"vdupi_([su])(8|16)", low)
        if immediate:
            if len(args) != 2:
                raise ValueError("VDUPI requires a vector and an immediate")
            dtype = ("int" if immediate[1] == "s" else "uint") + immediate[2]
            value = re.sub(r"^\s*\([^)]*\)\s*", "", args[1])
            literal = _eval_int_expr(value, self._resolved_integer_scalar_constants())
            if literal is None:
                raise ValueError("VDUPI requires a constant integer immediate")
            target = self._typed_register(_base_identifier(args[0]))
            location = self._source_location(line)
            low_value = AdapterValue(str(literal & 255), "Scalar", dtype)
            nodes = [
                AdapterInstruction(
                    "VDUPI",
                    [low_value],
                    [target],
                    form=dtype,
                    instruction_class="compute",
                    source_location=location,
                )
            ]
            if immediate[2] == "16":
                high_value = AdapterValue(str((literal >> 8) & 255), "Scalar", dtype)
                nodes.append(
                    AdapterInstruction(
                        "VDUPI_HI",
                        [target, high_value],
                        [target],
                        form=dtype,
                        instruction_class="compute",
                        source_location=location,
                    )
                )
            return nodes
        op = normalize_opcode(callee)
        if op == "VGATHERB" and len(args) == 3:
            op = "VGATHERB_UNMASKED"
        if op == "VLD" and len(args) == 3:
            destination = args[0]
            if self.register_dtypes.get(_base_identifier(destination)) == "s4x2":
                destination = f"(vector_s8 &){destination}"
            op, args = "VLDS", [destination, args[1], "0", args[2]]
        elif (
            op == "VLD"
            and len(args) == 4
            and _base_identifier(args[1]) in self.register_names
            and args[-1].strip()
            in DEFAULT_INSTRUCTION_CATALOG.lookup("VLD").memory_modes
        ):
            op, args = "VLDS", [*args[:3], "0", args[3]]
        if low in {"vload", "vstore"}:
            return self._memory_wrapper(low, args, line)
        lowered = self._software_call(low, args, line)
        if lowered is not None:
            return lowered
        callee_form = re.search(r"_b(8|16|32)$", low)
        if low in {"plt_b8", "plt_b16", "plt_b32", "plt_2xvl_b64"}:
            if len(args) != 3 or args[2].strip() != "POST_UPDATE":
                raise ValueError(f"{callee} requires a scalar count and POST_UPDATE")
            args = [args[0], args[1], args[1]]
        if op == "VLDU" and len(args) == 5:
            args = [*args, "POST_UPDATE"]
        if op == "VSTU" and (len(args) != 5 or args[4].strip() != "POST_UPDATE"):
            raise ValueError("VSTU requires POST_UPDATE")
        if op == "VSTUR" and len(args) == 4:
            width = dtype_element_bytes(
                self.register_dtypes.get(_base_identifier(args[1]))
            )
            if width is None or args[3].strip() not in {
                "POST_UPDATE",
                "NO_POST_UPDATE",
            }:
                raise ValueError("Invalid vstur signature")
            args = [
                args[0],
                str(256 // width),
                args[1],
                args[2],
                *([args[3]] if args[3].strip() == "POST_UPDATE" else []),
            ]
        if op == "VMOV" and len(args) in (3, 4):
            op = "VMOV_MERGE"
        if op == "VDUP":
            scalar = self.register_dtypes.get(_base_identifier(args[1])) is None
            valid = (
                len(args) == 4
                and scalar
                and args[3].strip() in {"MODE_ZEROING", "MODE_MERGING"}
                or len(args) == 5
                and not scalar
                and args[3].strip() in {"POS_LOWEST", "POS_HIGHEST"}
                and args[4].strip() in {"MODE_ZEROING", "MODE_MERGING"}
            )
            if not valid:
                raise ValueError(
                    "Unsupported vdup call variant; expected scalar or data vector signature"
                )
            merging = args[-1].strip() == "MODE_MERGING"
            highest = len(args) == 5 and args[3].strip() == "POS_HIGHEST"
            op = ("VDUPS" if scalar else "VDUP_HIGHEST" if highest else "VDUP") + (
                "_MERGE" if merging else ""
            )
            args = args[:3]
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
        if spec is None and not low.startswith(("v", "pset_")):
            raise ValueError(f"Unsupported CCE statement in __VEC_SCOPE__: {stmt}")

        if spec is None:
            return self._bind_generic_compute_call(
                callee, op, args, self._source_location(line)
            )
        if spec.virtual and spec.form_rule == FormRule.CONVERSION and len(args) >= 2:
            form = self._conversion_form(args)
            if form:
                op = DEFAULT_INSTRUCTION_CATALOG.specialize(op, form)
                spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
                if spec.virtual and not spec.opcode.startswith(
                    ("VFCVT_", "VSFCVT_", "VSCVT_")
                ):
                    raise ValueError(f"Unsupported semantic form for {callee}: {form}")
        catalog_mode = None
        for mode, variant in spec.memory_modes.items():
            mode_operand = next(o for o in variant.operands if o.name == "mode")
            if (
                len(args) > mode_operand.argument_index
                and args[mode_operand.argument_index].strip() == mode
            ):
                catalog_mode, spec = mode, variant
                break
        src, dst = self._bind_catalog_call(callee, spec, args, induction_variables)
        if op == "VDUPS_MERGE" and dst[0].dtype == "bf16":
            # CANN's BF16 scalar wrapper merges an uninitialized uint16 local.
            # Its caller destination is not an input to the emitted instruction.
            src[0] = self._temporary_register("bf16")
        if len(dst) > 1:
            if len({value.value_id for value in dst}) != len(dst):
                raise ValueError("Multi-result destinations must be distinct")
            register_values = [
                value for value in (*src, *dst) if value.storage == "Register"
            ]
            if len({value.dtype for value in register_values}) > 1:
                raise ValueError(
                    "Multi-result register operands must have matching dtypes"
                )
        form = _infer_inst_form(op, dst, src)
        if spec.opcode == "VGATHER2":
            # The byte gather zero-extends into 16-bit destination lanes.
            form = _cce_scalar_dtype_to_form(self._infer_memory_form(spec, args) or "")
        if (
            spec.instruction_class.value == "compute"
            or op in {"VLDS", "VLD", "VSTS", "VST"}
        ) and spec.form_rule in {
            FormRule.OPERAND_DTYPE,
            FormRule.SOURCE_DTYPE,
        }:
            primary = next(
                (o for o in spec.output_operands if o.kind == ArgumentKind.REGISTER),
                None,
            )
            if primary is None or spec.form_rule == FormRule.SOURCE_DTYPE:
                primary = next(
                    (o for o in spec.input_operands if o.kind == ArgumentKind.REGISTER),
                    None,
                )
            if primary is not None and primary.argument_index < len(args):
                view = re.match(
                    r"\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\)",
                    args[primary.argument_index],
                )
                if view:
                    form = _vector_dtype_to_form(view.group(1))
        if spec.form_rule == FormRule.CONVERSION:
            form = self._conversion_form(args) or form
        elif spec.fixed_form and "_to_" in spec.fixed_form:
            actual_form = self._conversion_form(args)
            if actual_form and actual_form != spec.fixed_form:
                raise ValueError(
                    f"{callee} requires {spec.fixed_form}, got {actual_form}"
                )
        if callee_form and f"b{callee_form.group(1)}" in spec.forms:
            form = f"b{callee_form.group(1)}"
        if low in {"plt_2xvl_b64", "pltm_2xvl_b64"}:
            form = "b32"
        if form is None:
            form = self._infer_memory_form(spec, args)
        resolved_op, resolved_form = (
            DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(op, form)
        )
        if spec.opcode == "PMOV":
            # CCEC lowers predicate copies to PAND, using the source as both
            # data operands and (for the unmasked form) as the mask as well.
            source = src[0]
            src = [source, source, src[1] if len(src) == 2 else source]
        if spec.opcode == "VCADD":
            source_dtype = src[0].dtype
            result_dtype = {"int16": "int32", "uint16": "uint32"}.get(
                source_dtype, source_dtype
            )
            if dst[0].dtype != result_dtype:
                raise ValueError(
                    f"VCADD {source_dtype} requires a {result_dtype} destination"
                )
        if spec.opcode == "VEXPDIF":
            source_views = []
            for text, value in zip(args[1:3], src[:2]):
                view = re.match(r"\s*\(\s*vector_([A-Za-z0-9_]+)\s*&?\s*\)", text)
                source_views.append(
                    _vector_dtype_to_form(view[1]) if view else value.dtype
                )
            if (
                dst[0].dtype != "fp32"
                or source_views[0] not in {"fp16", "fp32"}
                or source_views[1] != source_views[0]
            ):
                raise ValueError(
                    "Unsupported semantic form for VEXPDIF: requires matching FP16/FP32 sources and an FP32 destination"
                )
        if spec.opcode == "VMULS" and resolved_form == "bf16":
            # The CANN BF16 wrapper broadcasts the scalar, then multiplies.
            while True:
                temporary = f"__vfsim_vector_{self._vector_serial}"
                self._vector_serial += 1
                if (
                    temporary
                    not in self.register_names | self.scalar_names | self.ub_names
                ):
                    break
            self.register_names.add(temporary)
            scalar_vector = AdapterValue(temporary, "Register", "bf16")
            self._instruction_prefix.append(
                AdapterInstruction(
                    name="VDUPS",
                    form="bf16",
                    src=[src[1], src[2]],
                    dst=[scalar_vector],
                    instruction_class="compute",
                )
            )
            src = [src[0], scalar_vector, src[2]]
        for operand in spec.operands:
            allowed = operand.allowed_values_by_form.get(resolved_form)
            if (
                allowed is not None
                and operand.argument_index < len(args)
                and args[operand.argument_index].strip() not in allowed
            ):
                raise ValueError(
                    f"{callee} {resolved_form} argument {operand.name} is outside its allowed values"
                )
        if spec.virtual and spec.opcode.startswith(("VFCVT_", "VSFCVT_", "VSCVT_")):
            return self._lower_conversion_wrapper(
                spec, args, src, dst, resolved_form, line
            )
        attributes: dict[str, str] = {}
        if catalog_mode is not None:
            if resolved_form not in spec.forms or any(
                value.dtype != dst[0].dtype for value in dst
            ):
                raise ValueError(
                    f"Unsupported destination types for {op} {catalog_mode}"
                )
            if len({value.value_id for value in dst}) != len(dst):
                raise ValueError("Multi-result load destinations must be distinct")
            attributes["catalog_mode"] = catalog_mode
        if spec.align_state_argument_index is not None:
            state_name = _base_identifier(args[spec.align_state_argument_index])
            attributes = {
                "align_state_operation": str(spec.align_state_operation),
                "align_state_id": self.align_state_ids[state_name],
            }
        instruction = AdapterInstruction(
            name=resolved_op,
            form=resolved_form,
            src=src,
            dst=dst,
            instruction_class=spec.instruction_class.value,
            memory_accesses=self._memory_accesses_for_call(spec, args, resolved_form),
            source_location=self._source_location(line),
            attributes=attributes,
        )
        merging = any(
            o.name == "mode"
            and o.argument_index < len(args)
            and args[o.argument_index].strip() == "MODE_MERGING"
            for o in spec.operands
        )
        if merging and spec.opcode != "VMOV_MERGE":
            if len(dst) != 1 or dst[0].storage != "Register":
                raise ValueError(f"Unsupported merging destination for {callee}")
            mask = next(
                (v for v in reversed(src) if v.storage == "PredicateRegister"), None
            )
            if mask is None:
                raise ValueError(f"Merging requires a predicate for {callee}")
            temporary = self._temporary_register(dst[0].dtype)
            instruction.dst = [temporary]
            merge = AdapterInstruction(
                "VMOV_MERGE",
                [dst[0], temporary, mask],
                dst,
                form=resolved_form,
                instruction_class="compute",
                source_location=instruction.source_location,
            )
            for producer in self._instruction_prefix:
                producer.source_location = instruction.source_location
            return [*self._instruction_prefix, instruction, merge]
        for producer in self._instruction_prefix:
            producer.source_location = instruction.source_location
        return (
            [*self._instruction_prefix, instruction]
            if self._instruction_prefix
            else instruction
        )

    def _temporary_register(self, dtype: str) -> AdapterValue:
        predicate = dtype == "bool"
        while True:
            if predicate:
                name = f"__vfsim_predicate_{self._predicate_serial}"
                self._predicate_serial += 1
            else:
                name = f"__vfsim_vector_{self._vector_serial}"
                self._vector_serial += 1
            if name not in self.register_names | self.scalar_names | self.ub_names:
                break
        self.register_names.add(name)
        self.register_dtypes[name] = dtype
        return self._typed_register(name)

    def _declare_pair(self, name, dtype):
        component = "int32" if dtype == "2xvl_s64" else "uint32"
        self.pair_registers[name] = tuple(
            self._temporary_register(component) for _ in range(2)
        )

    def _temporary_scalar(self, dtype):
        while True:
            name = f"__vfsim_scalar_{self._software_scalar_serial}"
            self._software_scalar_serial += 1
            if name not in self.scalar_names | self.register_names | self.ub_names:
                break
        self.scalar_names.add(name)
        self.local_scalar_dtypes[name] = dtype
        return AdapterValue(name, "Scalar", dtype)

    def _software_call(self, api, args, line):
        from .software import select_recipe, emit_recipe

        dtypes = []
        for arg in args:
            cast = re.match(
                r"\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\)", arg
            )
            dtypes.append(
                _vector_dtype_to_form(cast[1])
                if cast
                else self.register_dtypes.get(_base_identifier(arg))
            )
        pair = any(str(dtype).startswith("2xvl_") for dtype in dtypes)
        unmasked_vcvt = api == "vcvt" and len(dtypes) >= 3 and dtypes[2] != "bool"
        if (
            not pair
            and not unmasked_vcvt
            and not (
                api == "vmod"
                or (
                    api == "vdiv"
                    and dtypes
                    and dtypes[0] in {"int16", "uint16", "int32", "uint32"}
                )
            )
        ):
            return None
        if pair and api in {"vlds", "vsts"}:
            return self._pair_memory(api, args, line)
        if pair and api in {"vgather2", "vscatter"}:
            return self._pair_indexed_memory(api, args, line)
        recipe = select_recipe(api, args, dtypes)
        if recipe is None:
            raise ValueError(f"Unsupported CANN software signature: {api}")
        arguments = {}
        for operand in recipe["arguments"]:
            if operand["config"]:
                continue
            position = operand["position"]
            arg = args[position]
            name = _base_identifier(arg)
            if operand["type"].startswith("vector_2xvl_"):
                if name not in self.pair_registers:
                    raise ValueError(f"Expected a vector_2xvl value: {arg}")
                arguments[position] = self.pair_registers[name]
            elif operand["type"].startswith("vector_"):
                if name not in self.register_names:
                    raise ValueError(f"Expected a declared vector value: {arg}")
                arguments[position] = self._typed_register(name)
            elif name in self.scalar_names:
                arguments[position] = AdapterValue(
                    name,
                    "Scalar",
                    _cce_scalar_dtype_to_form(self.local_scalar_dtypes[name]),
                )
            elif _is_numeric_scalar_literal(arg):
                arguments[position] = AdapterValue(
                    arg.strip(), "Scalar", _cce_scalar_dtype_to_form(operand["type"])
                )
            else:
                raise ValueError(f"Expected a scalar argument: {arg}")
        destinations = [
            arguments[o["position"]] for o in recipe["arguments"] if "&" in o["type"]
        ]
        ids = [
            v.value_id
            for dst in destinations
            for v in (dst if isinstance(dst, tuple) else (dst,))
        ]
        if len(ids) != len(set(ids)):
            raise ValueError("Multi-result destinations must be distinct")
        return emit_recipe(
            recipe,
            arguments,
            self._temporary_register,
            self._temporary_scalar,
            self._source_location(line),
        )

    def _pair_indexed_memory(self, api, args, line):
        """CANN implements each 64-bit lane as two indexed 32-bit accesses."""
        if len(args) != 4:
            raise ValueError(f"{api} vector_2xvl expects four arguments")
        pair = self.pair_registers[_base_identifier(args[0])]
        index_name, mask_name = map(_base_identifier, args[2:])
        if self.register_dtypes.get(index_name) != "uint32":
            raise ValueError("Pair indexed memory requires vector_u32 indices")
        if self.register_dtypes.get(mask_name) != "bool":
            raise ValueError("Pair indexed memory requires a predicate")
        ref = self._parse_ub_reference(args[1])
        if ref is None:
            raise ValueError("Pair indexed memory requires a declared UB pointer")
        base, alias = ref
        root_dtype = _cce_scalar_dtype_to_form(self.ub_dtypes[base])
        if dtype_element_bytes(root_dtype) != 8:
            raise ValueError("Pair indexed memory requires a 64-bit element pointer")
        memory = AdapterValue(base, "UB", root_dtype)
        index, mask = self._typed_register(index_name), self._typed_register(mask_name)
        low_index, high_index = (self._temporary_register("uint32") for _ in range(2))
        location = self._source_location(line)
        nodes = [
            AdapterInstruction(
                "VMULS",
                [index, AdapterValue("2", "Scalar", "uint32"), mask],
                [low_index],
                form="uint32",
                instruction_class="compute",
                source_location=location,
            ),
            AdapterInstruction(
                "VADDS",
                [low_index, AdapterValue("1", "Scalar", "uint32"), mask],
                [high_index],
                form="uint32",
                instruction_class="compute",
                source_location=location,
            ),
        ]
        load = api == "vgather2"
        for value, indices in zip(pair, (low_index, high_index)):
            access = AdapterMemoryAccess(
                base,
                "read" if load else "write",
                f"({alias})*8",
                256,
                address_unit_bytes=1,
            )
            nodes.append(
                AdapterInstruction(
                    api.upper() + "_BYTES",
                    [memory, indices, mask] if load else [value, indices, mask],
                    [value] if load else [memory],
                    form=value.dtype,
                    instruction_class="load" if load else "store",
                    memory_accesses=(access,),
                    source_location=location,
                    attributes={"cann_software_api": api},
                )
            )
        return nodes

    def _memory_wrapper(self, api, args, line):
        load = api == "vload"
        minimum = 2 if load else 3
        if not minimum <= len(args) <= minimum + 1:
            raise ValueError(f"{api} has an invalid argument count")
        mode = args[minimum].strip() if len(args) > minimum else "ADDRESS_UNALIGNED"
        if mode not in {"ADDRESS_ALIGNED", "ADDRESS_UNALIGNED"}:
            raise ValueError(f"Invalid {api} address type")
        reg_name = _base_identifier(args[0 if load else 1])
        dtype = self.register_dtypes.get(reg_name)
        pair = reg_name in self.pair_registers
        if not pair and dtype not in {
            "int8",
            "uint8",
            "int16",
            "uint16",
            "int32",
            "uint32",
            "fp16",
            "fp32",
            "bf16",
        }:
            raise ValueError(f"Unsupported {api} vector type")
        pointer = args[1 if load else 0]
        ref = self._parse_ub_reference(pointer)
        if ref is None:
            raise ValueError(f"{api} requires a declared UB pointer")
        base, offset = ref
        root_dtype = _cce_scalar_dtype_to_form(self.ub_dtypes[base])
        root_width = dtype_element_bytes(root_dtype)
        if root_width is None:
            raise ValueError("Unknown UB pointer width")
        memory = AdapterValue(base, "UB", root_dtype)
        location = self._source_location(line)
        nodes = []
        state = self._new_align_state_id(api)
        cursor = self._new_pointer_state(api)

        def emit(
            op,
            form,
            inputs,
            outputs,
            at,
            *,
            advance=None,
            alignment=None,
            byte=False,
            span=None,
        ):
            access = AdapterMemoryAccess(
                base,
                "read"
                if DEFAULT_INSTRUCTION_CATALOG.lookup(op).instruction_class.value
                == "load"
                else "write",
                at,
                span,
                address_state_id=cursor,
                update_mode="post_update" if advance is not None else "none",
                post_update_delta_bytes=advance,
                address_unit_bytes=1 if byte else None,
            )
            attrs = {"cann_software_api": api}
            if alignment:
                attrs.update(align_state_operation=alignment, align_state_id=state)
            nodes.append(
                AdapterInstruction(
                    op,
                    list(inputs),
                    list(outputs),
                    form=form,
                    instruction_class=DEFAULT_INSTRUCTION_CATALOG.lookup(
                        op
                    ).instruction_class.value,
                    memory_accesses=(access,),
                    source_location=location,
                    attributes=attrs,
                )
            )

        if load:
            if mode == "ADDRESS_ALIGNED":
                if pair:
                    return self._pair_memory("vlds", [args[0], pointer, "0"], line)
                emit("VLDS", dtype, [memory], [self._typed_register(reg_name)], offset)
            elif pair:
                at = f"({offset})*{root_width}"
                emit(
                    "VLDAS_BYTES",
                    "b32",
                    [memory],
                    [],
                    at,
                    alignment="load_init",
                    byte=True,
                    span=256,
                )
                chunks = [self._temporary_register("uint32") for _ in range(2)]
                for chunk in chunks:
                    emit(
                        "VLDUS_BYTES",
                        "b32",
                        [memory],
                        [chunk],
                        at,
                        advance=256,
                        alignment="load_use",
                        byte=True,
                        span=256,
                    )
                nodes.append(
                    AdapterInstruction(
                        "VDINTLV",
                        chunks,
                        list(self.pair_registers[reg_name]),
                        form="uint32",
                        instruction_class="compute",
                        source_location=location,
                        attributes={"cann_software_api": api},
                    )
                )
            else:
                emit("VLDAS", dtype, [memory], [], offset, alignment="load_init")
                emit(
                    "VLDUS",
                    dtype,
                    [memory],
                    [self._typed_register(reg_name)],
                    offset,
                    alignment="load_use",
                )
            return nodes
        count_expr = self._expand_offset_expression(args[2])
        count = _eval_int_expr(count_expr, self._resolved_integer_scalar_constants())
        if mode == "ADDRESS_ALIGNED":
            mask = self._temporary_register("bool")
            counter = self._temporary_scalar("uint32")
            raw = args[2].strip()
            name = _base_identifier(raw)
            source = (
                AdapterValue(name, "Scalar", "uint32")
                if name in self.scalar_names
                else AdapterValue(str(count), "Scalar", "uint32")
                if count is not None
                else self._temporary_scalar("uint32")
            )
            width = 32 if pair else dtype_element_bytes(dtype) * 8
            nodes.append(
                AdapterInstruction(
                    "PLT",
                    [source],
                    [mask, counter],
                    form=f"b{width}",
                    instruction_class="compute",
                    source_location=location,
                    attributes={"cann_software_api": api},
                )
            )
            if pair:
                nodes.extend(
                    self._pair_memory(
                        "vsts", [args[1], pointer, "0", mask.value_id], line
                    )
                )
            else:
                emit(
                    "VSTS",
                    dtype,
                    [self._typed_register(reg_name), mask],
                    [memory],
                    offset,
                )
            return nodes
        if count is None:
            raise ValueError(
                "Unaligned vstore requires elementCount in loop_params for its pointer advances"
            )
        if count < 0 or count > (64 if pair else 256 // dtype_element_bytes(dtype)):
            raise ValueError("vstore elementCount exceeds one vector")
        if pair:
            at = f"({offset})*{root_width}"
            chunks = [self._temporary_register("uint32") for _ in range(2)]
            nodes.append(
                AdapterInstruction(
                    "VINTLV",
                    list(self.pair_registers[reg_name]),
                    chunks,
                    form="uint32",
                    instruction_class="compute",
                    source_location=location,
                    attributes={"cann_software_api": api},
                )
            )
            for chunk, elements in zip(chunks, (min(32, count), max(0, count - 32))):
                emit(
                    "VSTUS_BYTES",
                    "b32",
                    [chunk],
                    [memory],
                    at,
                    advance=elements * 8,
                    alignment="append",
                    byte=True,
                    span=256,
                )
            emit(
                "VSTAS_BYTES",
                "b32",
                [],
                [memory],
                at,
                alignment="consume",
                byte=True,
                span=256,
            )
        else:
            emit(
                "VSTUI",
                dtype,
                [self._typed_register(reg_name)],
                [memory],
                offset,
                advance=count * dtype_element_bytes(dtype),
                alignment="append",
            )
            # The native-width CANN wrapper discards the post-update pointer.
            cursor = self._new_pointer_state(api + ":flush")
            emit("VSTAI", dtype, [], [memory], offset, alignment="consume")
            nodes[-1].attributes["cann_pointer_result_discarded"] = True
        return nodes

    def _pair_memory(self, api, args, line):
        expected = 3 if api == "vlds" else 4
        if len(args) != expected:
            raise ValueError(f"{api} vector_2xvl expects {expected} arguments")
        pair = self.pair_registers[_base_identifier(args[0])]
        ref = self._parse_ub_reference(args[1])
        if ref is None:
            raise ValueError("Pair memory requires a declared UB pointer")
        base, alias = ref
        if dtype_element_bytes(_cce_scalar_dtype_to_form(self.ub_dtypes[base])) != 8:
            raise ValueError("Pair memory requires a 64-bit element pointer")
        offset = f"(({alias})+({self._expand_offset_expression(args[2])}))*8"
        memory = AdapterValue(
            base, "UB", _cce_scalar_dtype_to_form(self.ub_dtypes[base])
        )
        location = self._source_location(line)
        if api == "vlds":
            return [
                AdapterInstruction(
                    "VLDS_PAIR",
                    [memory],
                    list(pair),
                    form="b32",
                    instruction_class="load",
                    memory_accesses=(
                        AdapterMemoryAccess(
                            base, "read", offset, 512, address_unit_bytes=1
                        ),
                    ),
                    source_location=location,
                )
            ]
        mask_name = _base_identifier(args[3])
        if self.register_dtypes.get(mask_name) != "bool":
            raise ValueError("Pair store requires a predicate")
        mask = self._typed_register(mask_name)
        predicates = [self._temporary_register("bool") for _ in range(2)]
        vectors = [self._temporary_register("uint32") for _ in range(2)]
        nodes = [
            AdapterInstruction(
                "PINTLV",
                [mask, mask],
                predicates,
                form="b32",
                instruction_class="compute",
                source_location=location,
            ),
            AdapterInstruction(
                "VINTLV",
                list(pair),
                vectors,
                form="uint32",
                instruction_class="compute",
                source_location=location,
            ),
        ]
        for index in range(2):
            nodes.append(
                AdapterInstruction(
                    "VSTS_BYTES",
                    [vectors[index], predicates[index]],
                    [memory],
                    form="b32",
                    instruction_class="store",
                    memory_accesses=(
                        AdapterMemoryAccess(
                            base,
                            "write",
                            f"({offset})+{index * 256}",
                            256,
                            address_unit_bytes=1,
                        ),
                    ),
                    source_location=location,
                )
            )
        return nodes

    def _lower_conversion_wrapper(self, spec, args, src, dst, form, line):
        """Expand CANN 3101 fast conversion wrappers into physical operations."""
        options = {
            o.name: args[o.argument_index].strip()
            for o in spec.operands
            if o.kind == ArgumentKind.CONFIG and o.argument_index < len(args)
        }
        nodes = list(self._instruction_prefix)
        location = self._source_location(line)

        def emit(op, key, inputs, outputs):
            nodes.append(
                AdapterInstruction(
                    op,
                    list(inputs),
                    list(outputs),
                    form=key,
                    instruction_class="compute",
                    source_location=location,
                )
            )

        def predicate(op, key, inputs=()):
            value = self._temporary_register("bool")
            emit(op, key, inputs, [value])
            return value

        def pset(width, pattern="PAT_ALL"):
            # Pattern affects functional lanes; all PSET patterns share timing.
            result = predicate(f"PSET_B{width}", f"b{width}")
            nodes[-1].attributes["pattern"] = pattern
            return result

        def interleave(left, right):
            first, second = (
                self._temporary_register("bool"),
                self._temporary_register("bool"),
            )
            emit("PINTLV", "b8", [left, right], [first, second])
            return first

        masked = any(o.kind == ArgumentKind.PREDICATE for o in spec.operands)
        source_type, dest_type = form.split("_to_")
        source_width = dtype_element_bytes(normalize_dtype(source_type)) * 8
        family = spec.opcode.split("_", 1)[0]
        mask_width = (
            32
            if family == "VSCVT" and form.endswith("32_to_u8")
            else source_width
            if source_width <= 16 and dest_type in {"f32", "s32", "f16"} and not masked
            else 8
        )
        mask = src[1] if masked else pset(mask_width)
        merging = options.get("mode") == "MODE_MERGING"
        result = (
            self._temporary_register(normalize_dtype(dest_type)) if merging else dst[0]
        )
        emit("VCVT_" + form.upper(), form, [src[0], mask], [result])
        if merging:
            if masked:
                merge_mask = mask
            elif source_width == 32 and dest_type == "u8":
                part = int(options.get("pp", "PART_P0")[-1])
                if part == 0:
                    merge_mask = pset(8, "PAT_M4")
                else:
                    all_bits, zeros = pset(8), pset(8, "PAT_ALLF")
                    mid = (
                        interleave(zeros, all_bits)
                        if part in (2, 3)
                        else interleave(all_bits, zeros)
                    )
                    merge_mask = (
                        interleave(mid, zeros) if part == 2 else interleave(zeros, mid)
                    )
            else:
                merge_mask = pset(8, "PAT_H" if source_width == 16 else "PAT_M4")
                if source_width == 16:
                    merge_mask = predicate("PUNPACK", "b8", [merge_mask])
                if options.get("part") == "PART_ODD":
                    all_bits = mask if mask_width == 8 else pset(8)
                    merge_mask = predicate("PNOT", "b8", [merge_mask, all_bits])
            emit(
                "VMOV_MERGE",
                normalize_dtype(dest_type),
                [dst[0], result, merge_mask],
                dst,
            )
        for node in nodes:
            node.source_location = location
        return nodes

    def _conversion_form(self, args: Sequence[str]) -> str | None:
        """Read explicit vector views without allocating another register value."""
        if len(args) < 2:
            return None
        dtypes = []
        for arg in args[:2]:
            cast = re.match(
                r"\s*\(\s*(?:const\s+)?vector_([A-Za-z0-9_]+)\s*&?\s*\)", arg
            )
            dtypes.append(
                _vector_dtype_to_form(cast.group(1))
                if cast
                else self.register_dtypes.get(_base_identifier(arg))
            )
        dst_dtype, src_dtype = dtypes
        return (
            f"{compact_dtype(src_dtype)}_to_{compact_dtype(dst_dtype)}"
            if all(dtypes)
            else None
        )

    def _typed_register(self, name: str) -> AdapterValue:
        dtype = self.register_dtypes.get(name)
        return AdapterValue(
            name, "PredicateRegister" if dtype == "bool" else "Register", dtype
        )

    def _bind_catalog_call(
        self,
        callee: str,
        spec: InstructionSpec,
        args: Sequence[str],
        induction_variables: frozenset[str],
    ) -> tuple[List[AdapterValue], List[AdapterValue]]:
        src: List[AdapterValue] = []
        dst: List[AdapterValue] = []
        expected_count = (
            max((operand.argument_index for operand in spec.operands), default=-1) + 1
        )
        if spec.call_variants:
            matching_variants = [
                variant
                for variant in spec.call_variants
                if variant.argument_count == len(args)
                and all(
                    args[index].strip() in values
                    for index, values in variant.argument_values.items()
                )
            ]
            if not matching_variants:
                raise ValueError(
                    f"{callee} arguments do not match a declared Catalog call variant"
                )
        if len(args) > expected_count:
            raise ValueError(
                f"{callee} expects at most {expected_count} arguments, got {len(args)}"
            )
        for operand_spec in spec.operands:
            index = operand_spec.argument_index
            if index >= len(args):
                if operand_spec.optional:
                    continue
                raise ValueError(
                    f"{callee} is missing required argument {index} "
                    f"({operand_spec.name})"
                )
            operand = self._bind_catalog_argument(
                callee,
                args[index],
                operand_spec,
                index,
                induction_variables,
            )
            if operand is None or operand_spec.direction == OperandDirection.IGNORE:
                continue
            if operand_spec.direction in (
                OperandDirection.INPUT,
                OperandDirection.READ_WRITE,
            ):
                src.append(operand)
            if operand_spec.direction in (
                OperandDirection.OUTPUT,
                OperandDirection.READ_WRITE,
            ):
                dst.append(operand)
        return src, dst

    def _bind_catalog_argument(
        self,
        callee: str,
        arg: str,
        operand_spec,
        index: int,
        induction_variables: frozenset[str],
    ) -> AdapterValue | None:
        kind = operand_spec.kind
        name = _base_identifier(arg)
        if kind == ArgumentKind.ALIGN_STATE:
            if name not in self.align_state_names:
                raise ValueError(
                    f"{callee} argument {index} must be a declared vector_align state: {arg}"
                )
            return None
        if kind == ArgumentKind.REGISTER:
            if (
                name not in self.register_names
                or self.register_dtypes.get(name) == "bool"
            ):
                raise ValueError(
                    f"{callee} argument {index} must be a declared vector register: {arg}"
                )
            return AdapterValue(
                name,
                "Register",
                self.register_dtypes.get(name),
            )
        if kind == ArgumentKind.UB:
            ub_reference = self._parse_ub_reference(arg)
            if ub_reference is None:
                raise ValueError(
                    f"{callee} argument {index} must be a declared UB object: {arg}"
                )
            dtype = self.ub_dtypes.get(ub_reference[0])
            return AdapterValue(
                ub_reference[0],
                "UB",
                _cce_scalar_dtype_to_form(dtype) if dtype else None,
            )
        if kind in {ArgumentKind.SCALAR, ArgumentKind.REGISTER_OR_SCALAR}:
            if _is_numeric_scalar_literal(arg):
                return AdapterValue(
                    arg.strip(), "Scalar", _cast_numeric_scalar_dtype(arg)
                )
            if name in self.register_names:
                if (
                    kind == ArgumentKind.SCALAR
                    or self.register_dtypes.get(name) == "bool"
                ):
                    raise ValueError(f"{callee} argument {index} must be scalar: {arg}")
                return AdapterValue(
                    name,
                    "Register",
                    self.register_dtypes.get(name),
                )
            if name in self.ub_names:
                raise ValueError(
                    f"{callee} argument {index} cannot use a UB object as scalar: {arg}"
                )
            if name in self.scalar_names:
                return AdapterValue(
                    name,
                    "Scalar",
                    _cce_scalar_dtype_to_form(self.local_scalar_dtypes.get(name, "")),
                )
            raise ValueError(
                f"{callee} argument {index} must be a declared scalar or literal: {arg}"
            )
        if kind == ArgumentKind.PREDICATE:
            inline = re.fullmatch(
                r"(pset_b(?:8|16|32))\s*\((.*)\)", arg.strip(), re.DOTALL
            )
            if inline and operand_spec.direction == OperandDirection.INPUT:
                name = f"__predicate_inline_{self._predicate_serial}"
                self._predicate_serial += 1
                self.register_names.add(name)
                self.register_dtypes[name] = "bool"
                inline_spec = DEFAULT_INSTRUCTION_CATALOG.lookup(inline.group(1))
                source, destination = self._bind_catalog_call(
                    inline.group(1),
                    inline_spec,
                    [name, *_split_args(inline.group(2))],
                    induction_variables,
                )
                self._instruction_prefix.append(
                    AdapterInstruction(
                        inline_spec.opcode,
                        source,
                        destination,
                        form=inline_spec.fixed_form,
                        instruction_class="compute",
                    )
                )
            if self.register_dtypes.get(name) == "bool":
                return self._typed_register(name)
            raise ValueError(f"{callee} argument {index} must be a predicate: {arg}")
        if kind == ArgumentKind.CONFIG:
            if operand_spec.name == "offset" and operand_spec.allow_integer_expression:
                variable_names = set(induction_variables) | self.offset_scalar_names
                constant_names = set(self.loop_params)
                vag_match = re.fullmatch(
                    r"vag_b(?:8|16|32)\s*\((.*)\)", arg.strip(), re.DOTALL
                )
                candidate = vag_match.group(1) if vag_match else arg
                if vag_match:
                    candidate = "+".join(f"({a})" for a in _split_args(candidate))
                if _is_affine_int_expression(
                    candidate,
                    variable_names=variable_names,
                    constant_names=constant_names,
                    symbol_expressions={
                        name: initializer
                        for name, initializer in self.local_scalar_initializers.items()
                        if _is_integer_scalar_dtype(
                            self.local_scalar_dtypes.get(name, "")
                        )
                    },
                ):
                    return None
                raise ValueError(
                    f"{callee} argument {index} has invalid offset expression: {arg}"
                )
            integer_constants = self._resolved_integer_scalar_constants()
            if (
                operand_spec.allowed_values
                and arg.strip() not in operand_spec.allowed_values
                and not (
                    operand_spec.allow_integer_expression
                    and _eval_int_expr(arg.strip(), integer_constants) is not None
                )
            ):
                raise ValueError(
                    f"{callee} argument {index} has unsupported configuration: {arg}"
                )
            if (
                arg.strip() in operand_spec.allowed_values
                or _is_config_token(arg)
                or (
                    operand_spec.allow_integer_expression
                    and _eval_int_expr(arg.strip(), integer_constants) is not None
                )
            ):
                return None
            raise ValueError(
                f"{callee} argument {index} must be a configuration token: {arg}"
            )
        return None

    def _infer_memory_form(
        self,
        spec: InstructionSpec,
        args: Sequence[str],
    ) -> str | None:
        memory_operand = next(
            (
                operand
                for operand in spec.operands
                if operand.kind == ArgumentKind.UB
                and operand.argument_index < len(args)
            ),
            None,
        )
        if memory_operand is None:
            return None
        reference = self._parse_ub_reference(args[memory_operand.argument_index])
        if reference is None:
            return None
        return self.ub_dtypes.get(reference[0])

    def _resolved_integer_scalar_constants(self) -> Dict[str, int]:
        resolved = dict(self.loop_params)
        pending = {
            name: initializer
            for name, initializer in self.local_scalar_initializers.items()
            if _is_integer_scalar_dtype(self.local_scalar_dtypes.get(name, ""))
        }
        while pending:
            progressed = False
            for name, initializer in list(pending.items()):
                value = _eval_int_expr(initializer, resolved)
                if value is None:
                    continue
                resolved[name] = value
                del pending[name]
                progressed = True
            if not progressed:
                break
        return resolved

    def _bind_generic_compute_call(
        self,
        callee: str,
        op: str,
        args: Sequence[str],
        source_location: SourceLocation,
    ) -> AdapterInstruction:
        if not args:
            raise ValueError(f"{callee} expects at least one destination operand")
        if any(
            self.register_dtypes.get(_base_identifier(arg)) == "bool" or "pset_" in arg
            for arg in args
        ):
            raise ValueError(
                f"Predicate semantics require a complete Catalog signature: {callee}"
            )
        dst = [self._register_operand(args[0])]
        src = [operand for arg in args[1:] if (operand := self._operand_for_arg(arg))]
        form = _infer_inst_form(op, dst, src)
        return AdapterInstruction(
            name=op,
            form=form,
            src=src,
            dst=dst,
            instruction_class="compute",
            source_location=source_location,
        )

    def _source_location(self, line: int) -> SourceLocation:
        return SourceLocation(
            source=self.scope.kernel_name,
            line=line,
            path=self.scope.source_path,
        )

    def _vag_offset(self, text, width):
        strides = _split_args(text)
        if not 1 <= len(strides) <= 4:
            raise ValueError("VAG requires one to four loop strides")
        terms = []
        for i, stride in enumerate(reversed(strides)):
            stride = self._expand_offset_expression(stride)
            value = _eval_int_expr(stride, self._resolved_integer_scalar_constants())
            if value == 0:
                continue
            if i >= len(self._loop_inductions):
                raise ValueError("VAG stride has no enclosing hardware loop")
            var, start, step = self._loop_inductions[-i - 1]
            if step not in (1, -1):
                if value is None or value % step:
                    raise ValueError(
                        "VAG stride is not affine in the source loop index"
                    )
                stride = str(value // step)
            elif step == -1:
                stride = f"-({stride})"
            if any(
                re.search(r"\b" + re.escape(v) + r"\b", stride)
                for v, _, _ in self._loop_inductions
            ):
                raise ValueError("VAG strides must be loop invariant")
            terms.append(f"(({var})-({start}))*({stride})*{width}")
        return "+".join(terms) or "0"

    def _memory_accesses_for_call(
        self,
        spec: InstructionSpec,
        args: Sequence[str],
        form: str,
    ) -> tuple[AdapterMemoryAccess, ...]:
        memory_operand = next(
            (
                operand
                for operand in spec.operands
                if operand.kind == ArgumentKind.UB
                and operand.argument_index < len(args)
            ),
            None,
        )
        if memory_operand is None:
            return ()
        offset_operand = next(
            (operand for operand in spec.operands if operand.name == "offset"),
            None,
        )
        mode_operand = next(
            (
                operand
                for operand in spec.operands
                if operand.name in {"mode", "config"}
            ),
            None,
        )
        ub_reference = self._parse_ub_reference(args[memory_operand.argument_index])
        if ub_reference is None:
            return ()
        base_name, alias_offset = ub_reference
        mode = next(
            (
                args[o.argument_index].strip()
                for o in spec.operands
                if o.name in {"mode", "config"} and o.argument_index < len(args)
            ),
            None,
        )
        # The immediate predicate encodings count 16B/32B blocks. Scalar
        # PLDS/PSTS offsets count bytes. Nonconstant PLDI/PSTI calls are
        # selected as the scalar encoding by this CANN compiler.
        immediate_unit = (
            (16 if mode in {"US", "PK"} else 32)
            if spec.opcode in {"PLDI", "PSTI"}
            else 1
        )
        if offset_operand is not None and offset_operand.argument_index < len(args):
            if (
                _eval_int_expr(
                    args[offset_operand.argument_index],
                    self._resolved_integer_scalar_constants(),
                )
                is None
            ):
                immediate_unit = 1
        if spec.memory_address_unit_bytes is not None:
            dtype = _cce_scalar_dtype_to_form(self.ub_dtypes.get(base_name, ""))
            width = dtype_element_bytes(dtype)
            if width is None:
                raise ValueError(f"Unknown pointer width for {spec.opcode}: {dtype}")
            if spec.memory_address_unit_bytes != 1:
                raise ValueError(
                    "Only byte-addressed fixed-unit CCE memory operands are supported"
                )
            alias_offset = f"({alias_offset}) * {width}"
        pointer_expression = args[memory_operand.argument_index]
        pointer_name = (
            _strip_ub_reference_wrappers(pointer_expression).split("+", 1)[0].strip()
        )
        address_state = self._address_bindings.get(
            offset_operand.argument_index if offset_operand else -1
        )
        state_id = address_state or self.pointer_states[pointer_name]
        update_operand = next((o for o in spec.operands if o.name == "update"), None)
        post_update = (
            update_operand is not None
            and update_operand.argument_index < len(args)
            and args[update_operand.argument_index].strip() == "POST_UPDATE"
        )
        delta_bytes = spec.implicit_post_update_bytes.get(form)
        implicit_update = delta_bytes is not None
        post_update = post_update or implicit_update
        if post_update:
            if (
                not address_state
                and _strip_ub_reference_wrappers(pointer_expression) != pointer_name
            ):
                raise ValueError(
                    "POST_UPDATE requires a pointer variable, not pointer arithmetic"
                )
            if state_id in self.loop_pointer_states:
                raise ValueError(
                    "POST_UPDATE on a loop-local reinitialized pointer is not yet supported"
                )
            delta_operand = next(
                (o for o in spec.operands if o.post_update_delta is not None),
                None,
            )
            if delta_operand is None and not implicit_update:
                raise ValueError("POST_UPDATE has no declared delta rule")
            if not implicit_update:
                raw_delta = args[delta_operand.argument_index].strip()
                if re.search(r"\bvag_b(?:8|16|32)\s*\(", raw_delta):
                    raise ValueError(
                        "POST_UPDATE with VAG requires address-generator modeling"
                    )
                delta_bytes = self._decode_post_update_delta_bytes(
                    delta_operand,
                    raw_delta,
                    pointer_expression,
                    pointer_name,
                )
                if immediate_unit != 1:
                    delta_bytes = f"({delta_bytes})*{immediate_unit}"
            self.updated_pointer_states.add(state_id)
            self.all_updated_pointer_states.add(state_id)
        offset: int | str = alias_offset
        address_unit = spec.memory_address_unit_bytes
        if (
            (not post_update or spec.opcode in {"VLDU", "VSTU"})
            and offset_operand is not None
            and offset_operand.argument_index < len(args)
        ):
            raw_offset = args[offset_operand.argument_index].strip()
            vag = re.fullmatch(r"vag_b(8|16|32)\s*\((.*)\)", raw_offset, re.DOTALL)
            if vag:
                expanded_offset = self._vag_offset(vag[2], int(vag[1]) // 8)
                if address_unit is None:
                    width = dtype_element_bytes(
                        _cce_scalar_dtype_to_form(self.ub_dtypes[base_name])
                    )
                    alias_offset = f"({alias_offset})*{width}"
                address_unit = 1
            else:
                expanded_offset = self._expand_offset_expression(raw_offset)
                if immediate_unit != 1:
                    expanded_offset = f"({expanded_offset})*{immediate_unit}"
                if spec.opcode == "VSLDB":
                    expanded_offset = f"({expanded_offset})*32"
                elif address_unit == 1 and spec.opcode in {"VLDS", "VSTS"}:
                    width = dtype_element_bytes(
                        _cce_scalar_dtype_to_form(self.ub_dtypes[base_name])
                    )
                    expanded_offset = f"({expanded_offset})*{width}"
            offset = self._combine_offset_expressions(alias_offset, expanded_offset)
        mode = None
        if mode_operand is not None and mode_operand.argument_index < len(args):
            mode = args[mode_operand.argument_index].strip()
        span = (
            spec.memory_span_by_form.get(form)
            or spec.memory_span
            or (
                1
                if mode and (mode.startswith("BRC_") or mode.startswith("ONEPT_"))
                else None
            )
        )
        if spec.opcode == "VSSTB":
            config = _eval_int_expr(mode, self._resolved_integer_scalar_constants())
            if config is None or not 0 <= config < (1 << 64):
                raise ValueError(
                    "VSSTB configuration must resolve to a uint64 constant"
                )
            width = dtype_element_bytes(
                _cce_scalar_dtype_to_form(self.ub_dtypes[base_name])
            )
            stride = (config >> 16) & 65535
            span = (7 * stride + 1) * 32 // width
            if not post_update:
                offset = self._combine_offset_expressions(
                    str(offset), str((config & 65535) * 32 // width)
                )
        return (
            AdapterMemoryAccess(
                value_id=base_name,
                access_kind=(
                    "read" if spec.instruction_class.value == "load" else "write"
                ),
                offset=offset,
                span=span,
                address_unit_bytes=address_unit,
                mode=mode,
                address_state_id=state_id,
                update_mode="post_update" if post_update else "none",
                post_update_delta_bytes=delta_bytes,
            ),
        )

    def _decode_post_update_delta_bytes(
        self,
        operand: OperandSpec,
        raw_delta: str,
        pointer_expression: str,
        pointer_name: str,
    ) -> int | str:
        rule = operand.post_update_delta
        if rule is None:
            raise ValueError("POST_UPDATE has no declared delta rule")
        if rule.encoding == PostUpdateDeltaEncoding.UNSIGNED_BIT_FIELD:
            value = _eval_int_expr(
                raw_delta,
                self._resolved_integer_scalar_constants(),
            )
            if value is None or value < 0 or value >= 1 << 64:
                raise ValueError(
                    "POST_UPDATE packed configuration must resolve to an unsigned "
                    "64-bit integer constant"
                )
            mask = (1 << rule.bit_width) - 1
            return ((value >> rule.bit_offset) & mask) * rule.unit_bytes

        if rule.encoding == PostUpdateDeltaEncoding.BYTE_COUNT:
            return self._expand_offset_expression(raw_delta)
        dtype = self.ub_dtypes.get(pointer_name)
        casts = re.findall(
            r"\(\s*(?:(?:const|volatile)\s+)*__ubuf__\s+"
            r"(?:(?:const|volatile)\s+)*(\w+)\s*[*&]+\s*\)",
            pointer_expression,
        )
        if "__ubuf__" in pointer_expression and not casts:
            raise ValueError(
                f"Unsupported POST_UPDATE pointer cast: {pointer_expression}"
            )
        if casts:
            dtype = _cce_scalar_dtype_to_form(casts[0])
        width_dtype = dtype.removesuffix("_t") if dtype else None
        width = dtype_element_bytes(width_dtype)
        if width is None:
            raise ValueError(f"Unknown POST_UPDATE pointer element width: {dtype}")
        return f"({self._expand_offset_expression(raw_delta)}) * {width}"

    @staticmethod
    def _combine_offset_expressions(lhs: str, rhs: str) -> str:
        if lhs.strip() == "0":
            return rhs
        if rhs.strip() == "0":
            return lhs
        return f"({lhs}) + ({rhs})"

    def _resolve_ub_alias(self, name: str) -> tuple[str, str]:
        return self.ub_aliases.get(name, (name, "0"))

    def _parse_ub_reference(self, expression: str) -> tuple[str, str] | None:
        value = _strip_ub_reference_wrappers(expression)
        match = re.fullmatch(
            r"(?P<base>[A-Za-z_]\w*)(?:\s*\+\s*(?P<offset>.+))?",
            value,
            re.DOTALL,
        )
        if match is None:
            return None
        source_name = match.group("base")
        if source_name not in self.ub_names:
            return None
        base_name, alias_offset = self._resolve_ub_alias(source_name)
        offset = match.group("offset")
        if offset is None:
            return base_name, alias_offset
        inline_offset = self._expand_offset_expression(offset)
        return (
            base_name,
            self._combine_offset_expressions(alias_offset, inline_offset),
        )

    def _expand_offset_expression(self, expression: str) -> str:
        symbol_expressions = dict(self.local_scalar_initializers)

        class Expander(ast.NodeTransformer):
            def __init__(self) -> None:
                self.resolving: set[str] = set()

            def visit_Name(self, node: ast.Name):
                initializer = symbol_expressions.get(node.id)
                if initializer is None or node.id in self.resolving:
                    return node
                self.resolving.add(node.id)
                try:
                    replacement = ast.parse(initializer, mode="eval").body
                    return self.visit(replacement)
                finally:
                    self.resolving.remove(node.id)

        tree = ast.parse(expression.strip(), mode="eval")
        expanded = Expander().visit(tree)
        ast.fix_missing_locations(expanded)
        return ast.unparse(expanded.body)

    def _register_operand(self, arg: str) -> AdapterValue:
        name = _base_identifier(arg)
        if name not in self.register_names:
            raise ValueError(f"Expected declared vector register: {arg}")
        return AdapterValue(
            name,
            "Register",
            self.register_dtypes.get(name),
        )

    def _operand_for_arg(self, arg: str) -> AdapterValue | None:
        name = _base_identifier(arg)
        if name in self.register_names:
            return AdapterValue(
                name,
                "Register",
                self.register_dtypes.get(name),
            )
        ub_reference = self._parse_ub_reference(arg)
        if ub_reference is not None:
            return AdapterValue(ub_reference[0], "UB")
        if name in self.scalar_names:
            return AdapterValue(
                name,
                "Scalar",
                _cce_scalar_dtype_to_form(self.local_scalar_dtypes.get(name, "")),
            )
        return None

    def _record_vector_decl_statement(self, stmt: str) -> None:
        clean = stmt.strip().rstrip(";").strip()
        match = _VECTOR_DECL_STMT_RE.fullmatch(clean)
        if not match:
            return
        dtype, names_text = match.groups()
        form = _vector_dtype_to_form(dtype)
        for raw_name in _split_args(names_text):
            name = _declared_identifier(raw_name)
            if name:
                if form == "align":
                    self.register_names.discard(name)
                    self.register_dtypes.pop(name, None)
                    self.align_state_names.add(name)
                    self.align_state_ids[name] = self._new_align_state_id(name)
                    continue
                self.align_state_names.discard(name)
                self.align_state_ids.pop(name, None)
                self.register_dtypes[name] = form
                self.register_names.add(name)
                if form in {"2xvl_s64", "2xvl_u64"}:
                    self._declare_pair(name, form)

    def _new_align_state_id(self, name: str) -> str:
        state_id = f"{self.scope.kernel_name}:align:{self._align_state_serial}:{name}"
        self._align_state_serial += 1
        return state_id

    def _new_pointer_state(self, name: str) -> str:
        state = f"{self.scope.kernel_name}:pointer:{self._pointer_serial}:{name}"
        self._pointer_serial += 1
        return state

    def _record_function_scope_scalar_declarations(self, source: str) -> None:
        for segment in source.split(";"):
            stmt = segment.strip()
            if not stmt:
                continue
            candidate = f"{stmt};"
            if _LOCAL_SCALAR_DECL_RE.fullmatch(candidate):
                self._record_local_scalar_decl_statement(candidate)

    def _record_function_scope_ub_pointer_declarations(self, source: str) -> None:
        for segment in source.split(";"):
            stmt = segment.strip()
            if not stmt:
                continue
            candidate = f"{stmt};"
            if _LOCAL_UB_POINTER_DECL_RE.fullmatch(candidate):
                self._record_local_ub_pointer_decl_statement(candidate)

    def _record_local_scalar_decl_statement(
        self,
        stmt: str,
    ) -> None:
        match = _LOCAL_SCALAR_DECL_RE.fullmatch(stmt)
        if not match:
            raise ValueError(f"Unsupported local scalar declaration: {stmt}")
        dtype = match.group("dtype")
        for raw_declaration in _split_args(match.group("declarations")):
            declaration = re.fullmatch(
                r"([A-Za-z_]\w*)\s*(?:=\s*(.+))?",
                raw_declaration.strip(),
                re.DOTALL,
            )
            if not declaration:
                raise ValueError(f"Unsupported local scalar declaration: {stmt}")
            name, initializer = declaration.groups()
            self.scalar_names.add(name)
            self.offset_scalar_names.discard(name)
            self.local_scalar_dtypes[name] = dtype
            self.local_scalar_initializers.pop(name, None)
            if initializer is None:
                continue
            self.local_scalar_initializers[name] = initializer.strip()

    def _record_local_ub_pointer_decl_statement(self, stmt: str) -> None:
        match = _LOCAL_UB_POINTER_DECL_RE.fullmatch(stmt)
        if not match:
            raise ValueError(f"Unsupported local UB pointer declaration: {stmt}")
        name = match.group("name")
        dtype = _cce_scalar_dtype_to_form(match.group("dtype"))
        initializer = match.group("initializer").strip()
        source_name = _strip_ub_reference_wrappers(initializer).split("+", 1)[0].strip()
        source_state = self.pointer_states.get(source_name)
        if self.pointer_states.get(source_name) in self.updated_pointer_states:
            raise ValueError(f"Cannot snapshot an updated pointer alias: {stmt}")
        ub_reference = self._parse_ub_reference(initializer)
        if ub_reference is None:
            raise ValueError(f"Unsupported local UB pointer initializer: {stmt}")
        self.ub_names.add(name)
        self.ub_aliases[name] = ub_reference
        self.ub_dtypes[name] = dtype
        self.pointer_states[name] = self._new_pointer_state(name)
        if self._block_induction_variables:
            self.loop_pointer_states.add(self.pointer_states[name])
            if source_state is not None:
                self.loop_pointer_sources[self.pointer_states[name]] = source_state

    def _loop_count_from_header(self, header: str) -> tuple[int, str, int, int]:
        parts = [part.strip() for part in header.split(";")]
        if len(parts) < 2:
            raise ValueError(f"Unsupported for-loop header: {header}")
        init = parts[0]
        cond = parts[1]
        step_expr = parts[2] if len(parts) >= 3 else ""

        init_match = re.search(r"([A-Za-z_]\w*)\s*=\s*(.+)$", init)
        cond_match = re.search(r"([A-Za-z_]\w*)\s*(<|<=)\s*(.+)$", cond)
        if not init_match or not cond_match:
            raise ValueError(f"Unsupported for-loop header: {header}")

        var = init_match.group(1)
        if cond_match.group(1) != var:
            raise ValueError(f"Unsupported for-loop variable mismatch: {header}")

        integer_constants = self._resolved_integer_scalar_constants()
        start = _resolve_count_expr(init_match.group(2), integer_constants)
        bound = _resolve_count_expr(cond_match.group(3), integer_constants)
        step = _resolve_loop_step(step_expr, var, integer_constants)
        if step <= 0:
            raise ValueError(f"Only positive for-loop steps are supported: {header}")

        inclusive = cond_match.group(2) == "<="
        span = bound - start + (1 if inclusive else 0)
        if span <= 0:
            return 0, var, start, step
        return (span + step - 1) // step, var, start, step


def _infer_call_argument_constants(source: str, scope: CCEVFScope) -> Dict[str, int]:
    constants: Dict[str, int] = {}
    assign_re = re.compile(
        r"\b(?:int|uint16_t|uint32_t|int32_t)\s+([A-Za-z_]\w*)\s*=\s*(\d+)\s*;"
    )
    call_re = re.compile(
        rf"\b{re.escape(scope.kernel_name)}\s*\((.*?)\)\s*;", re.DOTALL
    )

    for call in call_re.finditer(source):
        args = _split_args(call.group(1))
        prefix = source[: call.start()]
        assigned = {name: int(value) for name, value in assign_re.findall(prefix)}
        for param, arg in zip(scope.params, args):
            arg_name = _base_identifier(arg)
            if arg_name in assigned:
                constants[param] = assigned[arg_name]
            elif arg_name.isdigit():
                constants[param] = int(arg_name)
    return constants


def _resolve_count_expr(expr: str, loop_params: Dict[str, int]) -> int:
    expr = expr.strip()
    while True:
        cast_match = re.fullmatch(r"[A-Za-z_]\w*\s*\(\s*(.+)\s*\)", expr)
        if not cast_match:
            break
        expr = cast_match.group(1).strip()
    if expr.isdigit():
        return int(expr)
    resolved = _eval_int_expr(expr, loop_params)
    if resolved is not None:
        return resolved
    name = _base_identifier(expr)
    raise ValueError(
        f"Cannot resolve loop bound '{expr}'. Pass loop_params={{'{name}': ...}} "
        "or use a constant loop bound."
    )


def _eval_int_expr(expr: str, names: Dict[str, int]) -> int | None:
    expr = re.sub(r"(?<=\d)[uUlL]+\b", "", expr.strip())
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None

    def visit(node: ast.AST) -> int | None:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return None
            if isinstance(node.value, int):
                return int(node.value)
            return None
        if isinstance(node, ast.Name):
            return int(names[node.id]) if node.id in names else None
        if isinstance(node, ast.UnaryOp):
            value = visit(node.operand)
            if value is None:
                return None
            if isinstance(node.op, ast.UAdd):
                return value
            if isinstance(node.op, ast.USub):
                return -value
            return None
        if isinstance(node, ast.BinOp):
            lhs = visit(node.left)
            rhs = visit(node.right)
            if lhs is None or rhs is None:
                return None
            if isinstance(node.op, ast.Add):
                return lhs + rhs
            if isinstance(node.op, ast.Sub):
                return lhs - rhs
            if isinstance(node.op, ast.Mult):
                return lhs * rhs
            if isinstance(node.op, ast.Div):
                if rhs == 0 or lhs % rhs != 0:
                    return None
                return lhs // rhs
            if isinstance(node.op, ast.FloorDiv):
                if rhs == 0:
                    return None
                return lhs // rhs
            if isinstance(node.op, ast.LShift):
                if rhs < 0:
                    return None
                return lhs << rhs
            if isinstance(node.op, ast.RShift):
                if rhs < 0:
                    return None
                return lhs >> rhs
            if isinstance(node.op, ast.BitOr):
                return lhs | rhs
            if isinstance(node.op, ast.BitAnd):
                return lhs & rhs
        return None

    return visit(tree)


def _resolve_loop_step(step_expr: str, var: str, loop_params: Dict[str, int]) -> int:
    expr = step_expr.strip()
    if not expr:
        return 1
    if re.fullmatch(rf"\+\+\s*{re.escape(var)}|{re.escape(var)}\s*\+\+", expr):
        return 1
    plus_eq = re.fullmatch(rf"{re.escape(var)}\s*\+=\s*(.+)", expr)
    if plus_eq:
        return _resolve_count_expr(plus_eq.group(1), loop_params)
    assign_plus = re.fullmatch(
        rf"{re.escape(var)}\s*=\s*{re.escape(var)}\s*\+\s*(.+)", expr
    )
    if assign_plus:
        return _resolve_count_expr(assign_plus.group(1), loop_params)
    raise ValueError(f"Unsupported for-loop step: {step_expr}")


def _is_numeric_scalar_literal(value: str) -> bool:
    text = value.strip()
    text = re.sub(
        r"^\(\s*(?:u?int(?:8|16|32|64)_t|unsigned|signed|int)\s*\)\s*", "", text
    )
    text = re.sub(r"^[()]|[()]$", "", text).strip()
    if re.fullmatch(
        r"[+-]?(?:0[xX][0-9a-fA-F]+|[0-9]+)(?:[uU](?:[lL]{1,2})?|[lL]{1,2}[uU]?)?", text
    ):
        return True
    return bool(
        re.fullmatch(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?[fFlL]?",
            text,
        )
    )


def _cast_numeric_scalar_dtype(value: str) -> str | None:
    match = re.match(r"^\(\s*(u?int(?:8|16|32|64))_t\s*\)", value.strip())
    return normalize_dtype(match.group(1)) if match else None


def _is_integer_scalar_dtype(dtype: str) -> bool:
    return dtype not in {"float", "double", "half"}


def _is_config_token(value: str) -> bool:
    text = value.strip()
    return bool(
        re.fullmatch(r"[A-Z][A-Z0-9_]*", text) or re.fullmatch(r"[+-]?\d+", text)
    )


def _is_affine_int_expression(
    value: str,
    *,
    variable_names: set[str],
    constant_names: set[str] | None = None,
    symbol_expressions: Mapping[str, str] | None = None,
) -> bool:
    try:
        tree = ast.parse(value.strip(), mode="eval")
    except SyntaxError:
        return False

    constant_names = constant_names or set()
    symbol_expressions = symbol_expressions or {}

    def classify(node: ast.AST, resolving: frozenset[str]) -> tuple[bool, bool]:
        if isinstance(node, ast.Expression):
            return classify(node.body, resolving)
        if isinstance(node, ast.Constant):
            valid = isinstance(node.value, int) and not isinstance(node.value, bool)
            return valid, valid
        if isinstance(node, ast.Name):
            if node.id in constant_names:
                return True, True
            if node.id in symbol_expressions:
                if node.id in resolving:
                    return False, False
                try:
                    expression = ast.parse(symbol_expressions[node.id], mode="eval")
                except SyntaxError:
                    return False, False
                return classify(expression, resolving | {node.id})
            return node.id in variable_names, False
        if isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.UAdd, ast.USub)):
                return False, False
            return classify(node.operand, resolving)
        if isinstance(node, ast.BinOp):
            left_valid, left_constant = classify(node.left, resolving)
            right_valid, right_constant = classify(node.right, resolving)
            if not left_valid or not right_valid:
                return False, False
            if isinstance(node.op, (ast.Add, ast.Sub)):
                return True, left_constant and right_constant
            if isinstance(node.op, ast.Mult):
                return (
                    left_constant or right_constant,
                    left_constant and right_constant,
                )
        return False, False

    valid, _ = classify(tree, frozenset())
    return valid


def _declared_identifier(value: str) -> str:
    match = re.match(r"\s*([A-Za-z_]\w*)", value)
    return match.group(1) if match else ""


def _extract_vector_decls(source: str) -> Dict[str, str]:
    register_dtypes: Dict[str, str] = {}
    for stmt in source.split(";"):
        match = _VECTOR_DECL_STMT_RE.fullmatch(stmt)
        if not match:
            continue
        dtype, names_text = match.groups()
        form = _vector_dtype_to_form(dtype)
        for raw_name in _split_args(names_text):
            name = _declared_identifier(raw_name)
            if name:
                register_dtypes[name] = form
    return register_dtypes


def _infer_inst_form(
    op: str,
    dst: Sequence[AdapterValue],
    src: Sequence[AdapterValue],
) -> str | None:
    op = normalize_opcode(op)
    src_dtype = next(
        (
            operand.dtype
            for operand in src
            if operand.dtype and operand.storage != "PredicateRegister"
        ),
        None,
    )
    dst_dtype = next(
        (
            operand.dtype
            for operand in dst
            if operand.dtype and operand.storage != "PredicateRegister"
        ),
        None,
    )
    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    if spec and spec.form_rule == FormRule.FIXED:
        return spec.fixed_form
    if spec and spec.form_rule == FormRule.SOURCE_DTYPE:
        return src_dtype
    if spec and spec.form_rule == FormRule.CONVERSION and src_dtype and dst_dtype:
        src_key = compact_dtype(src_dtype)
        dst_key = compact_dtype(dst_dtype)
        if src_key and dst_key:
            return f"{src_key}_to_{dst_key}"
    return dst_dtype or src_dtype
