from pathlib import Path

from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from core.param_db import ParamDB
import pytest
from api.program_adapter import program_to_canonical
from core.program_ir import VfSimProgram, VfSimInst, VfSimValue, VfSimMemoryAccess, StorageKind


def test_point_store_profile_ownership_and_normal_store_cache_are_separate():
    db = ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
    normal = db.resolve_inst('VSTS','fp16','fp16')
    point = db.resolve_memory_mode_inst('VSTS','fp16','fp16','ONEPT_B16')
    assert point.latency == 13 and normal.latency == 9
    assert point.profile_id != normal.profile_id
    assert db.resolve_inst('VSTS','fp16','fp16') is normal
    assert db.resolve_memory_mode_inst('VSTS','fp16','fp16','ONEPT_B16') is point
    assert db.get_forwarding_for_profiles(db.resolve_inst('VADD','fp16','fp16'),point) > 0


def test_onepoint_is_a_single_element_store_with_a_distinct_signature():
    for mode,size in [('ONEPT_B16',2),('ONEPT_B32',4)]:
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup('VSTS').memory_modes[mode]
        assert spec.memory_span == 1 and spec.ub_transfer_bytes == size
        assert next(o for o in spec.operands if o.name=='mode').allowed_values == (mode,)


@pytest.mark.parametrize('dtype,mode',[('fp16','ONEPT_B16'),('fp32','ONEPT_B32')])
def test_point_store_accepts_bool_predicate_and_checks_output_span(dtype,mode):
    def program(span):
        return VfSimProgram([VfSimInst('PSET_B16' if dtype=='fp16' else 'PSET_B32',[],['p']),VfSimInst('VSTS',['v','p'],['out'],form=dtype,
            config={'catalog_mode':mode},memory_accesses=(VfSimMemoryAccess('out','write',span=span),))],
            values={'v':VfSimValue('v',StorageKind.REGISTER,dtype),
                    'p':VfSimValue('p',StorageKind.PREDICATE_REGISTER,'bool'),
                    'out':VfSimValue('out',StorageKind.UB,dtype,(16,1),storage_object_id='out')},dtype=dtype)
    program_to_canonical(program(1))
    with pytest.raises(ValueError,match='catalog_memory_span_mismatch'):
        program_to_canonical(program(2))
