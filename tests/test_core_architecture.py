from api.input_api import InputAPI
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from api.common.schema import StorageKind
from api.json.serialization import canonical_vf_info_from_dict, canonical_vf_info_to_dict
from core.execution.prepare import ValueStorageLookup
from core.simulator import CoreVfCostModel
from tests.core_support import engine_for
from tests.core_support import parse_vector_body as parse, records


class CoreArchitectureTest(unittest.TestCase):
    def test_core_requires_canonical_objects(self):
        model = CoreVfCostModel(out_dir=None)
        for data in ("input.cce", Path("input.json"), [], {"context": [], "values": {}}, None):
            with self.subTest(data=data), self.assertRaisesRegex(TypeError, "only CanonicalVfInfo"):
                model.run_vf_info(data)

    def test_model_fallback_dtype_and_default_canonical_schedule(self):
        self.assertEqual(CoreVfCostModel().fallback_dtype, 'fp32')
        self.assertEqual(CoreVfCostModel(fallback_dtype='fp16').fallback_dtype, 'fp16')
        fixture = Path(__file__).parent / 'fixtures/canonical_vf_info/v1_valid_loop.json'
        vf = InputAPI.load_json(fixture)
        result = CoreVfCostModel(out_dir=None).run_vf_info(vf)
        self.assertEqual((result['cycles_executed'], result['vf_end_cycle']), (35, 47))

    def test_preparation_helpers_apply_vf_overrides_like_the_model(self):
        from tests.core_support import expanded
        vf = parse('mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);'
                   'vadds(y,x,1,mask);vsts(y,out,0,NORM_B32,mask);')
        overridden = InputAPI.from_canonical(replace(vf, uarch={'ooo_to_lsq_delay': 7}))
        result = engine_for(overridden).run(None)
        expected = CoreVfCostModel(out_dir=None).run_vf_info(overridden)
        self.assertEqual(result.vf_end_cycle, expected['vf_end_cycle'])
        limited = InputAPI.from_canonical(replace(vf, uarch={'canonical_dynamic_instruction_limit': 1}))
        for prepare in (expanded, engine_for):
            with self.subTest(prepare=prepare.__name__), self.assertRaisesRegex(RuntimeError, 'canonical_dynamic_instruction_limit'):
                prepare(limited)

    def test_parameter_database_default_root_survives_directory_move(self):
        from core.params.database import ParamDB
        db = ParamDB()
        self.assertEqual(Path(db.base_dir), Path(__file__).resolve().parents[1])
        self.assertGreater(db.resolve_inst("VADD", "fp32", "fp32").latency, 0)

    def test_storage_uses_explicit_declarations(self):
        lookup = ValueStorageLookup({"mem_register": {"storage": "Register"},
                                     "v_buffer": {"storage": "UB"},
                                     "v_scalar": {"storage": "Scalar"}})
        self.assertTrue(lookup.is_register("mem_register"))
        self.assertTrue(lookup.is_ub("v_buffer"))
        self.assertTrue(lookup.is_scalar("v_scalar"))
        for name in ("v0", "V0", "mem0", "mask0"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Undeclared"):
                lookup.storage_of(name)

    def test_renaming_values_and_storage_objects_preserves_schedule(self):
        vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                   "for(int i=0;i<4;++i){vcmp_gt(p,x,x,mask);vsel(y,x,x,p);"
                   "vsts(y,out,64*i,NORM_B32,mask);}mem_bar(VST_VLD);")
        payload = canonical_vf_info_to_dict(vf)
        # Use names that differ from their declared storage category.
        names = {}
        for index, (key, value) in enumerate(vf.values.items()):
            prefix = "mem" if value.storage in (StorageKind.REGISTER, StorageKind.PREDICATE_REGISTER) else "v"
            names[key] = f"{prefix}_opaque_{index}"
        names.update({key: f"v_object_{i}" for i, key in enumerate(vf.storage_objects)})

        def rename(value):
            if isinstance(value, dict):
                return {names.get(k, k): rename(v) for k, v in value.items()}
            if isinstance(value, list):
                return [rename(item) for item in value]
            return names.get(value, value) if isinstance(value, str) else value

        renamed = InputAPI.from_canonical(canonical_vf_info_from_dict(rename(payload)))
        results = []
        schedules = []
        for program in (vf, renamed):
            with tempfile.TemporaryDirectory() as tmp:
                result = CoreVfCostModel(out_dir=tmp).run_vf_info(program)
                results.append((result["cycles_executed"], result["vf_end_cycle"]))
                schedules.append([(row["inst_id"], row["op"], row["cy"], row["preg_src"], row["preg_dst"])
                                  for row in records(Path(tmp) / "start_by_cycle.json")])
        self.assertEqual(results[0], results[1])
        self.assertEqual(schedules[0], schedules[1])

    def test_internal_storage_identity_cannot_shadow_a_canonical_register(self):
        fixture = Path(__file__).parent / "fixtures/canonical_vf_info/v1_valid_loop.json"
        payload = json.loads(fixture.read_text())
        original = InputAPI.from_canonical(canonical_vf_info_from_dict(payload))
        register_id = "__canonical_ub__ub.input"
        value = payload["values"].pop("acc.0")
        value["definition_id"] = register_id
        payload["values"][register_id] = value
        payload["context"][0]["body"][0]["outputs"][0]["value_id"] = register_id
        renamed = InputAPI.from_canonical(canonical_vf_info_from_dict(payload))
        engine = engine_for(renamed)
        self.assertTrue(engine.registers.value_storage.is_register(register_id))
        expected = CoreVfCostModel(out_dir=None).run_vf_info(original)
        result = engine.run(None)
        self.assertEqual(result.vf_end_cycle, expected["vf_end_cycle"])

    def test_backend_completion_drains_all_components(self):
        vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                   "vcmp_gt(p,x,x,mask);vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);")
        engine = engine_for(vf)
        result = engine.run(None)
        self.assertFalse(engine.active_uops)
        self.assertFalse(engine.exu.running)
        self.assertFalse(engine.lsu.running)
        self.assertEqual(set(row["event"] for row in engine.trace.history), {"start", "done"})
        self.assertGreater(result.vf_end_cycle, result.cycles_executed)


if __name__ == "__main__":
    unittest.main()
