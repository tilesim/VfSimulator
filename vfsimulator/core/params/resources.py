"""Execution-resource and producer/consumer timing rules."""
from __future__ import annotations

from vfsimulator.api.common.instruction_catalog import ArgumentKind, DEFAULT_INSTRUCTION_CATALOG
from .fallback import compatible_form


def instruction_banks(op):
    spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
    if spec is None:
        return frozenset()
    banks = {ArgumentKind.REGISTER: 'vector', ArgumentKind.PREDICATE: 'predicate'}
    return frozenset(banks[item.kind] for item in spec.output_operands if item.kind in banks)


def forwarding_interval(rules, producer_group, consumer_group, latency):
    if not producer_group or not consumer_group:
        return None
    policy = rules['forwarding']
    offset = policy['pair_offsets'].get(producer_group, {}).get(consumer_group, policy['default_offset'])
    return max(policy['minimum'], latency-offset)


def compute_store_forwarding(rules, producer, consumer):
    if producer.op_class == 'COMPUTE' and consumer.op_class == 'STORE':
        return max(0, producer.latency - rules['forwarding']['compute_store_offset'])
    return None


def issue_interval(rules, previous, current):
    if not previous.execution_resource or not current.execution_resource:
        return None
    policy = rules['ii']
    gap = (previous.self_ii if previous.execution_resource == current.execution_resource
           else policy['different_resource_interval'])
    if gap is None:
        return None
    if (previous.writeback_banks & current.writeback_banks and
            previous.latency == gap + current.latency):
        gap += policy['writeback_collision_penalty']
    return gap


def validate_resources(rules, overrides, isa):
    if rules.get('schema_version') != 1 or type(rules['schema_version']) is not int:
        raise ValueError('execution_resources requires schema_version=1')
    groups = rules.get('groups')
    if (not isinstance(groups, list) or any(not isinstance(g, str) or not g or g != g.upper() for g in groups)
            or len(set(groups)) != len(groups)):
        raise ValueError('execution_resources.groups must contain unique uppercase resource names')
    forwarding = rules.get('forwarding')
    if not isinstance(forwarding, dict):
        raise ValueError('execution_resources.forwarding must be an object')
    for key in ('minimum', 'default_offset', 'compute_store_offset', 'fallback_offset', 'load_store_interval'):
        if type(forwarding.get(key)) is not int or forwarding[key] < 0:
            raise ValueError(f'execution_resources.forwarding.{key} must be a nonnegative integer')
    offsets = forwarding.get('pair_offsets')
    if not isinstance(offsets, dict):
        raise ValueError('execution_resources.forwarding.pair_offsets must be an object')
    for a, row in offsets.items():
        if a not in groups or not isinstance(row, dict):
            raise ValueError(f'Unknown forwarding resource: {a}')
        for b, value in row.items():
            if b not in groups or type(value) is not int or value < 0:
                raise ValueError(f'Invalid forwarding resource offset: {a}->{b}')
    ii = rules.get('ii')
    if not isinstance(ii, dict):
        raise ValueError('execution_resources.ii must be an object')
    for key, minimum in (('different_resource_interval', 1), ('writeback_collision_penalty', 0)):
        if type(ii.get(key)) is not int or ii[key] < minimum:
            raise ValueError(f'execution_resources.ii.{key} must be an integer >= {minimum}')
    instructions = isa.get('instructions', {})
    forms = {}
    for op, node in instructions.items():
        op_forms = node.get('forms', {})
        for form, params in op_forms.items():
            effective = {**isa.get('defaults', {}), **node,
                         **op_forms.get(compatible_form(form), {}), **params}
            resource = effective.get('execution_resource', '')
            if not isinstance(resource, str):
                raise ValueError(f'ISA {op}.{form}.execution_resource must be a string')
            if resource and (resource not in groups or effective.get('op_class', '') != 'COMPUTE'):
                raise ValueError(f'Unknown or non-compute execution resource for {op}.{form}: {resource}')
            forms[f'{op}.{form}'] = effective
    if not overrides:
        overrides.update(schema_version=1, ii={}, forwarding={})
    if overrides.get('schema_version') != 1 or type(overrides['schema_version']) is not int:
        raise ValueError('timing_overrides requires schema_version=1')
    for kind, minimum in (('ii', 1), ('forwarding', 0)):
        table = overrides.get(kind)
        if not isinstance(table, dict):
            raise ValueError(f'timing_overrides.{kind} must be an object')
        for a, row in table.items():
            if a not in forms or not isinstance(row, dict):
                raise ValueError(f'Unknown timing override producer: {a}')
            for b, value in row.items():
                if b not in forms or type(value) is not int or value < minimum:
                    raise ValueError(f'Invalid timing override: {a}->{b}')
                if kind == 'ii' and (a == b or any(forms[key].get('op_class', '') != 'COMPUTE' for key in (a,b))):
                    raise ValueError(f'II overrides require distinct compute forms: {a}->{b}')
