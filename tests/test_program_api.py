from dataclasses import replace

import pytest

from api.program_adapter import program_to_canonical
from api.program_api import predict_from_program
from api.simulator_costmodel import CoreVfCostModel
from api.frontend.schema import StorageKind, CanonicalLoop, CanonicalMembar
from core.program_ir import VfSimValue, VfSimProgram, VfSimLoop, VfSimInst, VfSimMembar


def program(dtype='fp32', barrier='VST_VLD'):
    values = {
        'V_memory': VfSimValue('V_memory', StorageKind.UB, dtype, (128,), 'shared'),
        'view': VfSimValue('view', StorageKind.UB, dtype, (128,), 'shared', (64,)),
        'mem_register': VfSimValue('mem_register', StorageKind.REGISTER, dtype),
        'scalar': VfSimValue('scalar', StorageKind.SCALAR, dtype),
    }
    return VfSimProgram(values=values, dtype=dtype, params={'N': 4}, body=[
        VfSimLoop('N', [
            VfSimInst('VLDS', ['V_memory'], ['mem_register']),
            VfSimInst('VADDS', ['mem_register', 'scalar'], ['mem_register']),
            VfSimInst('VSTS', ['mem_register'], ['view']),
            VfSimMembar(barrier),
        ], name='repeat')])


@pytest.mark.parametrize('dtype', ['fp16', 'fp32'])
@pytest.mark.parametrize('barrier', ['VST_VLD', 'VLD_VST'])
def test_predict_matches_master_canonical_core(tmp_path, dtype, barrier):
    p = program(dtype, barrier)
    canonical = program_to_canonical(p)
    direct = CoreVfCostModel(out_dir=tmp_path/'direct', dtype=dtype).run_vf_info(canonical)
    result = predict_from_program(p, out_dir=tmp_path/'program', dump_trace_path=tmp_path/'canonical.json')
    assert result['cycles'] == direct['vf_end_cycle'] > 0
    assert 'schema_version' in (tmp_path/'canonical.json').read_text()


def test_names_versions_aliases_offsets_and_loop_barrier():
    canonical = program_to_canonical(program())
    assert set(canonical.storage_objects) == {'shared'}
    assert {v.logical_id for v in canonical.values.values()} == {'V_memory', 'view', 'mem_register', 'scalar'}
    loop = canonical.context[0]
    assert isinstance(loop, CanonicalLoop) and loop.carried_values
    assert isinstance(loop.body[-1], CanonicalMembar)
    access = loop.body[2].outputs[0].memory_access
    assert access.base_object_id == 'shared' and access.offset.constant == 64
    assert loop.body[1].inputs[0].value_id != loop.body[1].outputs[0].value_id


def test_predicate_and_mixed_dtype_conversion():
    p = VfSimProgram(values={
        'x': VfSimValue('x', StorageKind.REGISTER, 'fp16'),
        'y': VfSimValue('y', StorageKind.REGISTER, 'fp32'),
        'mask': VfSimValue('mask', StorageKind.REGISTER, 'bool'),
    }, body=[VfSimInst('VCVT_F16_TO_F32', ['x', 'mask'], ['y'], form='f16_to_f32')])
    inst = program_to_canonical(p).context[0]
    assert inst.form == 'f16_to_f32' and len(inst.inputs) == 1
    assert inst.inputs[0].dtype == 'fp16' and inst.outputs[0].dtype == 'fp32'


def test_missing_operands_are_not_guessed():
    with pytest.raises(ValueError, match='explicit metadata'):
        program_to_canonical(VfSimProgram(body=[VfSimInst('VABS', ['V1'], ['V2'])]))
    p = program()
    p.body[0].body[1].op = 'VADD'
    p.body[0].body[1].src.pop()
    with pytest.raises(ValueError, match='expected 2 sources'):
        program_to_canonical(p)


def test_unknown_storage_and_barrier_rejected():
    with pytest.raises(ValueError):
        VfSimValue('x', 'GM', 'fp32')
    with pytest.raises(ValueError):
        VfSimMembar('unknown')


def test_nested_loops_and_empty_program():
    p = program()
    p.body = [VfSimLoop(2, p.body, name='outer')]
    assert isinstance(program_to_canonical(p).context[0].body[0], CanonicalLoop)
    assert program_to_canonical(VfSimProgram([])).context == ()


def test_shared_object_name_cannot_collide_with_master_generated_name():
    p = program()
    p.values['V_memory'] = replace(p.values['V_memory'], storage_object_id='ub.view')
    assert set(program_to_canonical(p).storage_objects) == {'ub.view', 'shared'}



def test_omitted_scalar_is_rejected():
    p = program()
    p.body[0].body[1].src.pop()
    with pytest.raises(ValueError, match='expected 2 sources'):
        program_to_canonical(p)


def test_memory_bitwidth_form_and_storage_shape():
    p = VfSimProgram(values={
        'view': VfSimValue('view', StorageKind.UB, 'bf16', (2, 4), 'allocation', (1, 2), (8, 16)),
        'reg': VfSimValue('reg', StorageKind.REGISTER, 'bf16'),
    }, body=[VfSimInst('VLDS', ['view'], ['reg'])])
    inst = program_to_canonical(p).context[0]
    assert inst.form == 'b16'
    assert inst.inputs[0].memory_access.offset.constant == 18


def test_scalar_broadcast_uses_register_form():
    p = VfSimProgram(values={
        'one': VfSimValue('one', StorageKind.SCALAR, 'int32'),
        'reg': VfSimValue('reg', StorageKind.REGISTER, 'fp32'),
    }, body=[VfSimInst('VDUP', ['one'], ['reg'])])
    assert program_to_canonical(p).context[0].form == 'fp32'


def test_loop_affine_offsets_and_reused_source_loop_names():
    p = VfSimProgram(values={
        'view': VfSimValue('view', StorageKind.UB, 'fp32', (64,), 'allocation', ('i * 64',)),
        'reg': VfSimValue('reg', StorageKind.REGISTER, 'fp32'),
    }, body=[VfSimLoop(2, [VfSimInst('VLDS', ['view'], ['reg'])], name='i'),
             VfSimLoop(2, [VfSimInst('VLDS', ['view'], ['reg'])], name='i')])
    canonical = program_to_canonical(p)
    first, second = canonical.context
    assert first.loop_id != second.loop_id
    term = first.body[0].inputs[0].memory_access.offset.terms[0]
    assert term.variable_id == 'i' and term.coefficient == 64


@pytest.mark.parametrize('dtype', ['fp16', 'fp32'])
@pytest.mark.parametrize('with_predicate', [False, True])
def test_comparison_form_uses_data_inputs_not_bool_result(dtype, with_predicate):
    p = VfSimProgram(values={
        'lhs': VfSimValue('lhs', StorageKind.REGISTER, dtype),
        'rhs': VfSimValue('rhs', StorageKind.REGISTER, dtype),
        'result': VfSimValue('result', StorageKind.REGISTER, 'bool'),
        'predicate': VfSimValue('predicate', StorageKind.REGISTER, 'bool'),
    }, body=[VfSimInst('VCMP_EQ', ['lhs', 'rhs'] + (['predicate'] if with_predicate else []), ['result'])])
    inferred = program_to_canonical(p)
    inst = inferred.context[0]
    assert inst.form == dtype
    assert inst.outputs[0].dtype == 'bool'
    assert len(inst.inputs) == 2
    p.body[0].form = dtype
    assert program_to_canonical(p) == inferred
