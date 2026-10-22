"""Check the public program contract and per-call parameter isolation."""

from importlib import import_module
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from api.common.schema import StorageKind
from api.tilesim import (
    VfSimInst, VfSimMemoryAccess, VfSimProgram, VfSimValue,
    predict_from_program, program_to_canonical,
)
from tests.test_program_api import program


def test_program_crosses_semantic_validation_once():
    from api.common import validator

    with patch.object(validator, "validate_canonical_vf_info",
                      wraps=validator.validate_canonical_vf_info) as validate:
        result = predict_from_program(program(), dump_results=False)
    assert result["cycles"] > 0
    assert validate.call_count == 1


def test_scalar_vdup_selects_the_scalar_broadcast_instruction():
    p = VfSimProgram(values={
        "one": VfSimValue("one", StorageKind.SCALAR, "int32"),
        "value": VfSimValue("value", StorageKind.REGISTER, "fp32"),
    }, body=[VfSimInst("VDUP", ["one"], ["value"])])
    instruction = program_to_canonical(p).context[0]
    assert (instruction.opcode, instruction.form) == ("VDUPS", "fp32")


def test_read_write_destination_uses_the_existing_definition():
    p = VfSimProgram(values={
        "one": VfSimValue("one", StorageKind.SCALAR, "fp32"),
        "value": VfSimValue("value", StorageKind.REGISTER, "fp32"),
        "acc": VfSimValue("acc", StorageKind.REGISTER, "fp32"),
        "mask": VfSimValue("mask", StorageKind.PREDICATE_REGISTER, "bool"),
    }, body=[
        VfSimInst("PSET_B32", [], ["mask"]),
        VfSimInst("VDUPS", ["one", "mask"], ["value"]),
        VfSimInst("VMOV", ["value"], ["acc"]),
        VfSimInst("VMADD", ["acc", "value", "value", "mask"], ["acc"]),
    ])
    canonical = program_to_canonical(p)
    write = canonical.context[-1]
    assert write.inputs[0].value_id == canonical.context[-2].outputs[0].value_id
    assert write.outputs[0].value_id != write.inputs[0].value_id
    assert predict_from_program(p, dump_results=False)["cycles"] > 0


@pytest.mark.parametrize("explicit", [False, True])
def test_predicate_memory_converts_element_offsets_to_bytes(explicit):
    p = VfSimProgram(values={
        "mask": VfSimValue("mask", StorageKind.PREDICATE_REGISTER, "bool"),
        "buf": VfSimValue("buf", StorageKind.UB, "uint32", (64,), "allocation", (4,)),
    }, body=[
        VfSimInst("PSET_B8", [], ["mask"]),
        VfSimInst("PSTS", ["mask"], ["buf"],
                  memory_accesses=(VfSimMemoryAccess("buf", "write", offset=4, span=8),)
                  if explicit else ()),
    ])
    access = program_to_canonical(p).context[-1].outputs[0].memory_access
    assert (access.offset.constant, access.span, access.address_unit_bytes) == (16, 32, 1)


@pytest.mark.parametrize("prefix", ["", "vfsimulator."])
def test_repeated_calls_reuse_parameters_and_observe_file_changes(tmp_path, prefix):
    api = import_module(prefix + "api.tilesim")
    predict = import_module(prefix + "api.tilesim.predict")
    loader = import_module(prefix + "core.params.loader")
    predict._model.cache_clear()
    config = tmp_path / "configuration"
    shutil.copytree(Path(__file__).resolve().parents[1] / "configs", config / "configs")
    original = program()
    value = api.VfSimProgram(
        params=original.params,
        values={name: api.VfSimValue(item.value_id, item.storage.value, item.dtype,
                                    item.shape, item.storage_object_id, item.offsets)
                for name, item in original.values.items()},
        body=[api.VfSimLoop("N", [
            api.VfSimInst("VLDS", ["V_memory"], ["mem_register"]),
            api.VfSimInst("VADDS", ["mem_register", "scalar"], ["mem_register"]),
            api.VfSimInst("VSTS", ["mem_register"], ["view"]),
        ], name="repeat")],
    )
    output = tmp_path / "silent"
    with patch.object(loader, "read_json", wraps=loader.read_json) as read:
        first = api.predict_from_program(value, config_root=config, out_dir=output, dump_results=False)
        assert read.call_count == 4
        cycles = first["cycles"]
        first["raw"]["vf_end_cycle"] = -1
        second = api.predict_from_program(value, config_root=config, out_dir=output, dump_results=False)
        assert second["cycles"] == second["raw"]["vf_end_cycle"] == cycles
        assert read.call_count == 4
        assert not output.exists()
        path = config / "configs/uarch.json"
        settings = json.loads(path.read_text())
        settings["exq_issue_inflight_cap_per_port"] = 8
        path.write_text(json.dumps(settings))
        assert api.predict_from_program(value, config_root=config, dump_results=False)["cycles"] > 0
        assert read.call_count == 8


@pytest.mark.parametrize("kwargs", [{"offset": True, "span": 1}, {"offset": 0, "span": True}])
def test_unit_conversion_keeps_boolean_memory_fields_invalid(kwargs):
    p = VfSimProgram(values={
        "buf": VfSimValue("buf", StorageKind.UB, "fp32", (64,), "allocation"),
        "value": VfSimValue("value", StorageKind.REGISTER, "fp32"),
    }, body=[VfSimInst("VLDS", ["buf"], ["value"], config={"catalog_mode": "BRC_B32"},
                      memory_accesses=(VfSimMemoryAccess("buf", "read", **kwargs),))])
    with pytest.raises(ValueError, match="memory (offset|span) must be an integer"):
        program_to_canonical(p)


def test_dump_results_and_canonical_dump_have_independent_paths(tmp_path):
    trace = tmp_path / "canonical.json"
    result = predict_from_program(program(), out_dir=tmp_path / "silent",
                                  dump_results=False, dump_trace_path=trace)
    assert trace.exists() and not (tmp_path / "silent").exists()
    assert result["trace_path"] == str(trace)
    assert result["model"] == "mainline"
    assert result["cycles"] == result["raw"]["vf_end_cycle"]
