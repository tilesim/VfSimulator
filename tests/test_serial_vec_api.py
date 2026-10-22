import pytest

from api.serial_vec_api import LegacyUbInvocation, VfInvocation, predict_serial_vec
from api.program_api import predict_from_program
from api.frontend.schema import StorageKind as S
from core.program_ir import VfSimProgram, VfSimValue, VfSimInst


def vf():
    return VfSimProgram([VfSimInst('VLDS', ['src'], ['x']), VfSimInst('VSTS', ['x'], ['dst'])],
                        values={'src': VfSimValue('src', S.UB, 'fp32', (64,), storage_object_id='shared'),
                                'dst': VfSimValue('dst', S.UB, 'fp32', (64,), storage_object_id='out'),
                                'x': VfSimValue('x', S.REGISTER, 'fp32')})


def test_serial_composition_preserves_both_vf_startup_and_drain():
    p = vf()
    one = predict_from_program(p, dump_results=False)['cycles']
    result = predict_serial_vec([
        LegacyUbInvocation('sort', 'VBS32', 'fp32', 32),
        VfInvocation('copy0', p), VfInvocation('copy1', p),
    ], serialization_evidence='test explicitly serial source')
    assert result['cycles'] == 547 + 2*one
    assert [e['cycles'] for e in result['events']] == [547, one, one]
    assert result['events'][2]['start_cycle'] == result['events'][1]['done_cycle']
    assert p.values['src'].storage_object_id == 'shared'


def test_serial_api_rejects_implicit_scheduling_and_ambiguous_boundaries():
    calls = [LegacyUbInvocation('same', 'VBS32', 'fp32', 1)]*2
    with pytest.raises(ValueError, match='evidence'):
        predict_serial_vec(calls, serialization_evidence='')
    with pytest.raises(ValueError, match='unique'):
        predict_serial_vec(calls, serialization_evidence='serial')
