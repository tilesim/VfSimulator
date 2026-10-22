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
