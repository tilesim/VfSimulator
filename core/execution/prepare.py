from __future__ import annotations
from dataclasses import dataclass, fields
from typing import Any, Dict, List
from core.common.instruction import DynamicInstruction
from core.execution.expansion import DynamicExpander
from core.params.types import UarchParameters
from typing import Any, Iterable
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.common.schema import AffineExpression, CanonicalInstruction, CanonicalLoop, CanonicalMembar, CanonicalNode, CanonicalOperand, CanonicalVfInfo, InstructionClass, SourceLocation, StorageKind

class CanonicalCoreCompatibilityError(ValueError):

    def __init__(self, issues: Iterable[str]) -> None:
        self.issues = tuple(issues)
        super().__init__(f"CanonicalVfInfo uses semantics not yet supported by the current Python Core: {'; '.join(self.issues)}")

def _source_location(location: SourceLocation | None) -> dict[str, Any] | None:
    if location is None:
        return None
    return {'source': location.source, 'line': location.line, 'column': location.column, 'path': location.path}

def _affine_expression(expression: AffineExpression) -> dict[str, Any]:
    return {
        'constant': expression.constant,
        'terms': [{'variable_id': term.variable_id, 'coefficient': term.coefficient} for term in expression.terms]
    }

class CoreLoweringPass:
    """Convert API-validated Canonical input to the current Python Core payload."""

    def lower(self, vf_info: CanonicalVfInfo) -> dict[str, Any]:
        self.ensure_current_core_compatible(vf_info)
        ub_core_ids = {}
        occupied_ids = set(vf_info.values)
        for object_id in vf_info.storage_objects:
            candidate = f'__canonical_ub__{object_id}'
            while candidate in occupied_ids:
                candidate += ':ub'
            ub_core_ids[object_id] = candidate
            occupied_ids.add(candidate)
        values: dict[str, dict[str, Any]] = {
            definition_id: {
                'value_id': definition_id,
                'logical_id': value.logical_id,
                'storage': value.storage.value,
                'dtype': value.dtype,
                'shape': list(value.shape),
                'producer_node_id': value.producer_node_id,
                'storage_object_id': value.storage_object_id
            } for definition_id,
            value in vf_info.values.items() if value.storage != StorageKind.UB
        }
        for object_id, storage_object in vf_info.storage_objects.items():
            dtype = next(
                (value.dtype for value in vf_info.values.values() if value.storage_object_id == object_id),
                None
            )
            core_id = ub_core_ids[object_id]
            values[core_id] = {
                'value_id': core_id,
                'logical_id': object_id,
                'storage': StorageKind.UB.value,
                'dtype': dtype,
                'shape': list(storage_object.shape),
                'storage_object_id': object_id
            }
        return {
            'fallback_dtype': 'fp32',
            'params': dict(vf_info.params),
            'uarch': dict(vf_info.uarch),
            'values': values,
            'program': self._lower_nodes(vf_info.context, ub_core_ids),
            'canonical_ub_map': dict(ub_core_ids)
        }

    def ensure_current_core_compatible(self, vf_info: CanonicalVfInfo) -> None:
        issues: list[str] = []

        def resolve(value: int | str) -> int | None:
            if isinstance(value, int) and (not isinstance(value, bool)):
                return value
            if isinstance(value, str):
                if value in vf_info.params:
                    return int(vf_info.params[value])
                try:
                    return int(value, 10)
                except ValueError:
                    return None
            return None

        def visit(nodes: Iterable[CanonicalNode]) -> None:
            for node in nodes:
                if isinstance(node, CanonicalInstruction):
                    if node.dependencies:
                        issues.append(f'instruction {node.instruction_id} has explicit dependencies')
                    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(node.opcode)
                    if spec is None and node.instruction_class != InstructionClass.COMPUTE:
                        issues.append(f'unknown {node.instruction_class.value} instruction {node.instruction_id} cannot use Core ISA classification')
                    if node.instruction_class == InstructionClass.CONTROL:
                        issues.append(f'control instruction {node.instruction_id} is not a Membar node')
                    continue
                if isinstance(node, CanonicalMembar):
                    if node.dependencies:
                        issues.append(f'Membar {node.instruction_id} has explicit dependencies')
                    continue
                unroll = resolve(node.unroll)
                count = resolve(node.count)
                has_nested_loop = any((isinstance(child, CanonicalLoop) for child in node.body))
                if unroll is not None and unroll > 1 and has_nested_loop:
                    issues.append(f'non-innermost loop {node.loop_id} cannot use unroll={node.unroll}')
                if unroll is not None and unroll > 1 and (count is not None) and (count % unroll != 0):
                    issues.append(f'loop {node.loop_id} count={node.count} is not divisible by unroll={node.unroll}')
                visit(node.body)
        visit(vf_info.context)
        if issues:
            raise CanonicalCoreCompatibilityError(issues)

    def _lower_nodes(self, nodes: Iterable[CanonicalNode], ub_core_ids: dict[str, str]) -> list[dict[str, Any]]:
        return [self._lower_node(node, ub_core_ids) for node in nodes]

    def _lower_node(self, node: CanonicalNode, ub_core_ids: dict[str, str]) -> dict[str, Any]:
        if isinstance(node, CanonicalInstruction):
            return {
                'type': 'inst',
                'op': node.opcode,
                'form': node.form,
                'src': [self._lower_operand(item, ub_core_ids) for item in node.inputs],
                'dst': [self._lower_operand(item, ub_core_ids) for item in node.outputs],
                'static_instruction_id': node.instruction_id,
                'instruction_class': node.instruction_class.value,
                'attributes': dict(node.attributes),
                'dependencies': [{
                    'producer_node_id': item.producer_node_id,
                    'kind': item.kind.value,
                    'operand_index': item.operand_index
                } for item in node.dependencies],
                'memory_accesses': self._memory_accesses(node),
                'source_location': _source_location(node.source_location)
            }
        if isinstance(node, CanonicalMembar):
            return {
                'type': 'membar',
                'barrier': node.barrier,
                'static_instruction_id': node.instruction_id,
                'source_location': _source_location(node.source_location)
            }
        return {
            'type': 'loop',
            'name': node.loop_id,
            'iters': node.count,
            'unroll': node.unroll,
            'induction': {
                'variable_id': node.induction.variable_id,
                'start': node.induction.start,
                'step': node.induction.step
            },
            'carried_values': [{
                'logical_id': item.logical_id,
                'entry_value_id': item.entry_value_id,
                'back_edge_value_id': item.back_edge_value_id,
                'exit_value_id': item.exit_value_id
            } for item in node.carried_values],
            'body': self._lower_nodes(node.body, ub_core_ids),
            'source_location': _source_location(node.source_location)
        }

    @staticmethod
    def _lower_operand(operand: CanonicalOperand, ub_core_ids: dict[str, str]) -> str:
        if operand.memory_access is not None:
            return ub_core_ids[operand.memory_access.base_object_id]
        return operand.value_id

    @staticmethod
    def _memory_accesses(node: CanonicalInstruction) -> list[dict[str, Any]]:
        accesses: list[dict[str, Any]] = []
        for direction, operands in (('input', node.inputs), ('output', node.outputs)):
            for index, operand in enumerate(operands):
                memory = operand.memory_access
                if memory is None:
                    continue
                accesses.append({
                    'direction': direction,
                    'operand_index': index,
                    'value_id': operand.value_id,
                    'base_object_id': memory.base_object_id,
                    'offset': _affine_expression(memory.offset),
                    'access_kind': memory.access_kind.value,
                    'span': memory.span,
                    'alias_group': memory.alias_group,
                    'address_state_id': memory.address_state_id,
                    'update_mode': memory.update_mode,
                    'post_update_delta_bytes': _affine_expression(memory.post_update_delta_bytes) if memory.post_update_delta_bytes is not None else None
                })
        return accesses
