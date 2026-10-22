"""Conversion memory operations retain half-register UB payloads."""
import pytest
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.program_adapter import program_to_canonical
from core.program_ir import VfSimInst,VfSimProgram,VfSimValue,VfSimMemoryAccess,StorageKind

@pytest.mark.parametrize('opcode,mode,dtype',[('VLDS','UNPK_B16','fp16'),('VSTS','PK_B32','fp16'),('VSTS','PK_B32','bf16')])
def test_conversion_memory_modes_validate_exact_payload(opcode,mode,dtype):
    spec=DEFAULT_INSTRUCTION_CATALOG.lookup(opcode).memory_modes[mode]
    assert spec.memory_span==64 and spec.ub_transfer_bytes==128
    def program(span):
        load=opcode=='VLDS'
        inst=VfSimInst(opcode,['mem'] if load else ['v','p'],['v'] if load else ['mem'],form=dtype,
            config={'catalog_mode':mode},memory_accesses=(VfSimMemoryAccess('mem','read' if load else 'write',span=span),))
        return VfSimProgram(([VfSimInst('PSET_B32',[],['p'])] if not load else [])+[inst],values={
            'mem':VfSimValue('mem',StorageKind.UB,dtype,(128,),storage_object_id='mem'),
            'v':VfSimValue('v',StorageKind.REGISTER,dtype),
            'p':VfSimValue('p',StorageKind.PREDICATE_REGISTER,'bool')},dtype=dtype)
    program_to_canonical(program(64))
    with pytest.raises(ValueError,match='catalog_memory_span_mismatch'):
        program_to_canonical(program(128))
