import pytest
from api.program_adapter import program_to_canonical
from api.frontend.schema import StorageKind
from core.program_ir import VfSimInst,VfSimProgram,VfSimValue


def program(dtype='int32',attribute=True):
    values={name:VfSimValue(name,StorageKind.REGISTER,dt) for name,dt in (
        ('x','fp32'),('zero','fp32'),('value','fp32'),('index',dtype))}
    return VfSimProgram(values=values,dtype='fp32',body=[VfSimInst('VDINTLV',['x','zero'],['value','index'],
        form='fp32',config={'bit_reinterpretation':'b32'} if attribute else {})])


def test_explicit_b32_shuffle_retains_float_value_and_integer_index_types():
    p=program_to_canonical(program())
    assert [v.dtype for v in p.context[0].outputs]==['fp32','int32']
    assert len(p.context)==1


@pytest.mark.parametrize('dtype',['int16','fp16','int64'])
def test_shuffle_cannot_reinterpret_a_different_register_width(dtype):
    with pytest.raises(ValueError,match='matching dtypes'):program_to_canonical(program(dtype))


def test_mixed_shuffle_requires_explicit_source_bitcast_contract():
    with pytest.raises(ValueError,match='matching dtypes'):program_to_canonical(program(attribute=False))
