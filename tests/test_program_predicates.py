"""Exercise the TileSim object boundary against the synchronized canonical core."""
import json
from dataclasses import replace

import pytest

from api.program_adapter import program_to_canonical
from api.program_api import predict_from_program
from api.frontend.schema import StorageKind
from api.frontend.serialization import canonical_vf_info_to_dict, canonical_vf_info_from_dict
from core.program_ir import VfSimInst, VfSimValue, VfSimProgram, VfSimLoop, VfSimMemoryAccess


def predicate_program():
    values = {n: VfSimValue(n, StorageKind.PREDICATE_REGISTER, 'bool') for n in ('mask', 'p')}
    values.update({n: VfSimValue(n, StorageKind.REGISTER, 'fp32') for n in ('x', 'y')})
    values['buf'] = VfSimValue('buf', StorageKind.UB, 'fp32', (128,), 'buffer')
    return VfSimProgram(values=values, body=[
        VfSimInst('PSET_B32', [], ['mask']),
        VfSimInst('VLDS', ['buf'], ['x']),
        VfSimInst('VCMP_GT', ['x', 'x', 'mask'], ['p']),
        VfSimInst('VSEL', ['x', 'x', 'p'], ['y']),
        VfSimInst('VSTS', ['y', 'mask'], ['buf']),
    ])


def starts(path):
    return [json.loads(line) for line in (path/'start_by_cycle.json').read_text().splitlines()]


def test_predicate_producer_consumer_reaches_physical_bank(tmp_path):
    p = predicate_program()
    vf = program_to_canonical(p)
    assert vf.schema_version == 2
    assert canonical_vf_info_from_dict(canonical_vf_info_to_dict(vf)) == vf
    assert predict_from_program(p, out_dir=tmp_path)['cycles'] > 0
    by_op = {r['op']: r for r in starts(tmp_path)}
    assert by_op['PSET_B32']['preg_dst'][0].startswith('pred')
    assert by_op['VCMP_GT']['preg_src'][-1] == by_op['PSET_B32']['preg_dst'][0]
    assert by_op['VSEL']['preg_src'][-1] == by_op['VCMP_GT']['preg_dst'][0]
    assert by_op['VSEL']['cy'] > by_op['VCMP_GT']['cy']


def test_predicate_loop_recycles_and_preserves_carried_dependency(tmp_path):
    p = predicate_program()
    p.body[2:4] = [VfSimLoop(12, [
        VfSimInst('PAND', ['mask', 'mask', 'mask'], ['mask']),
        VfSimInst('VCMP_GT', ['x', 'x', 'mask'], ['p']),
        VfSimInst('VSEL', ['x', 'x', 'p'], ['y']),
    ], name='i')]
    p.uarch = {'physical_predicate_registers': 3}
    predict_from_program(p, out_dir=tmp_path)
    rows = [r for r in starts(tmp_path) if r['op'] == 'PAND']
    assert len(rows) == 12
    assert len({r['preg_dst'][0] for r in rows}) <= 3
    for previous, current in zip(rows, rows[1:]):
        assert current['preg_src'][0] == previous['preg_dst'][0]


@pytest.mark.parametrize('invalid', ['missing_mask', 'live_in', 'wrong_storage'])
def test_strict_predicate_information_is_not_invented(invalid):
    p = predicate_program()
    if invalid == 'missing_mask':
        p.body[2].src.pop()
    elif invalid == 'live_in':
        p.body.pop(0)
    else:
        p.values['mask'] = replace(p.values['mask'], storage=StorageKind.REGISTER)
    with pytest.raises(ValueError):
        program_to_canonical(p)


def test_legacy_comparison_requires_migration():
    p = predicate_program()
    for name in ('mask', 'p'):
        p.values[name] = replace(p.values[name], storage=StorageKind.REGISTER)
    p.body = [p.body[2]]
    with pytest.raises(ValueError, match='PredicateRegister'):
        program_to_canonical(p)


