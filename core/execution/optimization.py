"""Conservative loop-invariant motion on the shared Canonical representation."""
from __future__ import annotations

from dataclasses import replace
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.common.schema import CanonicalInstruction, CanonicalLoop, InstructionClass, StorageKind


def hoist_loop_invariants(vf_info):
    """Move proven invariant, explicitly pure instructions to loop preheaders.

    Scalar expressions describe dependencies only; they do not become timed
    instructions. Unknown effects and zero/unknown trip counts preserve order.
    """
    enabled = vf_info.source.get('compiler_optimized') is not True
    stats = dict(enabled=enabled, hoisted_instruction_count=0, moves=[])
    if not enabled:
        return vf_info, stats
    values = vf_info.values

    def resolve_count(value):
        if isinstance(value, str):
            value = vf_info.params.get(value, value)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def descendants(nodes):
        result = set()
        for node in nodes:
            if isinstance(node, CanonicalLoop):
                result.add(node.loop_id)
                result.update(descendants(node.body))
            else:
                result.add(node.instruction_id)
        return result

    def visit(nodes, parent=None):
        output = []
        for node in nodes:
            if not isinstance(node, CanonicalLoop):
                output.append(node)
                continue
            count = resolve_count(node.count)
            if count is None or count <= 0:
                output.append(node)
                continue
            loop = replace(node, body=visit(node.body, node.loop_id))
            inside = descendants(loop.body) | {loop.loop_id}
            entries = {binding.entry_value_id for binding in loop.carried_values
                       if binding.entry_value_id != binding.back_edge_value_id}
            # Older Canonical inputs may omit scalar feedback metadata. Do not
            # infer invariance for an opaque scalar written in the loop.
            scalar_writes = set()
            def collect_writes(children):
                for child in children:
                    if isinstance(child, CanonicalLoop):
                        collect_writes(child.body)
                    elif isinstance(child, CanonicalInstruction):
                        scalar_writes.update(values[o.value_id].logical_id for o in child.outputs
                                             if values[o.value_id].storage == StorageKind.SCALAR)
            collect_writes(loop.body)
            moved = set()

            def invariant(identity, visiting=None):
                visiting = set() if visiting is None else visiting
                if identity in entries or identity in visiting:
                    return False
                value = values[identity]
                if value.storage == StorageKind.UB:
                    return False
                if value.producer_node_id in inside:
                    return value.producer_node_id in moved
                if value.storage == StorageKind.SCALAR:
                    if set(value.scalar_loop_dependencies) & inside:
                        return False
                    if value.scalar_expression is None and value.logical_id in scalar_writes:
                        return False
                    return all(invariant(source, visiting | {identity}) for source in value.scalar_dependencies)
                return True

            remaining, hoisted = list(loop.body), []
            while True:
                changed = False
                retained = []
                for instruction in remaining:
                    spec = (DEFAULT_INSTRUCTION_CATALOG.lookup(instruction.opcode)
                            if isinstance(instruction, CanonicalInstruction) else None)
                    if (spec is not None and spec.pure
                            and instruction.instruction_class == InstructionClass.COMPUTE
                            and instruction.outputs and not instruction.attributes.get('volatile')
                            and all(o.memory_access is None and invariant(o.value_id) for o in instruction.inputs)
                            and all(o.memory_access is None and values[o.value_id].storage != StorageKind.UB
                                    for o in instruction.outputs)):
                        moved.add(instruction.instruction_id)
                        hoisted.append(instruction)
                        stats['moves'].append(dict(instruction_id=instruction.instruction_id,
                            opcode=instruction.opcode, from_loop=loop.loop_id, to_loop=parent))
                        changed = True
                    else:
                        retained.append(instruction)
                remaining = retained
                if not changed:
                    break
            output.extend(hoisted)
            output.append(replace(loop, body=tuple(remaining)))
        return tuple(output)

    result = replace(vf_info, context=visit(vf_info.context))
    stats['hoisted_instruction_count'] = len({move['instruction_id'] for move in stats['moves']})
    return result, stats
