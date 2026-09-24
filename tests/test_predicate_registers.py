import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.simulator_costmodel import CoreVfCostModel
from core.ooo_mainline import OoOCoreMainline
from core.param_db import ParamDB
from tests import test_physical_register_bank as bank_tests


ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def parse(body):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "predicate.cce"
        path.write_text("void vf(__ubuf__ float *a, __ubuf__ float *out) {"
                        "__VEC_SCOPE__ {vector_f32 x, y; vector_bool mask, p;"
                        + body + "}}")
        return parse_cce_canonical_vf_info(path)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class PredicateReleaseContractTest(bank_tests.VectorReleaseContractTest):
    def setUp(self):
        db = ParamDB(base_dir=str(ROOT))
        self.core = OoOCoreMainline(db.get_uarch(), db)
        self.slot = self.core.predicate_bank.allocate()
        # Run the exact vector lifetime contract against the predicate bank.
        self.core.freelist = self.core.predicate_bank.freelist
        self.core.preg_generation[self.slot] = 1


class PredicateRegisterTest(unittest.TestCase):
    def run_program(self, vf):
        with tempfile.TemporaryDirectory() as tmp:
            result = CoreVfCostModel(out_dir=tmp).run_vf_info(vf)
            starts = records(Path(tmp) / "start_by_cycle.json")
            history = json.loads((Path(tmp) / "sim_history.json").read_text())
            for row in records(Path(tmp) / "idu_resource_blocked.json"):
                self.assertGreaterEqual(row["predicate_phys_free"], 0)
        self.assertLess(result["vf_end_cycle"], 10000)
        return result, starts, history

    def test_pset_compare_select_and_store_are_real_dependencies(self):
        vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                   "vcmp_gt(p,x,x,mask);vsel(y,x,x,p);"
                   "vsts(y,out,0,NORM_B32,mask);")
        self.assertEqual(vf.schema_version, 2)
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        _, starts, _ = self.run_program(vf)
        by_op = {r["op"]: r for r in starts}
        self.assertTrue(by_op["PSET_B32"]["preg_dst"][0].startswith("pred"))
        self.assertTrue(by_op["VCMP_GT"]["preg_dst"][0].startswith("pred"))
        self.assertEqual(by_op["VCMP_GT"]["preg_src"][-1], by_op["PSET_B32"]["preg_dst"][0])
        self.assertEqual(by_op["VSEL"]["preg_src"][-1], by_op["VCMP_GT"]["preg_dst"][0])
        self.assertEqual(by_op["VSTS"]["preg_src"][-1], by_op["PSET_B32"]["preg_dst"][0])
        self.assertGreater(by_op["VSEL"]["cy"], by_op["VCMP_GT"]["cy"])

    def test_small_predicate_pool_recycles_in_unrolled_loops(self):
        for unroll in (1, 2, 4):
            vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                       f"\n#pragma unroll({unroll})\n"
                       "for(int i=0;i<16;++i){vcmp_gt(p,x,x,mask);"
                       "vsel(y,x,x,p);vsts(y,out,64*i,NORM_B32,mask);}")
            capacity = unroll + 1  # All unrolled compare results plus invariant mask.
            vf = replace(vf, uarch={"physical_predicate_registers": capacity})
            _, starts, history = self.run_program(vf)
            compares = [r for r in starts if r["op"] == "VCMP_GT"]
            self.assertEqual(len(compares), 16)
            self.assertLessEqual(len({r["preg_dst"][0] for r in compares}), capacity)
            self.assertTrue(all(0 <= r["predicate_phys_free"] <= capacity for r in history))

    def test_predicate_loop_carried_alias_and_zero_loop(self):
        for count in (0, 1, 3):
            vf = parse("mask=pset_b32(PAT_ALL);p=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                       f"for(int i=0;i<{count};++i){{vector_bool q;vcmp_gt(q,x,x,p);p=q;}}"
                       "vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);")
            _, starts, _ = self.run_program(vf)
            producer = [r for r in starts if r["op"] == "PSET_B32"][-1]["preg_dst"][0]
            for row in sorted((r for r in starts if r["op"] == "VCMP_GT"), key=lambda r:r["stream_seq"]):
                self.assertEqual(row["preg_src"][-1], producer)
                producer = row["preg_dst"][0]
            self.assertEqual(next(r for r in starts if r["op"] == "VSEL")["preg_src"][-1], producer)

    def test_semantic_errors_fail_closed(self):
        for body, message in (
            ("vsel(y,x,x,p);", "predicate_live_in_not_supported"),
            ("mask=pset_b32(PAT_ALL);vunknown(y,x,mask);", "Predicate"),
            ("mask=pset_b32(PAT_ALL);vadd(y,x,x,mask,MODE_MERGING);", "MODE_MERGING"),
            ("mask=pset_b32(PAT_ALL);vdup(y,mask,mask,MODE_ZEROING);", "scalar"),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(ValueError, message):
                parse(body)

    def test_pset_forms_nested_loops_and_predicate_capacity(self):
        for bits in (8, 16, 32):
            vf = parse(f"mask=pset_b{bits}(PAT_ALL);p=pset_b{bits}(PAT_ALL);"
                       "vlds(x,a,0,NORM);for(int i=0;i<3;++i){"
                       "for(int j=0;j<2;++j){vcmp_gt(p,x,x,p);}}"
                       "vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);")
            _, starts, _ = self.run_program(vf)
            compares = sorted((r for r in starts if r["op"] == "VCMP_GT"), key=lambda r:r["stream_seq"])
            self.assertEqual(len(compares), 6)
            for previous, current in zip(compares, compares[1:]):
                self.assertEqual(current["preg_src"][-1], previous["preg_dst"][0])
        for capacity in (0, -1, True, "32", 2**31):
            self.assertFalse(validate_canonical_vf_info(replace(
                vf, uarch={"physical_predicate_registers": capacity})).ok)

    def test_missing_timing_warns_without_losing_predicate_semantics(self):
        vf = parse("mask=pset_b8(PAT_ALL);vcmps_gt(p,x,1.0f,mask);vsel(y,x,x,p);")
        with tempfile.TemporaryDirectory() as tmp:
            CoreVfCostModel(out_dir=tmp).run_vf_info(vf)
            warnings = json.loads((Path(tmp) / "model_warnings.json").read_text())
        self.assertTrue(warnings["instruction_fallback_warnings"])
        self.assertEqual(vf.values[vf.context[1].outputs[0].value_id].storage.value, "PredicateRegister")

    def test_loop_local_definitions_export_last_iteration_for_both_banks(self):
        for body, consumer, opcode in (
            ("vcmp_gt(p,x,x,mask);", "vsel(y,x,x,p);", "VCMP_GT"),
            ("vadds(y,x,1.0f,mask);", "vadd(x,y,y,mask);", "VADDS"),
        ):
            for count, unroll in ((1, 1), (4, 1), (4, 2)):
                vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                           f"\n#pragma unroll({unroll})\nfor(int i=0;i<{count};++i){{{body}}}"
                           + consumer)
                loop = vf.context[2]
                self.assertIsNone(loop.carried_values[0].entry_value_id)
                result, starts, _ = self.run_program(vf)
                producers = sorted((r for r in starts if r["op"] == opcode), key=lambda r:r["stream_seq"])
                last = max(starts, key=lambda r:r["stream_seq"])
                self.assertIn(producers[-1]["preg_dst"][0], last["preg_src"])
                if RUNNER:
                    with tempfile.TemporaryDirectory() as tmp:
                        path = Path(tmp)/"vf.json"
                        path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                        proc = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", tmp], capture_output=True, text=True)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                        native = records(Path(tmp)/"start_by_cycle.json")
                        key = lambda rows: sorted((r["inst_id"],r["cy"]) for r in rows)
                        self.assertEqual(key(native), key(starts))

    def test_first_definition_exit_nested_and_empty_loop(self):
        vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                   "for(int i=0;i<2;++i){for(int j=0;j<3;++j){vcmp_gt(p,x,x,mask);}}"
                   "vsel(y,x,x,p);")
        _, starts, _ = self.run_program(vf)
        last_compare = max((r for r in starts if r["op"] == "VCMP_GT"), key=lambda r:r["stream_seq"])
        self.assertEqual(next(r for r in starts if r["op"] == "VSEL")["preg_src"][-1], last_compare["preg_dst"][0])
        for count in ("0", "N"):
            with self.assertRaisesRegex(ValueError, "undefined|resolve"):
                parse("mask=pset_b32(PAT_ALL);"
                      f"for(int i=0;i<{count};++i){{vcmp_gt(p,x,x,mask);}}vsel(y,x,x,p);")
        # A zero-trip loop with no escaping first definition remains legal.
        self.run_program(parse("mask=pset_b32(PAT_ALL);for(int i=0;i<0;++i){vcmp_gt(p,x,x,mask);}"))
        with self.assertRaisesRegex(ValueError, "predicate_live_in_not_supported"):
            parse("for(int i=0;i<2;++i){vcmp_gt(p,x,x,p);}vsel(y,x,x,p);")

    def test_schema_versions_and_symbolic_exit_only_validation(self):
        from api.frontend import CURRENT_SCHEMA_VERSION, SUPPORTED_SCHEMA_VERSIONS
        from api.frontend.schema import CanonicalVfInfo
        self.assertEqual(CURRENT_SCHEMA_VERSION, 2)
        self.assertEqual(SUPPORTED_SCHEMA_VERSIONS, {1, 2})
        errors = validate_canonical_vf_info(CanonicalVfInfo(context=(), values={}, schema_version=99)).errors
        self.assertEqual(errors[0].context["supported_versions"], [1, 2])
        vf = parse("mask=pset_b32(PAT_ALL);for(int i=0;i<2;++i){vcmp_gt(p,x,x,mask);}vsel(y,x,x,p);")
        loop = replace(vf.context[1], count="N")
        vf = replace(vf, context=(vf.context[0], loop, vf.context[2]), params={"N": 2})
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        for params in ({"N": 0}, {}):
            self.assertIn("invalid_loop_exit_only", [d.code for d in validate_canonical_vf_info(replace(vf, params=params)).errors])

    @unittest.skipUnless(RUNNER, "Set VFSIM_NATIVE_RUNNER for cross-language parity")
    def test_native_matches_python_under_predicate_pressure(self):
        for count in (0, 1, 16):
            vf = parse("mask=pset_b32(PAT_ALL);p=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                       f"for(int i=0;i<{count};++i){{vcmp_gt(p,x,x,p);"
                       "vsel(y,x,x,p);vsts(y,out,64*i,NORM_B32,mask);}")
            vf = replace(vf, uarch={"physical_predicate_registers": 3})
            expected, starts, py_history = self.run_program(vf)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "input.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                out = Path(tmp) / "native"
                proc = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", str(out),
                                       "--max-cycles", "10000"], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"vfEndCycle={expected['vf_end_cycle']}", proc.stdout)
                native = records(out / "start_by_cycle.json")
                key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                self.assertEqual(key(native), key(starts))
                native_history = json.loads((out / "sim_history.json").read_text())
                self.assertTrue(all(0 <= row["predicate_phys_free"] <= 3 for row in native_history))
                timings = lambda rows: sorted((r["id"], r["start"], r["done"])
                                             for r in rows if r["event"] == "start")
                self.assertEqual(timings(py_history), timings(native_history))

    @unittest.skipUnless(RUNNER, "Set VFSIM_NATIVE_RUNNER for cross-language validation")
    def test_native_rejects_incomplete_predicate_semantics(self):
        vf = parse("mask=pset_b32(PAT_ALL);vadd(y,x,x,mask);")
        payload = canonical_vf_info_to_dict(vf)
        variants = []
        def changed():
            return json.loads(json.dumps(payload))
        bad = changed(); bad["schema_version"] = 1
        variants.append((bad, "predicate_requires_schema_v2"))
        bad = changed(); bad["values"][vf.context[0].outputs[0].value_id]["producer_node_id"] = None
        variants.append((bad, "predicate_live_in_not_supported"))
        bad = changed(); bad["context"][1]["opcode"] = "UNKNOWN_MASK_OP"
        variants.append((bad, "unsupported_predicate_semantics"))
        bad = changed(); bad["context"][1]["attributes"] = {"mode": "MODE_MERGING"}
        variants.append((bad, "unsupported_merging_mode"))
        bad = changed(); bad["context"][1]["inputs"][-1]["role"] = "source"
        variants.append((bad, "predicate_operand_role_mismatch"))
        for bad, code in variants:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "invalid.json"
                path.write_text(json.dumps(bad))
                proc = subprocess.run([RUNNER, "--trace", str(path)], capture_output=True, text=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(code, proc.stderr)