@pytest.mark.parametrize('op', ['VINTLV', 'VDINTLV'])
def test_multi_result_program_and_load_mode(tmp_path, op):
    p = predicate_program()
    p.body[1:4] = [
        VfSimInst('VLDS', ['buf'], ['x', 'y'], config={'catalog_mode': 'DINTLV_B32'}),
        VfSimInst(op, ['x', 'y'], ['x', 'y']),
    ]
    canonical = program_to_canonical(p)
    load, pair = canonical.context[1:3]
    assert load.opcode == 'VLDS'
    assert load.inputs[0].memory_access.span == 128
    assert [v.value_id for v in pair.inputs] == [v.value_id for v in load.outputs]
    predict_from_program(p, out_dir=tmp_path)
    load_row = next(r for r in starts(tmp_path) if r['op'] == 'VLDS')
    assert len(set(load_row['preg_dst'])) == 2
    p.body[1].dst = ['x', 'x']
    with pytest.raises(ValueError, match='distinct'):
        program_to_canonical(p)


def test_mlir_opcode_is_not_rewritten():
    p = predicate_program()
    p.body[1].op = 'VLDSX2'
    with pytest.raises(ValueError, match='lowered by the caller'):
        program_to_canonical(p)


def test_post_update_metadata_and_dump_results_switch(tmp_path):
    p = predicate_program()
    p.body[1].memory_accesses = (VfSimMemoryAccess(
        'buf', 'read', address_state_id='ptr', update_mode='post_update',
        post_update_delta_bytes=256,
    ),)
    access = program_to_canonical(p).context[1].inputs[0].memory_access
    assert access.address_state_id == 'ptr'
    assert access.post_update_delta_bytes.constant == 256
    dumped = predict_from_program(p, out_dir=tmp_path/'dumped')
    silent = predict_from_program(p, out_dir=tmp_path/'silent', dump_results=False,
                                  dump_trace_path=tmp_path/'canonical.json')
    assert dumped['cycles'] == silent['cycles']
    assert not (tmp_path/'silent').exists()
    assert (tmp_path/'canonical.json').exists()
    assert (tmp_path/'dumped/idu_address_blocked.json').exists()
    assert (tmp_path/'dumped/idu_resource_blocked.json').exists()


def test_external_predicate_store_forms_and_align_state(tmp_path):
    p = VfSimProgram(values={
        'bits': VfSimValue('bits', StorageKind.REGISTER, 'uint16'),
        'p': VfSimValue('p', StorageKind.PREDICATE_REGISTER, 'bool'),
        'mem': VfSimValue('mem', StorageKind.UB, 'uint32', (256,), 'buffer'),
    }, body=[
        VfSimInst('MOVVP', ['bits'], ['p'], form='B16'),
        VfSimInst('PSTU', ['p'], ['mem'], form='B32',
                  config={'align_state_id': 'align', 'align_state_operation': 'append'},
                  memory_accesses=(VfSimMemoryAccess('mem', 'write', address_state_id='ptr',
                       update_mode='post_update', post_update_delta_bytes=8),)),
        VfSimInst('VSTAS', [], ['mem'], form='B32',
                  config={'align_state_id': 'align', 'align_state_operation': 'consume'},
                  memory_accesses=(VfSimMemoryAccess('mem', 'write', address_state_id='ptr',
                       update_mode='post_update', post_update_delta_bytes=0),)),
    ])
    vf = program_to_canonical(p)
    assert vf.context[1].outputs[0].memory_access.span == 2
    predict_from_program(p, out_dir=tmp_path)
    rows = {r['op']: r for r in starts(tmp_path)}
    assert rows['PSTU']['preg_src'] == rows['MOVVP']['preg_dst']
    assert rows['VSTAS']['cy'] == rows['PSTU']['cy'] + 1
    p.body[1].memory_accesses = ()
    with pytest.raises(ValueError, match='implicit_update'):
        program_to_canonical(p)


def test_config_json_cache_reuses_reads_and_invalidates_changed_file(tmp_path, monkeypatch):
    import core.param_db as db
    path = tmp_path/'config.json'
    path.write_text('{"value":1}')
    read = db._read_json
    calls = []
    def counting_read(p):
        calls.append(p)
        return read(p)
    monkeypatch.setattr(db, '_read_json', counting_read)
    db.clear_param_json_cache()
    assert db._read_json_cached(str(path)) == {'value': 1}
    assert db._read_json_cached(str(path)) == {'value': 1}
    assert len(calls) == 1
    path.write_text('{"value":100}')
    assert db._read_json_cached(str(path)) == {'value': 100}
    assert len(calls) == 2
    db.clear_param_json_cache()