__all__ = ['CanonicalCoreCompatibilityError', 'CoreLoweringPass']

def resolve_value(v, params, default=None):
    """
    Resolve v which can be:
      - int/float -> return as-is
      - string -> if in params, return params[string], else return string
      - None -> default
    """
    if v is None:
        return default
    if isinstance(v, (int, float)) and (not isinstance(v, bool)):
        return v
    if isinstance(v, str):
        if v in params:
            return params[v]
        return v
    return v

def contains_any_loop(node):
    """
    Return True if node (list/dict) contains any loop node at any depth.
    Used to decide whether a loop is innermost.
    """
    if isinstance(node, list):
        for x in node:
            if contains_any_loop(x):
                return True
        return False
    if isinstance(node, dict):
        if node.get('type') == 'loop':
            return True
        for k in ('body', 'program', 'nodes', 'then', 'else'):
            if k in node:
                if contains_any_loop(node[k]):
                    return True
        return False
    return False

class _StaticLayout:

    def __init__(self, params):
        self.params = params
        self._next_loop_id = 0
        self._pc = 0
        self.linear = []

    def flatten(self, program):
        self.linear = []
        self._next_loop_id = 0
        self._pc = 0
        self._visit(program, depth=0, loop_stack=[])
        return self.linear

    def _visit(self, node, depth, loop_stack):
        if isinstance(node, list):
            for x in node:
                self._visit(x, depth=depth, loop_stack=loop_stack)
            return
        if not isinstance(node, dict):
            raise ValueError('Invalid node type')
        ntype = node.get('type')
        if ntype == 'inst':
            self._emit_inst(node, depth, loop_stack)
            return
        if ntype == 'membar':
            self._emit_membar(node, depth, loop_stack)
            return
        if ntype == 'loop':
            self._emit_loop(node, depth, loop_stack)
            return
        raise ValueError('Unknown node type')

    def _emit_inst(self, inst, depth, loop_stack):
        out = dict(inst)
        out['type'] = 'inst'
        out['pc'] = self._pc
        out['depth'] = depth
        out['loop_stack'] = list(loop_stack)
        out.setdefault('dst', [])
        out.setdefault('src', [])
        self.linear.append(out)
        self._pc += 1

    def _emit_membar(self, membar, depth, loop_stack):
        out = dict(membar)
        out['type'] = 'membar'
        out['pc'] = self._pc
        out['depth'] = depth
        out['loop_stack'] = list(loop_stack)
        self.linear.append(out)
        self._pc += 1

    def _emit_loop(self, loop_node, depth, loop_stack):
        loop_id = self._next_loop_id
        self._next_loop_id += 1
        iters_raw = loop_node.get('iters')
        iters = resolve_value(iters_raw, self.params, default=1)
        unroll_raw = loop_node.get('unroll', 1)
        unroll = resolve_value(unroll_raw, self.params, default=1)
        if isinstance(unroll, float) and unroll.is_integer():
            unroll = int(unroll)
        if not isinstance(unroll, str):
            if not isinstance(unroll, int) or unroll < 1:
                raise ValueError(f'Invalid unroll={unroll} for loop {loop_node}. Must be int >= 1')
        name = loop_node.get('name', f'loop_{loop_id}')
        body = loop_node.get('body')
        if body is None:
            raise ValueError('Loop missing body')
        is_innermost = not contains_any_loop(body)
        if not is_innermost:
            if isinstance(unroll, int) and unroll != 1 or (isinstance(unroll, str) and unroll_raw != 1):
                raise ValueError(f"Only innermost loop can unroll. Loop '{name}' is not innermost but has unroll={unroll_raw}.")
            unroll = 1
        begin = {
            'type': 'loop_begin',
            'op': 'VLOOPv2',
            'pc': self._pc,
            'depth': depth + 1,
            'loop_id': loop_id,
            'iters': iters,
            'iters_raw': iters_raw,
            'unroll': unroll,
            'unroll_raw': unroll_raw,
            'name': name,
            'is_innermost': is_innermost,
            'loop_stack': list(loop_stack),
            'induction': dict(loop_node.get('induction', {})),
            'carried_values': [dict(item) for item in loop_node.get('carried_values', [])]
        }
        self.linear.append(begin)
        self._pc += 1
        new_stack = list(loop_stack) + [loop_id]
        self._visit(body, depth + 1, new_stack)
        end = {
            'type': 'loop_end',
            'op': 'VLOOPv2',
            'pc': self._pc,
            'depth': depth + 1,
            'loop_id': loop_id,
            'name': name,
            'loop_stack': list(loop_stack)
        }
        self.linear.append(end)
        self._pc += 1

