import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from core.simulator import CoreVfCostModel
from api.json.serialization import canonical_vf_info_to_dict
from api.input_api import InputAPI
from core.params.database import ParamDB

ROOT = Path(__file__).resolve().parents[1]


class InstructionFallbackTest(unittest.TestCase):
    def test_unknown_vector_op_uses_default_compute_params(self):
        db = ParamDB(base_dir=str(ROOT))
        params = db.get_inst_form("VUNKNOWN", form="fp32", dtype="fp32")
        self.assertEqual(params["op_class"], "COMPUTE")
        self.assertEqual(params["EXU"], "ALU")
        self.assertEqual(params["dispatch_exu"], "EXU01")
        self.assertEqual(params["latency"], 9)
        self.assertTrue(
            any(
                (
                    warning["kind"] == "unsupported_isa_op"
                    for warning in db.get_warnings()
                )
            )
        )

    def test_instruction_profile_is_cached_and_immutable(self):
        db = ParamDB(base_dir=str(ROOT))
        first = db.resolve_inst("vadd", form="fp32", dtype="fp32")
        with patch.object(
            db,
            "get_inst_form",
            side_effect=AssertionError(
                "Cached profile must not query parameters again"
            ),
        ):
            second = db.resolve_inst("VADD", form="fp32", dtype="fp32")
        self.assertIs(first, second)
        self.assertEqual(first.op_class, "COMPUTE")
        self.assertEqual(first.fu_type, "ALU")
        self.assertEqual(first.latency, db.get_inst_form("VADD", "fp32")["latency"])
        with self.assertRaises((AttributeError, TypeError)):
            first.latency = 99

    def test_public_inst_params_cannot_mutate_cached_nested_values(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VNESTED_PROFILE"] = {
            "op_class": "COMPUTE",
            "forms": {
                "fp32": {
                    "latency": 7,
                    "EXU": "ALU",
                    "dispatch_exu": "EXU01",
                    "metadata": {"source": "config"},
                }
            },
        }
        first = db.get_inst_form("VNESTED_PROFILE", "fp32", "fp32")
        first["metadata"]["source"] = "caller"
        second = db.get_inst_form("VNESTED_PROFILE", "fp32", "fp32")
        self.assertEqual(second["metadata"]["source"], "config")

    def test_fallback_profile_records_one_warning_per_missing_configuration(self):
        db = ParamDB(base_dir=str(ROOT))
        db.resolve_inst("VPROFILE_UNKNOWN", "fp32", "fp32")
        db.resolve_inst("VPROFILE_UNKNOWN", "fp32", "fp32")
        db.get_inst_form("VPROFILE_UNKNOWN", "fp32", "fp32")
        warnings = [
            item
            for item in db.get_warnings()
            if item["kind"] == "unsupported_isa_op" and item["op"] == "VPROFILE_UNKNOWN"
        ]
        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["count"], 1)

    def test_forwarding_and_ii_are_cached_by_profile_pair(self):
        db = ParamDB(base_dir=str(ROOT))
        producer = db.resolve_inst("VEXPDIF", "fp32", "fp32")
        consumer = db.resolve_inst("VADD", "fp32", "fp32")
        forwarding = db.get_forwarding_for_profiles(producer, consumer)
        ii = db.get_ii_for_profiles(producer, consumer)
        with (
            patch.object(
                db,
                "_compute_forwarding_cycles",
                side_effect=AssertionError("Cached forwarding must not be recomputed"),
            ),
            patch.object(
                db,
                "_compute_ii",
                side_effect=AssertionError("Cached II must not be recomputed"),
            ),
        ):
            self.assertEqual(
                db.get_forwarding_for_profiles(producer, consumer), forwarding
            )
            self.assertEqual(db.get_ii_for_profiles(producer, consumer), ii)

    def test_profile_pair_rejects_profiles_from_another_param_db(self):
        first_db = ParamDB(base_dir=str(ROOT))
        second_db = ParamDB(base_dir=str(ROOT))
        first_producer = first_db.resolve_inst("VEXPDIF", "fp32", "fp32")
        first_consumer = first_db.resolve_inst("VADD", "fp32", "fp32")
        first_db.get_forwarding_for_profiles(first_producer, first_consumer)
        foreign_producer = second_db.resolve_inst("VADD", "fp32", "fp32")
        foreign_consumer = second_db.resolve_inst("VMUL", "fp32", "fp32")
        with self.assertRaisesRegex(ValueError, "different ParamDB"):
            first_db.get_forwarding_for_profiles(foreign_producer, foreign_consumer)
        with self.assertRaisesRegex(ValueError, "different ParamDB"):
            first_db.get_ii_for_profiles(foreign_producer, foreign_consumer)

    def test_profile_preserves_explicit_memory_class(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["CUSTOM_MEMORY_OP"] = {
            "op_class": "LOAD",
            "forms": {"fp32": {"latency": 7}},
        }
        profile = db.resolve_inst("CUSTOM_MEMORY_OP", "fp32", "fp32")
        self.assertEqual(profile.op_class, "LOAD")
        self.assertEqual(profile.latency, 7)

    def test_unknown_load_store_prefixes_use_lsu_defaults(self):
        db = ParamDB(base_dir=str(ROOT))
        load_params = db.get_inst_form("VLDX", form="fp32", dtype="fp32")
        store_params = db.get_inst_form("VSTX", form="fp32", dtype="fp32")
        self.assertEqual(load_params["op_class"], "LOAD")
        self.assertEqual(load_params["latency"], 9)
        self.assertEqual(store_params["op_class"], "STORE")
        self.assertEqual(store_params["latency"], 9)

    def test_missing_forwarding_and_ii_defaults(self):
        db = ParamDB(base_dir=str(ROOT))
        self.assertEqual(
            db.get_forwarding_cycles(
                "VLDX", "VSTX", dtype="fp32", producer_form="fp32", consumer_form="fp32"
            ),
            6,
        )
        self.assertEqual(
            db.get_ii(
                "VUNKNOWN", "VUNKNOWN2", dtype="fp32", prev_form="fp32", cur_form="fp32"
            ),
            1,
        )
        kinds = {warning["kind"] for warning in db.get_warnings()}
        self.assertIn("missing_forwarding_pair", kinds)
        self.assertIn("missing_ii_pair", kinds)

    def test_missing_ii_uses_two_when_prev_latency_exceeds_cur_by_one(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VPREV_LAT10"] = {
            "op_class": "COMPUTE",
            "forms": {"fp32": {"latency": 10, "EXU": "ALU", "dispatch_exu": "EXU01"}},
        }
        db._insts["VCUR_LAT9"] = {
            "op_class": "COMPUTE",
            "forms": {"fp32": {"latency": 9, "EXU": "ALU", "dispatch_exu": "EXU01"}},
        }
        self.assertEqual(
            db.get_ii(
                "VPREV_LAT10",
                "VCUR_LAT9",
                dtype="fp32",
                prev_form="fp32",
                cur_form="fp32",
            ),
            2,
        )

    def test_load_store_done_latency_uses_instruction_latency(self):
        payload = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/load_store_done_latency_uses_instruction_latency.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_vf_info(payload)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
        start_by_op = {item["op"]: item["cy"] for item in starts}
        done_by_op = {item["op"]: item["cy"] for item in dones}
        self.assertEqual(done_by_op["VLDS"] - start_by_op["VLDS"], 9)
        self.assertEqual(done_by_op["VSTS"] - start_by_op["VSTS"], 9)

    def test_vpack_and_vsstb_have_explicit_model_params(self):
        db = ParamDB(base_dir=str(ROOT))
        vpack = db.get_inst_form("VPACK", form="b32", dtype="bf16")
        vsstb = db.get_inst_form("VSSTB", form="b16", dtype="bf16")
        self.assertEqual(vpack["op_class"], "COMPUTE")
        self.assertEqual(vpack["latency"], 11)
        self.assertEqual(vpack["dispatch_exu"], "EXU0_ONLY")
        self.assertEqual(vsstb["op_class"], "STORE")
        self.assertEqual(vsstb["latency"], 9)
        self.assertEqual(
            db.get_forwarding_cycles(
                "VCVT_F32_TO_F16",
                "VPACK",
                dtype="bf16",
                producer_form="f32_to_f16",
                consumer_form="b32",
            ),
            4,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VPACK", "VADD", dtype="bf16", producer_form="b32", consumer_form="fp16"
            ),
            8,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VPACK", "VSSTB", dtype="bf16", producer_form="b32", consumer_form="b16"
            ),
            9,
        )

    def test_vexpdif_and_vmulscvt_have_explicit_model_params(self):
        db = ParamDB(base_dir=str(ROOT))
        vexpdif = db.get_inst_form("VEXPDIF", form="fp32", dtype="fp32")
        vmulscvt = db.get_inst_form("VMULSCVT", form="f32_to_f16", dtype="fp32")
        self.assertEqual(vexpdif["op_class"], "COMPUTE")
        self.assertEqual(vexpdif["latency"], 18)
        self.assertEqual(vexpdif["dispatch_exu"], "EXU01")
        self.assertEqual(vmulscvt["op_class"], "COMPUTE")
        self.assertEqual(vmulscvt["latency"], 8)
        self.assertEqual(vmulscvt["dispatch_exu"], "EXU01")
        self.assertEqual(
            db.get_forwarding_cycles(
                "VMULS",
                "VEXPDIF",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="fp32",
            ),
            5,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VEXPDIF",
                "VMULSCVT",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="f32_to_f16",
            ),
            15,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VMULSCVT",
                "VPACK",
                dtype="fp32",
                producer_form="f32_to_f16",
                consumer_form="b32",
            ),
            5,
        )
        self.assertEqual(
            db.get_ii(
                "VEXPDIF", "VEXPDIF", dtype="fp32", prev_form="fp32", cur_form="fp32"
            ),
            4,
        )

    def test_vcvt_f32_to_bf16_and_vsts_bf16_have_explicit_model_params(self):
        db = ParamDB(base_dir=str(ROOT))
        vcvt = db.get_inst_form("VCVT_F32_TO_BF16", form="f32_to_bf16", dtype="bf16")
        vsts = db.get_inst_form("VSTS", form="bf16", dtype="bf16")
        self.assertEqual(vcvt["op_class"], "COMPUTE")
        self.assertEqual(vcvt["latency"], 7)
        self.assertEqual(vcvt["dispatch_exu"], "EXU01")
        self.assertEqual(vsts["op_class"], "STORE")
        self.assertEqual(vsts["latency"], 9)
        self.assertEqual(
            db.get_forwarding_cycles(
                "VEXP",
                "VCVT_F32_TO_BF16",
                dtype="bf16",
                producer_form="fp32",
                consumer_form="f32_to_bf16",
            ),
            13,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VCVT_F32_TO_BF16",
                "VSTS",
                dtype="bf16",
                producer_form="f32_to_bf16",
                consumer_form="bf16",
            ),
            5,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VCVT_F32_TO_BF16",
                "VPACK",
                dtype="bf16",
                producer_form="f32_to_bf16",
                consumer_form="b32",
            ),
            4,
        )
        self.assertEqual(
            db.get_forwarding_cycles(
                "VEXPDIF",
                "VADD",
                dtype="fp32",
                producer_form="fp32",
                consumer_form="fp32",
            ),
            15,
        )

    def test_vpack_vsstb_forms_do_not_fallback_through_python_idu(self):
        payload = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/vpack_vsstb_forms_do_not_fallback_through_python_idu.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_vf_info(payload)
            warnings = json.loads(
                (out_dir / "model_warnings.json").read_text(encoding="utf-8")
            )
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
        fallback_warnings = warnings["instruction_fallback_warnings"]
        self.assertFalse(
            any(
                (
                    item["kind"] == "unsupported_isa_form"
                    and item.get("op") in {"VPACK", "VSSTB"}
                    for item in fallback_warnings
                )
            )
        )
        self.assertEqual([item["op"] for item in starts], ["VLDS", "VPACK", "VSSTB"])

    def test_compatible_form_fallback_uses_fp_params_for_b_forms(self):
        db = ParamDB(base_dir=str(ROOT))
        vadd = db.get_inst_form("VADD", form="b16", dtype="bf16")
        self.assertEqual(vadd["form"], "b16")
        self.assertEqual(vadd["resolved_form"], "fp16")
        self.assertEqual(
            vadd["latency"], db.get_inst_form("VADD", form="fp16")["latency"]
        )
        self.assertTrue(db.has_inst("VADD", "b16"))
        self.assertEqual(
            db.get_forwarding_cycles(
                "VADD", "VMUL", dtype="bf16", producer_form="b16", consumer_form="b16"
            ),
            db.get_forwarding_cycles(
                "VADD", "VMUL", dtype="fp16", producer_form="fp16", consumer_form="fp16"
            ),
        )
        self.assertEqual(
            db.get_ii("VADD", "VMUL", dtype="bf16", prev_form="b16", cur_form="b16"),
            db.get_ii("VADD", "VMUL", dtype="fp16", prev_form="fp16", cur_form="fp16"),
        )
        kinds = {warning["kind"] for warning in db.get_warnings()}
        self.assertIn("compatible_isa_form_fallback", kinds)
        self.assertIn("compatible_forwarding_pair_fallback", kinds)
        self.assertIn("compatible_ii_pair_fallback", kinds)

    def test_missing_fp16_form_does_not_borrow_fp32_params(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VFP32ONLY"] = {
            "op_class": "COMPUTE",
            "forms": {
                "fp32": {"latency": 123, "EXU": "SFU", "dispatch_exu": "EXU0_ONLY"}
            },
        }
        params = db.get_inst_form("VFP32ONLY", form="fp16", dtype="fp32")
        self.assertEqual(params["latency"], 9)
        self.assertEqual(params["dispatch_exu"], "EXU01")
        self.assertTrue(
            any(
                (
                    warning["kind"] == "unsupported_isa_form"
                    and warning.get("op") == "VFP32ONLY"
                    and (warning.get("form") == "fp16")
                    for warning in db.get_warnings()
                )
            )
        )

    def test_vector_align_stores_have_explicit_timing(self):
        db = ParamDB(base_dir=str(ROOT))
        for op, latency in (("VSTUS", 9), ("VSTAS", 8)):
            params = db.get_inst_form(op, form="fp32", dtype="fp32")
            self.assertEqual(params["op_class"], "STORE")
            self.assertEqual(params["latency"], latency)
        self.assertFalse(
            any(
                (
                    warning.get("op") in {"VSTUS", "VSTAS"}
                    for warning in db.get_warnings()
                )
            )
        )

    def test_vector_align_generations_are_sealed_at_vstas_accept(self):
        starts, _ = self._run_fixture_logs(
            "vector_align_generations_are_sealed_at_vstas_accept.json"
        )
        vstus_starts = [item["cy"] for item in starts if item["op"] == "VSTUS"]
        vstas_starts = [item["cy"] for item in starts if item["op"] == "VSTAS"]
        self.assertEqual(len(vstus_starts), 2)
        self.assertEqual(len(vstas_starts), 2)
        self.assertEqual(vstas_starts[0], vstus_starts[0] + 1)
        self.assertGreaterEqual(vstas_starts[1], vstus_starts[1] + 1)

    def test_partial_compatible_form_params_inherit_missing_fields(self):
        db = ParamDB(base_dir=str(ROOT))
        db._insts["VPARTIAL"] = {
            "op_class": "COMPUTE",
            "forms": {
                "fp16": {
                    "latency": 7,
                    "throughput": 2,
                    "data_load_cost": 9,
                    "data_store_cost": 9,
                    "EXU": "ALU",
                    "dispatch_exu": "EXU01",
                },
                "b16": {"latency": 123},
            },
        }
        params = db.get_inst_form("VPARTIAL", form="b16", dtype="bf16")
        self.assertEqual(params["form"], "b16")
        self.assertEqual(params["resolved_form"], "b16")
        self.assertEqual(params["latency"], 123)
        self.assertEqual(params["throughput"], 2)
        self.assertEqual(params["dispatch_exu"], "EXU01")

    def test_cli_writes_instruction_fallback_warnings_without_vreg_warning(self):
        trace = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/cli_writes_instruction_fallback_warnings_without_vreg_warning.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "unknown_trace.json"
            out_dir = tmp / "out"
            trace_path.write_text(
                json.dumps(canonical_vf_info_to_dict(trace)), encoding="utf-8"
            )
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            warnings_path = out_dir / "model_warnings.json"
            payload = json.loads(warnings_path.read_text(encoding="utf-8"))
        self.assertNotIn("vreg_capacity_warnings", payload)
        self.assertTrue(payload["instruction_fallback_warnings"])

    def _run_fixture_logs(self, filename, include_history=False):
        vf_info = InputAPI.load_json(
            ROOT / "VFtest/canonical/fixtures/timing" / filename
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_vf_info(vf_info)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            history = json.loads((out_dir / "sim_history.json").read_text())
        return (starts, dones, history) if include_history else (starts, dones)

    def test_vst_vld_membar_blocks_following_load_until_prior_store_done(self):
        starts, dones = self._run_fixture_logs(
            "vst_vld_membar_blocks_following_load_until_prior_store_done.json"
        )
        store_done = next((item["cy"] for item in dones if item["op"] == "VSTS"))
        post_barrier_load_start = [
            item["cy"] for item in starts if item["op"] == "VLDS"
        ][-1]
        self.assertGreaterEqual(post_barrier_load_start, store_done + 1)

    def test_same_ub_without_membar_does_not_create_implicit_dependency(self):
        starts, dones = self._run_fixture_logs(
            "same_ub_without_membar_does_not_create_implicit_dependency.json"
        )
        store_done = next((item["cy"] for item in dones if item["op"] == "VSTS"))
        following_load_start = [item["cy"] for item in starts if item["op"] == "VLDS"][
            -1
        ]
        self.assertLess(following_load_start, store_done)

    def test_vld_vst_membar_blocks_following_store_until_prior_load_done(self):
        starts, dones = self._run_fixture_logs(
            "vld_vst_membar_blocks_following_store_until_prior_load_done.json"
        )
        load_done = next((item["cy"] for item in dones if item["op"] == "VLDS"))
        store_start = next((item["cy"] for item in starts if item["op"] == "VSTS"))
        self.assertGreaterEqual(store_start, load_done + 1)
        self.assertEqual(store_start, 45)

    def test_vst_vld_membar_does_not_directly_block_compute(self):
        starts, dones, history = self._run_fixture_logs(
            "vst_vld_membar_does_not_directly_block_compute.json", include_history=True
        )
        store_done = next((item["cy"] for item in dones if item["op"] == "VSTS"))
        compute_start = next((item["cy"] for item in starts if item["op"] == "VADDS"))
        post_barrier_load_start = [
            item["cy"] for item in starts if item["op"] == "VLDS"
        ][-1]
        self.assertGreaterEqual(post_barrier_load_start, store_done)
        self.assertLess(compute_start, store_done)
        self.assertTrue(
            any(
                (
                    item.get("event") == "blocked"
                    and item.get("op") == "VLDS"
                    and (item.get("state") == "ready")
                    and (item.get("blocked_reason") == "membar")
                    for item in history
                )
            )
        )

    def test_loop_membar_uses_dynamic_stream_sequence_not_static_pc(self):
        payload = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/loop_membar_uses_dynamic_stream_sequence_not_static_pc.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir)
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_vf_info(payload)
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
            dones = [
                json.loads(line)
                for line in (out_dir / "done_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
        first_post_barrier_load_start = next(
            (item["cy"] for item in starts if item["inst_id"] == 2)
        )
        second_store_done = next((item["cy"] for item in dones if item["inst_id"] == 4))
        self.assertLess(first_post_barrier_load_start, second_store_done)

    def test_membar_disables_innermost_unroll_with_warning(self):
        trace = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/membar_disables_innermost_unroll_with_warning.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "membar_unroll.json"
            out_dir = tmp / "out"
            trace_path.write_text(
                json.dumps(canonical_vf_info_to_dict(trace)), encoding="utf-8"
            )
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            warnings_payload = json.loads(
                (out_dir / "model_warnings.json").read_text(encoding="utf-8")
            )
            starts = [
                json.loads(line)
                for line in (out_dir / "start_by_cycle.json").read_text().splitlines()
                if line.strip()
            ]
        warning = next(
            (
                item
                for item in warnings_payload["instruction_fallback_warnings"]
                if item["kind"] == "membar_unroll_disabled"
            )
        )
        self.assertEqual(warning["barrier"], "VST_VLD")
        self.assertEqual(warning["loop_id"], 0)
        self.assertEqual(warning["requested_unroll"], 2)
        self.assertEqual(warning["used_unroll"], 1)
        self.assertEqual(warning["reason"], "membar_in_unrolled_innermost_loop")
        touched_values = []
        for item in starts:
            touched_values.extend(item.get("src", []))
            touched_values.extend(item.get("dst", []))
        self.assertFalse(any(("_lane" in value for value in touched_values)))

    def test_unsupported_membar_type_writes_warning(self):
        trace = InputAPI.load_json(
            ROOT
            / "VFtest/canonical/fixtures/timing/unsupported_membar_type_writes_warning.json"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            trace_path = tmp / "unsupported_membar.json"
            out_dir = tmp / "out"
            trace_path.write_text(
                json.dumps(canonical_vf_info_to_dict(trace)), encoding="utf-8"
            )
            subprocess.run(
                [
                    "python3",
                    str(ROOT / "main.py"),
                    "--trace",
                    str(trace_path),
                    "--out_dir",
                    str(out_dir),
                ],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            payload = json.loads(
                (out_dir / "model_warnings.json").read_text(encoding="utf-8")
            )
        kinds = {
            warning["kind"] for warning in payload["instruction_fallback_warnings"]
        }
        self.assertIn("unmeasured_membar_timing", kinds)


if __name__ == "__main__":
    unittest.main()
