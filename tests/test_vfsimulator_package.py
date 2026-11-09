import json
import pytest
import subprocess
import sys
from pathlib import Path

from api.json.serialization import canonical_vf_info_to_dict
from api.tilesim.adapter import program_to_canonical
from tests.test_program_api import program
from tests.test_program_predicates import predicate_program
import vfsimulator as packaged
from vfsimulator.api.json.serialization import canonical_vf_info_to_dict as package_dict


def test_package_sources_are_synchronized():
    subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1]/'tools/sync_python_package.py'), '--check'], check=True)


def test_package_contains_sub_exu_rules_and_timing_parameters():
    from vfsimulator.core.params.database import ParamDB
    db = ParamDB()
    assert db.resolve_inst('VADD', 'fp32').execution_resource == 'ADD_DUAL'
    assert db.resolve_inst('VEXP', 'fp32').execution_resource == 'LNEXP'
    assert db.get_forwarding_cycles('VADD', 'VSTS', 'fp32') == 5
    assert db.get_forwarding_cycles('VEXP', 'VADD', 'fp32') == 13
    assert db.get_ii('VEXP', 'VDIV', 'fp32') == 6
    assert db.resource_timing['forwarding']['compute_store_offset'] == 2


@pytest.mark.parametrize('feedback', [False, True])
def test_loop_invariant_motion_preserves_program_feedback_in_package(tmp_path, feedback):
    from api.tilesim import program as root_api
    from api.tilesim.predict import predict_from_program

    def make(api):
        values = {
            'mask': api.VfSimValue('mask', 'PredicateRegister', 'bool'),
            'count': api.VfSimValue('count', 'Scalar', 'uint32'),
            'one': api.VfSimValue('one', 'Scalar', 'fp32'),
            'x': api.VfSimValue('x', 'Register', 'fp32'),
            'out': api.VfSimValue('out', 'UB', 'fp32', (256,), 'buffer'),
        }
        mask = (api.VfSimInst('PLT', ['count'], ['mask', 'count'], 'b32')
                if feedback else api.VfSimInst('PSET_B32', [], ['mask'], 'b32'))
        body = [mask, api.VfSimInst('VDUP', ['one','mask'], ['x']),
                api.VfSimInst('VSTS', ['x','mask'], ['out'])]
        return api.VfSimProgram(values=values, body=[api.VfSimLoop(4, body, name='repeat')])

    root = predict_from_program(make(root_api), out_dir=tmp_path/'root')
    result = packaged.predict_from_program(make(packaged), out_dir=tmp_path/'package')
    assert result['cycles'] == root['cycles']
    assert result['raw']['compiler_optimization_stats'] == root['raw']['compiler_optimization_stats']
    assert result['raw']['compiler_optimization_stats']['hoisted_instruction_count'] == (0 if feedback else 2)
    events = [json.loads(line) for line in (tmp_path/'package/start_by_cycle.json').read_text().splitlines()]
    assert sum(event['op'] == 'VDUPS' for event in events) == (4 if feedback else 1)
    assert sum(event['op'] == ('PLT' if feedback else 'PSET_B32') for event in events) == (4 if feedback else 1)


@pytest.mark.parametrize('width', [16, 32])
def test_movp_program_uses_packaged_predicate_semantics_and_parameters(tmp_path, width):
    from api.common.schema import StorageKind
    from api.tilesim.predict import predict_from_program
    from api.tilesim.program import VfSimInst, VfSimValue, VfSimProgram
    dtype = 'fp16' if width == 16 else 'fp32'
    values = {
        'mask': VfSimValue('mask', StorageKind.PREDICATE_REGISTER, 'bool'),
        'p': VfSimValue('p', StorageKind.PREDICATE_REGISTER, 'bool'),
        'one': VfSimValue('one', StorageKind.SCALAR, dtype),
        'x': VfSimValue('x', StorageKind.REGISTER, dtype),
        'out': VfSimValue('out', StorageKind.UB, dtype, (256,), 'buffer'),
    }
    body = [VfSimInst(f'PSET_B{width}', [], ['mask']),
            VfSimInst(f'MOVP_B{width}', [], ['p']),
            VfSimInst('VDUP', ['one', 'p'], ['x']),
            VfSimInst('VSTS', ['x', 'mask'], ['out'])]
    root = VfSimProgram(values=values, body=body, dtype=dtype)
    program = packaged.VfSimProgram(
        values={k: packaged.VfSimValue(v.value_id, v.storage.value, v.dtype, v.shape, v.storage_object_id)
                for k, v in values.items()},
        body=[packaged.VfSimInst(n.op, n.src, n.dst) for n in body], dtype=dtype)
    canonical = packaged.program_to_canonical(program)
    move = canonical.context[1]
    assert move.form == f'b{width}'
    assert move.inputs == ()
    assert canonical.values[move.outputs[0].value_id].storage.value == 'PredicateRegister'
    assert package_dict(canonical) == canonical_vf_info_to_dict(program_to_canonical(root))
    assert packaged.predict_from_program(program, out_dir=tmp_path/'package')['cycles'] == \
        predict_from_program(root, out_dir=tmp_path/'root')['cycles']
    events = [json.loads(line)
              for line in (tmp_path/'package/start_by_cycle.json').read_text().splitlines()]
    by_op = {event['op']: event for event in events}
    assert by_op[f'MOVP_B{width}']['preg_dst'][0].startswith('pred')
    assert by_op[f'MOVP_B{width}']['preg_dst'][0] in by_op['VDUPS']['preg_src']


@pytest.mark.parametrize('factory', [program, predicate_program])
def test_packaged_api_matches_root(tmp_path, factory):
    root = factory()
    def convert(n):
        if hasattr(n, 'body'):
            return packaged.VfSimLoop(n.count, [convert(x) for x in n.body], n.name, n.unroll)
        if hasattr(n, 'barrier'):
            return packaged.VfSimMembar(n.barrier)
        return packaged.VfSimInst(n.op, n.src, n.dst, n.form, n.config)
    p = packaged.VfSimProgram(body=[convert(x) for x in root.body], params=root.params,
        values={k: packaged.VfSimValue(v.value_id, v.storage.value, v.dtype, v.shape, v.storage_object_id, v.offsets) for k,v in root.values.items()})
    assert package_dict(packaged.program_to_canonical(p)) == canonical_vf_info_to_dict(program_to_canonical(root))
    from api.tilesim.predict import predict_from_program
    assert packaged.predict_from_program(p, out_dir=tmp_path/'package')['cycles'] == predict_from_program(root, out_dir=tmp_path/'root')['cycles']
