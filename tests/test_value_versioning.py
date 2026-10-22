from core.uarch.ifu import IFU
from tests.core_support import expanded, engine_for
import tempfile
import unittest
from pathlib import Path
from core.execution.prepare import CoreLoweringPass
from api.common.diagnostics import VfInfoValidationError
from api.common.schema import (
    CanonicalInstruction,
    CanonicalLoop,
    OperandRole,
    SourceLocation,
)
from api.common.validator import validate_canonical_vf_info
from api.cce.value_versioning import ValueVersioningPass
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel
from api.cce.ir import (
    AdapterAlias,
    AdapterInstruction,
    AdapterLoop,
    AdapterMemoryAccess,
    AdapterMembar,
    AdapterProgram,
    AdapterValue,
)
from api.cce.normalization import normalize_adapter_program
from core.params.database import ParamDB


class ValueVersioningPassTest(unittest.TestCase):
    def _version(self, program: AdapterProgram):
        program.live_in_values = tuple(
            (
                name
                for name, value in program.values.items()
                if value.storage == "Register"
            )
        )
        canonical = ValueVersioningPass().run(normalize_adapter_program(program))
        validate_canonical_vf_info(canonical).raise_for_errors()
        return canonical

    def test_versioning_does_not_repair_omitted_scalar_operand(self):
        vf_info = AdapterProgram(
            values={
                "src": AdapterValue("src", "Register", "fp32"),
                "dst": AdapterValue("dst", "Register", "fp32"),
            },
            context=[AdapterInstruction("VADDS", ["src"], ["dst"], "fp32")],
        )
        with self.assertRaises(VfInfoValidationError) as captured:
            self._version(vf_info)
        self.assertTrue(
            any(
                (
                    diagnostic.code == "catalog_operand_count_mismatch"
                    for diagnostic in captured.exception.diagnostics
                )
            )
        )

    def _accumulator_vf_info(self, *, count=4, unroll=1):
        values = {
            "input": AdapterValue("input", "UB", "fp32", (64,)),
            "output": AdapterValue("output", "UB", "fp32", (64,)),
            "tmp": AdapterValue("tmp", "Register", "fp32", (64,)),
            "acc": AdapterValue("acc", "Register", "fp32", (64,)),
            "final": AdapterValue("final", "Register", "fp32", (64,)),
        }
        return AdapterProgram(
            values=values,
            context=[
                AdapterLoop(
                    count=count,
                    unroll=unroll,
                    loop_id="loop.acc",
                    induction_variable="i",
                    body=[
                        AdapterInstruction(
                            "VLDS",
                            ["input"],
                            ["tmp"],
                            "fp32",
                            memory_accesses=(
                                AdapterMemoryAccess("input", "read", span=64),
                            ),
                        ),
                        AdapterInstruction("VADD", ["tmp", "acc"], ["acc"], "fp32"),
                        AdapterInstruction(
                            "VSTS",
                            ["acc"],
                            ["output"],
                            "fp32",
                            memory_accesses=(
                                AdapterMemoryAccess("output", "write", span=64),
                            ),
                        ),
                    ],
                ),
                AdapterMembar("VST_VLD"),
                AdapterInstruction("VEXP", ["acc"], ["final"], "fp32"),
            ],
        )

    def test_versions_straight_line_redefinitions(self):
        vf_info = AdapterProgram(
            values={
                "lhs": AdapterValue("lhs", "Register", "fp32"),
                "rhs": AdapterValue("rhs", "Register", "fp32"),
                "acc": AdapterValue("acc", "Register", "fp32"),
            },
            context=[
                AdapterInstruction("VADD", ["lhs", "rhs"], ["acc"], "fp32"),
                AdapterInstruction("VMUL", ["acc", "rhs"], ["acc"], "fp32"),
            ],
        )
        canonical = self._version(vf_info)
        first, second = canonical.context
        self.assertIsInstance(first, CanonicalInstruction)
        self.assertNotEqual(first.outputs[0].value_id, second.outputs[0].value_id)
        self.assertEqual(second.inputs[0].value_id, first.outputs[0].value_id)
        self.assertTrue(validate_canonical_vf_info(canonical).ok)

    def test_long_canonical_definition_chain_releases_registers(self):
        values = {
            "acc": AdapterValue("acc", "Register", "fp32"),
            "rhs": AdapterValue("rhs", "Register", "fp32"),
        }
        vf_info = AdapterProgram(
            values=values,
            context=[
                AdapterInstruction("VADD", ["acc", "rhs"], ["acc"], "fp32")
                for _ in range(80)
            ],
        )
        canonical = self._version(vf_info)
        self.assertTrue(
            all(
                (
                    value_id.startswith("acc.def")
                    for value_id in canonical.values
                    if value_id.startswith("acc.")
                )
            )
        )
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)
        self.assertLess(result["vf_end_cycle"], 1000000)

    def test_long_unroll_one_loop_releases_dynamic_value_instances(self):
        vf_info = AdapterProgram(
            values={
                "acc": AdapterValue("acc", "Register", "fp32"),
                "rhs": AdapterValue("rhs", "Register", "fp32"),
                "final": AdapterValue("final", "Register", "fp32"),
            },
            context=[
                AdapterLoop(
                    count=80,
                    unroll=1,
                    loop_id="loop.long_acc",
                    induction_variable="i",
                    body=[AdapterInstruction("VADD", ["acc", "rhs"], ["acc"], "fp32")],
                ),
                AdapterInstruction("VEXP", ["acc"], ["final"], "fp32"),
            ],
        )
        canonical = self._version(vf_info)
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)
        self.assertLess(result["vf_end_cycle"], 1000000)

    def test_cce_scalar_vdup_uses_catalog_source_role(self):
        source = "\n        void scalar_vdup() {\n          __VEC_SCOPE__ {\n            vector_f32 dst;\n            vector_bool mask = pset_b32(PAT_ALL);\n            vdup(dst, -3.4028234663852886e38f, mask, MODE_ZEROING);\n          }\n        }\n        "
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scalar_vdup.cce"
            path.write_text(source, encoding="utf-8")
            canonical = InputAPI.load_cce(path, "scalar_vdup")
        operand = canonical.context[1].inputs[0]
        self.assertEqual(operand.role, OperandRole.SOURCE)
        self.assertEqual(canonical.values[operand.value_id].storage.value, "Scalar")
        self.assertTrue(validate_canonical_vf_info(canonical).ok)

    def test_cce_local_ub_pointer_alias_preserves_stable_base(self):
        source = "\n        void ub_alias(__ubuf__ float *scores) {\n          __VEC_SCOPE__ {\n            vector_f32 value;\n            __ubuf__ float *score_ptr = scores + 64;\n            for (uint32_t row = 0; row < 2; ++row) {\n              vld(value, score_ptr, vag_b32(128), NORM);\n            }\n          }\n        }\n        "
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ub_alias.cce"
            path.write_text(source, encoding="utf-8")
            canonical = InputAPI.load_cce(path, "ub_alias")
        loop = canonical.context[0]
        self.assertIsInstance(loop, CanonicalLoop)
        self.assertEqual(len(loop.body), 1)
        memory = loop.body[0].inputs[0].memory_access
        self.assertIsNotNone(memory)
        self.assertEqual(memory.offset.constant, 256)
        self.assertEqual(
            [(t.variable_id, t.coefficient) for t in memory.offset.terms],
            [("row", 512)],
        )
        base_object = canonical.storage_objects[memory.base_object_id]
        base_values = [
            value
            for value in canonical.values.values()
            if value.storage_object_id == base_object.object_id
        ]
        self.assertTrue(any((value.logical_id == "scores" for value in base_values)))
        self.assertTrue(validate_canonical_vf_info(canonical).ok)

    def test_cce_register_alias_snapshots_definition_before_source_rewrite(self):
        source = "\n        void alias_case() {\n          __VEC_SCOPE__ {\n            vector_f32 a, b, out;\n            vector_bool pred = pset_b32(PAT_ALL);\n            vdup(a, 1.0f, pred, MODE_ZEROING);\n            b = a;\n            vdup(a, 2.0f, pred, MODE_ZEROING);\n            vadd(out, b, a, pred);\n          }\n        }\n        "
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "register_alias.cce"
            path.write_text(source, encoding="utf-8")
            canonical = InputAPI.load_cce(path, "alias_case")
            result = CoreVfCostModel(out_dir=tmpdir).run_vf_info(canonical)
        pset, first_dup, second_dup, add = canonical.context
        self.assertEqual(add.inputs[0].value_id, first_dup.outputs[0].value_id)
        self.assertEqual(add.inputs[1].value_id, second_dup.outputs[0].value_id)
        self.assertNotEqual(add.inputs[0].value_id, add.inputs[1].value_id)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_loop_register_alias_forms_cross_iteration_dependency(self):
        vf_info = AdapterProgram(
            values={
                "a": AdapterValue("a", "Register", "fp32"),
                "b": AdapterValue("b", "Register", "fp32"),
                "out": AdapterValue("out", "Register", "fp32"),
            },
            context=[
                AdapterLoop(
                    count=2,
                    loop_id="loop.alias",
                    induction_variable="i",
                    body=[
                        AdapterInstruction("VADD", ["b", "a"], ["out"], "fp32"),
                        AdapterAlias("b", "out"),
                    ],
                )
            ],
        )
        canonical = self._version(vf_info)
        loop = canonical.context[0]
        carried = {item.logical_id: item for item in loop.carried_values}
        add = loop.body[0]
        self.assertEqual(carried["b"].back_edge_value_id, add.outputs[0].value_id)
        self.assertNotEqual(
            canonical.values[carried["b"].back_edge_value_id].logical_id,
            carried["b"].logical_id,
        )
        self.assertTrue(validate_canonical_vf_info(canonical).ok)
        lowered = CoreLoweringPass().lower(canonical)
        dynamic = IFU(expanded(canonical, None)).take(2)
        db = ParamDB(base_dir=str(Path(__file__).parents[1]))
        core = engine_for(canonical)
        for inst in dynamic:
            core.admit(inst)
        adds = [uop for uop in list(core.active_uops.values()) if uop.op == "VADD"]
        self.assertEqual(adds[1].preg_src[0], adds[0].preg_dst[0])

    def test_loop_alias_back_edge_accepts_invariant_and_entry_values(self):
        for alias_source in ("a", "out"):
            for count in (0, 1, 2):
                with self.subTest(alias_source=alias_source, count=count):
                    vf_info = AdapterProgram(
                        values={
                            "a": AdapterValue("a", "Register", "fp32"),
                            "b": AdapterValue("b", "Register", "fp32"),
                            "out": AdapterValue("out", "Register", "fp32"),
                            "final": AdapterValue("final", "Register", "fp32"),
                        },
                        context=[
                            AdapterLoop(
                                count=count,
                                loop_id="loop.alias.entry",
                                induction_variable="i",
                                body=[
                                    AdapterAlias("b", alias_source),
                                    AdapterInstruction(
                                        "VADD", ["b", "a"], ["out"], "fp32"
                                    ),
                                ],
                            ),
                            AdapterInstruction("VADD", ["b", "a"], ["final"], "fp32"),
                        ],
                    )
                    canonical = self._version(vf_info)
                    self.assertTrue(
                        validate_canonical_vf_info(canonical).ok,
                        validate_canonical_vf_info(canonical).diagnostics,
                    )
                    lowered = CoreLoweringPass().lower(canonical)
                    dynamic = IFU(expanded(canonical, None)).take(count + 1)
                    db = ParamDB(base_dir=str(Path(__file__).parents[1]))
                    core = engine_for(canonical)
                    for inst in dynamic:
                        core.admit(inst)
                    adds = [
                        uop
                        for uop in list(core.active_uops.values())
                        if uop.op == "VADD"
                    ]
                    self.assertEqual(len(adds), count + 1)
                    if alias_source == "a":
                        self.assertTrue(
                            all(
                                (uop.preg_src[0] == adds[0].preg_src[0] for uop in adds)
                            )
                        )
                    elif count == 0:
                        self.assertNotEqual(
                            adds[-1].src_value_instances[0]["definition_id"],
                            adds[-1].src_value_instances[1]["definition_id"],
                        )
                    elif count == 1:
                        self.assertEqual(adds[-1].preg_src[0], adds[0].preg_src[0])
                    else:
                        self.assertEqual(adds[1].preg_src[0], adds[0].preg_dst[0])
                        self.assertEqual(adds[-1].preg_src[0], adds[0].preg_dst[0])

    def test_cce_cast_ub_pointer_alias_chain_accumulates_offset(self):
        source = "\n        void cast_alias(__ubuf__ float *scores) {\n          __VEC_SCOPE__ {\n            vector_f32 value;\n            __ubuf__ float *ptr = (__ubuf__ float *)(scores + 64);\n            __ubuf__ float *ptr2 = (__ubuf__ float *)(ptr + 32);\n            vlds(value, ptr2, 0, NORM);\n          }\n        }\n        "
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "cast_alias.cce"
            path.write_text(source, encoding="utf-8")
            canonical = InputAPI.load_cce(path, "cast_alias")
        memory = canonical.context[0].inputs[0].memory_access
        self.assertEqual(memory.offset.constant, 96)
        base_values = [
            value
            for value in canonical.values.values()
            if value.storage_object_id == memory.base_object_id
        ]
        self.assertTrue(any((value.logical_id == "scores" for value in base_values)))

    def test_cce_ub_pointer_alias_rejects_unconsumed_expression_prefix(self):
        expressions = ("64 + scores", "unknown + scores", "identity(scores)")
        for expression in expressions:
            source = f"\n            void invalid_alias(__ubuf__ float *scores) {{\n              __VEC_SCOPE__ {{\n                __ubuf__ float *ptr = {expression};\n              }}\n            }}\n            "
            with (
                self.subTest(expression=expression),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                path = Path(tmpdir) / "invalid_alias.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(
                    ValueError, "Unsupported local UB pointer initializer"
                ):
                    InputAPI.load_cce(path, "invalid_alias")

    def test_versioning_preserves_structured_validation_diagnostics(self):
        location = SourceLocation(path="invalid.cce", line=12, column=3)
        vf_info = AdapterProgram(
            values={
                "lhs": AdapterValue("lhs", "Register", "fp32"),
                "rhs": AdapterValue("rhs", "Register", "fp32"),
                "dst": AdapterValue("dst", "Register", "fp32"),
            },
            context=[
                AdapterInstruction(
                    "VADD",
                    ["lhs", "rhs"],
                    ["dst"],
                    "fp32",
                    instruction_class="store",
                    source_location=location,
                )
            ],
        )
        with self.assertRaises(VfInfoValidationError) as captured:
            self._version(vf_info)
        self.assertTrue(captured.exception.diagnostics)
        self.assertTrue(
            any(
                (
                    item.context.get("path") == "context[0]"
                    for item in captured.exception.diagnostics
                )
            )
        )
        self.assertTrue(
            all(
                (
                    item.location == location
                    for item in captured.exception.diagnostics
                    if item.context.get("path") == "context[0]"
                )
            )
        )

    def test_structured_expansion_has_configurable_hard_limit(self):
        canonical = self._version(self._accumulator_vf_info(count=4, unroll=1))
        lowered = CoreLoweringPass().lower(canonical)
        with self.assertRaisesRegex(
            RuntimeError, "canonical_dynamic_instruction_limit=5"
        ):
            expanded(canonical, 5)

    def test_versions_loop_entry_back_edge_and_exit(self):
        canonical = self._version(self._accumulator_vf_info())
        loop = canonical.context[0]
        after = canonical.context[2]
        self.assertIsInstance(loop, CanonicalLoop)
        carried = {item.logical_id: item for item in loop.carried_values}
        self.assertEqual(set(carried), {"acc", "tmp"})
        add = loop.body[1]
        self.assertEqual(add.inputs[1].value_id, carried["acc"].entry_value_id)
        self.assertEqual(add.outputs[0].value_id, carried["acc"].back_edge_value_id)
        self.assertEqual(after.inputs[0].value_id, carried["acc"].exit_value_id)
        self.assertTrue(validate_canonical_vf_info(canonical).ok)
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_zero_iteration_loop_exit_resolves_to_entry(self):
        canonical = self._version(self._accumulator_vf_info(count=0))
        lowered = CoreLoweringPass().lower(canonical)
        loop = lowered["program"][0]
        after = lowered["program"][2]
        carried = {item["logical_id"]: item for item in loop["carried_values"]}
        self.assertEqual(after["src"], [carried["acc"]["exit_value_id"]])
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_nested_loop_exit_is_outer_back_edge(self):
        vf_info = AdapterProgram(
            values={
                "acc": AdapterValue("acc", "Register", "fp32"),
                "rhs": AdapterValue("rhs", "Register", "fp32"),
            },
            context=[
                AdapterLoop(
                    count=2,
                    loop_id="loop.outer",
                    induction_variable="i",
                    body=[
                        AdapterLoop(
                            count=2,
                            loop_id="loop.inner",
                            induction_variable="j",
                            body=[
                                AdapterInstruction(
                                    "VADD", ["acc", "rhs"], ["acc"], "fp32"
                                )
                            ],
                        )
                    ],
                )
            ],
        )
        canonical = self._version(vf_info)
        outer = canonical.context[0]
        inner = outer.body[0]
        outer_acc = next(
            (item for item in outer.carried_values if item.logical_id == "acc")
        )
        inner_acc = next(
            (item for item in inner.carried_values if item.logical_id == "acc")
        )
        self.assertEqual(outer_acc.back_edge_value_id, inner_acc.exit_value_id)
        self.assertEqual(inner_acc.entry_value_id, outer_acc.entry_value_id)
        self.assertTrue(validate_canonical_vf_info(canonical).ok)
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_cce_canonical_preserves_affine_memory_and_source(self):
        source = "\n        void canonical_vf(__ubuf__ float *input, __ubuf__ float *output) {\n          __VEC_SCOPE__ {\n            vector_bool pred = pset_b32(PAT_ALL);\n            for (uint32_t i = 2; i < 6; i += 2) {\n              uint32_t off = 64 * i;\n              vector_f32 value;\n              vector_f32 sum;\n              vlds(value, input, off, NORM);\n              vadds(sum, value, 1.0f, pred);\n              vsts(sum, output, off, NORM_B32, pred);\n              mem_bar(VST_VLD);\n            }\n          }\n        }\n        "
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "canonical.cce"
            path.write_text(source, encoding="utf-8")
            canonical = InputAPI.load_cce(path, "canonical_vf")
        loop = canonical.context[1]
        load = loop.body[0]
        store = loop.body[2]
        self.assertEqual(loop.induction.start, 2)
        self.assertEqual(loop.induction.step, 2)
        self.assertEqual(loop.count, 2)
        self.assertEqual(load.source_location.path, str(path))
        self.assertEqual(load.inputs[0].memory_access.offset.constant, 0)
        self.assertEqual(load.inputs[0].memory_access.offset.terms[0].variable_id, "i")
        self.assertEqual(load.inputs[0].memory_access.offset.terms[0].coefficient, 64)
        output_object = next(
            (
                value.storage_object_id
                for value in canonical.values.values()
                if value.logical_id == "output"
            )
        )
        self.assertEqual(store.outputs[0].memory_access.base_object_id, output_object)
        self.assertTrue(validate_canonical_vf_info(canonical).ok)
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_canonical_unroll_uses_structured_lane_dependencies(self):
        canonical = self._version(self._accumulator_vf_info(count=4, unroll=2))
        lowered = CoreLoweringPass().lower(canonical)
        dynamic = IFU(expanded(canonical, None)).take(13)
        loop_insts = dynamic[:12]
        self.assertFalse(
            any(
                (
                    "_lane" in str(value)
                    for inst in loop_insts
                    for value in [*inst.src, *inst.dst]
                )
            )
        )
        self.assertEqual(
            [
                (inst.op, inst.iteration_path[-1]["iteration"])
                for inst in loop_insts[:6]
            ],
            [
                ("VLDS", 0),
                ("VLDS", 1),
                ("VADD", 0),
                ("VADD", 1),
                ("VSTS", 0),
                ("VSTS", 1),
            ],
        )
        db = ParamDB(base_dir=str(Path(__file__).parents[1]))
        core = engine_for(canonical)
        for inst in loop_insts:
            core.admit(inst)
        loads = [uop for uop in list(core.active_uops.values()) if uop.op == "VLDS"]
        adds = [uop for uop in list(core.active_uops.values()) if uop.op == "VADD"]
        for load, add in zip(loads, adds):
            self.assertEqual(add.preg_src[0], load.preg_dst[0])
        for previous, current in zip(adds, adds[1:]):
            self.assertEqual(current.preg_src[1], previous.preg_dst[0])
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_structured_unroll_does_not_leak_rat_mappings(self):
        canonical = self._version(self._accumulator_vf_info(count=64, unroll=4))
        with tempfile.TemporaryDirectory() as out_dir:
            result = CoreVfCostModel(out_dir=out_dir).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)


if __name__ == "__main__":
    unittest.main()