class _ProgramAnalysis:

    def __init__(self, params: Dict[str, Any]) -> None:
        self.params = params

    def resolve_bound(self, bound: Any) -> int:
        if isinstance(bound, int):
            return bound
        if isinstance(bound, str):
            if bound.isdigit():
                return int(bound)
            if bound in self.params:
                return int(self.params[bound])
        raise ValueError(f'Unsupported loop bound: {bound}')

    def infer_nested_bounds_from_loop(self, loop_node: Dict[str, Any]) -> List[int]:
        bounds: List[int] = []
        node = loop_node
        while isinstance(node, dict) and node.get('type') == 'loop' and (len(bounds) < 3):
            bounds.append(self.resolve_bound(node.get('iters')))
            body = node.get('body', [])
            next_loop = None
            if isinstance(body, list):
                for item in body:
                    if isinstance(item, dict) and item.get('type') == 'loop':
                        next_loop = item
                        break
            if next_loop is None:
                break
            node = next_loop
        return bounds

    def infer_top_block_loop_bounds(self, program: List[Dict[str, Any]]) -> Dict[int, List[int]]:
        result: Dict[int, List[int]] = {}
        tbid = 0
        if not isinstance(program, list):
            return {0: []}
        for node in program:
            if isinstance(node, dict) and node.get('type') == 'loop':
                result[tbid] = self.infer_nested_bounds_from_loop(node)
                tbid += 1
        if not result:
            result[0] = []
        return result

@dataclass(frozen=True)
class PreparedProgram:
    instructions: tuple[DynamicInstruction, ...]
    values: dict
    params: dict
    top_block_loop_bounds: dict
    empty_top_blocks: set[int]
    fallback_dtype: str
    linear_inst_count: int

def prepare_program(vf_info, db, uarch: UarchParameters):
    payload = CoreLoweringPass().lower(vf_info)
    params = payload['params']
    linear = _StaticLayout(params).flatten(payload['program'])
    expander = DynamicExpander(
        linear,
        params,
        pdb=db,
        structured_dynamic_instruction_limit=uarch.canonical_dynamic_instruction_limit
    )
    expander.prepare_structured_stream()
    keys = {f.name for f in fields(DynamicInstruction)}
    instructions = tuple((DynamicInstruction(**{k: v for k, v in node.items() if k in keys}) for node in expander._structured_stream))
    bounds = _ProgramAnalysis(params).infer_top_block_loop_bounds(payload['program'])
    return PreparedProgram(
        instructions,
        payload['values'],
        params,
        bounds,
        expander.empty_top_block_ids(),
        payload['fallback_dtype'],
        len(linear)
    )
