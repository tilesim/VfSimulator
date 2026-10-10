import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

import api.common.validator as validator_module

from api.common import CanonicalVfInfo, CanonicalOperand, InstructionClass, OperandRole, StorageKind, VfInfoValidationError
from api.json.serialization import (canonical_vf_info_to_dict)
from api.cce.ir import AdapterInstruction, AdapterProgram
from api.cce.normalization import normalize_adapter_program
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel
from core.execution.prepare import CanonicalCoreCompatibilityError


ROOT = Path(__file__).resolve().parents[1]
SOURCE = """void vf(__ubuf__ float *input, __ubuf__ float *output) {
  __VEC_SCOPE__ {
    vector_f32 x, y;
    vector_bool p = pset_b32(PAT_ALL);
    vlds(x, input, 0, NORM);
    vadds(y, x, 1.0f, p);
    vsts(y, output, 0, NORM_B32, p);
  }
}
"""


class InputApiTest(unittest.TestCase):
    def test_cce_json_and_builder_preserve_dependencies_and_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cce = root / "input.cce"
            cce.write_text(SOURCE)
            parsed = InputAPI.load_cce(cce)

            builder = InputAPI.new_builder(source=parsed.source)
            for storage in parsed.storage_objects.values():
                builder.register_storage_object(
                    storage.object_id, storage=storage.storage, shape=storage.shape,
                    source_location=storage.source_location,
                )
            for value in parsed.values.values():
                builder.register_value(
                    value.definition_id, logical_id=value.logical_id,
                    storage=value.storage, dtype=value.dtype, shape=value.shape,
                    producer_node_id=value.producer_node_id,
                    storage_object_id=value.storage_object_id,
                    source_location=value.source_location,
                )
            for instruction in parsed.context:
                builder.add_instruction(
                    instruction.instruction_id, opcode=instruction.opcode,
                    instruction_class=instruction.instruction_class,
                    form=instruction.form, inputs=instruction.inputs,
                    outputs=instruction.outputs, attributes=instruction.attributes,
                    source_location=instruction.source_location,
                )
            built = builder.build()
            self.assertEqual(built, parsed)

            payload = root / "input.json"
            payload.write_text(json.dumps(canonical_vf_info_to_dict(parsed)))
            restored = InputAPI.load_json(payload)
            self.assertEqual(restored, parsed)
            for name, vf_info in (("cce", parsed), ("builder", built), ("json", restored)):
                with self.subTest(input=name):
                    result = CoreVfCostModel(out_dir=root / name).run_vf_info(vf_info)
                    self.assertEqual(result["vf_end_cycle"], 55)

    def test_api_rejects_mutated_canonical_maps_before_prediction(self):
        vf_info = InputAPI.load_json(
            ROOT / "tests/fixtures/canonical_vf_info/v1_valid_loop.json"
        )
        operand = vf_info.context[0].body[0].outputs[0]
        del vf_info.values[operand.value_id]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(VfInfoValidationError):
                InputAPI.from_canonical(vf_info)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_direct_canonical_input_is_checked_and_preserves_identity(self):
        vf = CanonicalVfInfo(context=(), values={})
        self.assertIs(InputAPI.from_canonical(vf), vf)
        with self.assertRaises(VfInfoValidationError):
            InputAPI.from_canonical(CanonicalVfInfo(context=(object(),), values={}))
        for value in ({"context": [], "values": {}}, "input.json", None):
            with self.subTest(value=value), self.assertRaises(TypeError):
                InputAPI.from_canonical(value)

    def test_each_input_is_validated_once_before_prediction(self):
        with tempfile.TemporaryDirectory() as tmp:
            cce = Path(tmp) / "input.cce"
            cce.write_text(SOURCE)
            fixture = ROOT / "tests/fixtures/canonical_vf_info/v1_valid_loop.json"
            template = InputAPI.load_json(fixture)
            direct = CanonicalVfInfo(
                context=template.context, values=dict(template.values),
                storage_objects=dict(template.storage_objects), params=dict(template.params),
                uarch=dict(template.uarch), source=dict(template.source),
                schema_version=template.schema_version,
            )
            builder = InputAPI.new_builder()
            for name, producer in (("lhs", None), ("rhs", None), ("out", "inst.add")):
                builder.register_value(name, logical_id=name, storage=StorageKind.REGISTER,
                                       dtype="fp32", producer_node_id=producer)
            builder.add_instruction(
                "inst.add", opcode="VADD", instruction_class=InstructionClass.COMPUTE, form="fp32",
                inputs=(CanonicalOperand("lhs", OperandRole.SOURCE, "fp32"),
                        CanonicalOperand("rhs", OperandRole.SOURCE, "fp32")),
                outputs=(CanonicalOperand("out", OperandRole.DESTINATION, "fp32"),),
            )
            loaders = {
                "cce": lambda: InputAPI.load_cce(cce),
                "json": lambda: InputAPI.load_json(fixture),
                "canonical": lambda: InputAPI.from_canonical(direct),
                "builder": builder.build,
            }
            validation_code = validator_module.validate_canonical_vf_info.__code__
            for name, load in loaders.items():
                with self.subTest(input=name):
                    calls = []
                    def record_validation(frame, event, arg):
                        if event == "call" and frame.f_code is validation_code:
                            calls.append(frame.f_locals["vf_info"])
                    previous_profiler = sys.getprofile()
                    sys.setprofile(record_validation)
                    try:
                        vf = load()
                        result = CoreVfCostModel(out_dir=None).run_vf_info(vf)
                    finally:
                        sys.setprofile(previous_profiler)
                    self.assertEqual(len(calls), 1)
                    self.assertIs(calls[0], vf)
                    self.assertGreater(result["vf_end_cycle"], 0)

    def test_core_rejects_valid_but_unsupported_execution_semantics(self):
        template = InputAPI.load_json(ROOT / "tests/fixtures/canonical_vf_info/v1_valid_loop.json")
        # Partial unroll is valid Canonical input but is not implemented by this Core.
        vf = InputAPI.from_canonical(replace(
            template, context=(replace(template.context[0], unroll=3),),
        ))
        with self.assertRaisesRegex(CanonicalCoreCompatibilityError, "not divisible"):
            CoreVfCostModel(out_dir=None).run_vf_info(vf)

    def test_undeclared_adapter_values_do_not_infer_storage_from_names(self):
        for name in ("v0", "memA", "scalar"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "declared storage"):
                normalize_adapter_program(AdapterProgram(
                    context=[AdapterInstruction("VUNKNOWN", [name], [])],
                ))

    def test_json_file_rejects_retired_program_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.json"
            path.write_text(json.dumps({"dtype": "fp32", "program": []}))
            with self.assertRaises(VfInfoValidationError):
                InputAPI.load_json(path)

    def test_cli_reports_rejected_input_with_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "old.json"
            path.write_text(json.dumps({"dtype": "fp32", "program": []}))
            result = subprocess.run([
                sys.executable, str(ROOT / "main.py"), "--trace", str(path),
                "--out_dir", str(root / "out"),
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("[ERROR]", result.stdout)
            self.assertFalse((root / "out").exists())

    def test_importing_input_api_does_not_load_simulation_modules(self):
        subprocess.run([
            sys.executable, "-c",
            "import api, sys; assert not any(n == 'core' or n.startswith('core.') for n in sys.modules)",
        ], cwd=ROOT, check=True, capture_output=True, text=True)


if __name__ == "__main__":
    unittest.main()
