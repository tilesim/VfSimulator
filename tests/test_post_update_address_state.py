import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import (AffineExpression, CoreLoweringPass, canonical_vf_info_from_dict,
                          canonical_vf_info_to_dict, validate_canonical_vf_info)
from api.simulator_costmodel import CoreVfCostModel
from core.address_state import AddressStateTracker
from core.idu import IDU
from core.ooo_mainline import OoOCoreMainline
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def parse(body, params="__ubuf__ float *p, __ubuf__ float *q"):
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "input.cce"
        source.write_text(f"void vf({params}) {{ __VEC_SCOPE__ {{"
                          "vector_f32 a, b, c; vector_bool mask = pset_b32(PAT_ALL);"
                          + body + "}}")
        return parse_cce_canonical_vf_info(source)


def access(state="p", update=True):
    return {"address_state_id": state, "update_mode": "post_update" if update else "none"}


class PostUpdateTests(unittest.TestCase):
    def idu(self, latency=1, **overrides):
        db = ParamDB(base_dir=str(ROOT))
        uarch = {**db.get_uarch(), "idu_post_update_ready_latency": latency, **overrides}
        return IDU(uarch, db), OoOCoreMainline(uarch, db)

    def inst(self, i, state="p", update=True, op="VLDS"):
        return {"inst_id": i, "stream_seq": i, "op": op, "form": "fp32",
                "src": [], "dst": [], "memory_accesses": [access(state, update)]}

    def test_same_pointer_hol_and_repeated_dispatch(self):
        for latency in (1, 3):
            idu, core = self.idu(latency)
            first, second, other = self.inst(0), self.inst(1, update=False), self.inst(2, "q")
            for inst in (first, second, other):
                idu.accept(inst)
            self.assertEqual(idu.dispatch(100, core), [first])
            self.assertEqual(idu.dispatch(100, core), [])
            if latency > 1:
                self.assertEqual(idu.dispatch(102, core), [])
            self.assertEqual(idu.dispatch(100 + latency, core), [second, other])
            self.assertEqual(idu.address_block_log[0]["address_dependencies"][0],
                             {"address_state_id": "p", "producer_inst_id": 0,
                              "producer_dispatch_cycle": 100, "ready_cycle": 100 + latency})

    def test_independent_pointers_and_read_only_dispatch(self):
        for state, update in (("q", True), ("p", False)):
            idu, core = self.idu()
            for i, s in enumerate(("p", state)):
                idu.accept(self.inst(i, s, update))
            self.assertEqual(len(idu.dispatch(100, core)), 2)

    def test_address_head_of_line_blocks_independent_compute(self):
        idu, core = self.idu()
        for inst in (self.inst(0), self.inst(1), self.inst(2, "q", op="VADD")):
            idu.accept(inst)
        self.assertEqual([i["inst_id"] for i in idu.dispatch(100, core)], [0])
        self.assertEqual([i["inst_id"] for i in idu.dispatch(101, core)], [1, 2])

    def test_zero_update_and_past_producer_dispatch(self):
        idu, core = self.idu(3)
        first = self.inst(0)
        first["memory_accesses"][0]["post_update_delta_bytes"] = {"constant": 0, "terms": []}
        idu.accept(first)
        idu.dispatch(100, core)
        # Producer need not be alive in any backend queue.
        idu.accept(self.inst(1))
        self.assertEqual(idu.dispatch(102, core), [])
        self.assertEqual(len(idu.dispatch(103, core)), 1)

    def test_failed_resource_check_does_not_publish_update(self):
        idu, core = self.idu()
        inst = self.inst(0)
        inst["dst"] = ["V0"]
        idu.accept(inst)
        original = core.get_free_preg
        core.get_free_preg = lambda: 0
        self.assertEqual(idu.dispatch(100, core), [])
        self.assertEqual(idu.address_states.updates, {})
        core.get_free_preg = original
        self.assertEqual(idu.dispatch(101, core), [inst])
        self.assertEqual(idu.address_states.updates["p"]["producer_dispatch_cycle"], 101)

    def test_backlogged_loads_dual_issue_without_lsu_spacing(self):
        idu, core = self.idu(3)
        idu.accept(self.inst(0))
        idu.accept(self.inst(1))
        for cycle in (100, 103):
            inst = idu.dispatch(cycle, core)[0]
            # Deliberately delay accept, then hold both in the backend.
            core.cycle = cycle + 7
            core.accept(inst)
            core.LSQ[-1].state = "ready"
        core._blocked_by_control_unit = lambda u: True
        core._issue_ready_lsu(120, 0, 0, 0)
        self.assertTrue(all(u.start_cycle is None for u in core.LSQ))
        core._blocked_by_control_unit = lambda u: False
        core._issue_ready_lsu(121, 0, 0, 0)
        self.assertEqual([u.start_cycle for u in core.ROB], [121, 121])
        self.assertEqual([r["cy"] for r in idu.dispatch_log], [100, 103])

    def test_reader_and_data_blocked_store_do_not_gate_address_dispatch(self):
        for update in (False, True):
            idu, core = self.idu()
            idu.accept(self.inst(0, update=update, op="VSTS"))
            idu.accept(self.inst(1))
            sent = idu.dispatch(100, core)
            self.assertEqual(len(sent), 1 if update else 2)
            core.cycle = 100
            core.accept(sent[0])
            self.assertIsNone(core.LSQ[0].start_cycle)
            if update:
                self.assertEqual(len(idu.dispatch(101, core)), 1)

    def test_all_address_states_and_bounded_tracker(self):
        tracker = AddressStateTracker(3)
        tracker.notify_dispatch(0, [access("p")], 100)
        self.assertFalse(tracker.can_dispatch([access("q"), access("p", False)], 102))
        self.assertTrue(tracker.can_dispatch([access("q"), access("p", False)], 103))
        for i in range(1000):
            tracker.notify_dispatch(i, [access("p")], 200 + i * 3)
            tracker.notify_dispatch(i, [access("q", False)], 200 + i * 3)
        self.assertEqual(len(tracker.updates), 1)
        self.assertEqual(AddressStateTracker(1).updates, {})

    def test_old_config_rejected_at_public_and_file_boundaries(self):
        vf = replace(parse("vlds(a,p,1,NORM,POST_UPDATE);"),
                     uarch={"lsu_post_update_ready_latency": 1})
        self.assertIn("deprecated_uarch_field",
                      [d.code for d in validate_canonical_vf_info(vf).errors])
        with self.assertRaisesRegex(ValueError, "was removed"):
            self.idu(lsu_post_update_ready_latency=1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uarch.json"
            path.write_text(json.dumps({"lsu_post_update_ready_latency": 1}))
            with self.assertRaisesRegex(ValueError, "was removed"):
                ParamDB(base_dir=str(ROOT), uarch_path=str(path))

    def test_frontend_delta_identity_roundtrip_and_cast(self):
        vf = parse("__ubuf__ float *p0 = p + 4; __ubuf__ float *p1 = p0;"
                   "vlds(a,p0,64,NORM,POST_UPDATE);vlds(b,p1,0,NORM);"
                   "vlds(c,((__ubuf__ half *&)p0),1,BRC_B16,POST_UPDATE);")
        first, second, cast = [n.inputs[0].memory_access for n in vf.context]
        self.assertEqual(first.offset.constant, 4)
        self.assertEqual(first.post_update_delta_bytes.constant, 256)
        self.assertEqual(first.base_object_id, second.base_object_id)
        self.assertNotEqual(first.address_state_id, second.address_state_id)
        self.assertEqual(first.address_state_id, cast.address_state_id)
        self.assertEqual(cast.post_update_delta_bytes.constant, 2)
        encoded = canonical_vf_info_to_dict(vf)
        self.assertEqual(canonical_vf_info_from_dict(encoded), vf)
        lowered = CoreLoweringPass().lower(vf)["program"][0]["memory_accesses"][0]
        self.assertEqual(lowered["address_state_id"], first.address_state_id)
        self.assertEqual(lowered["post_update_delta_bytes"]["constant"], 256)

    def test_bfloat_width_and_zero_update_are_explicit(self):
        vf = parse("vlds(a,p,0,BRC_B16,POST_UPDATE);vlds(b,p,1,BRC_B16,POST_UPDATE);",
                   "__ubuf__ bfloat16_t *p")
        accesses = [n.inputs[0].memory_access for n in vf.context]
        self.assertEqual([a.post_update_delta_bytes.constant for a in accesses], [0, 2])
        self.assertTrue(all(a.update_mode == "post_update" for a in accesses))

    def test_reject_alias_after_outer_pointer_updated_in_loop(self):
        with self.assertRaisesRegex(ValueError, "snapshot an updated pointer"):
            parse("for(int i=0;i<2;++i){vlds(a,p,1,BRC_B32,POST_UPDATE);}"
                  "__ubuf__ float *p1=p;vlds(b,p1,0,NORM);")

    def test_reject_loop_local_update_and_vag_update(self):
        with self.assertRaisesRegex(ValueError, "loop-local"):
            parse("for(int i=0;i<2;++i){__ubuf__ float *p0=p;"
                  "vlds(a,p0,1,NORM,POST_UPDATE);}")
        with self.assertRaisesRegex(ValueError, "VAG"):
            parse("vlds(a,p,vag_b32(256),NORM,POST_UPDATE);")
        with self.assertRaisesRegex(ValueError, "loop-local alias"):
            parse("for(int i=0;i<2;++i){__ubuf__ float *r=p;"
                  "vlds(a,r,0,NORM);vlds(b,p,64,NORM,POST_UPDATE);}")

    def test_invalid_canonical_metadata(self):
        vf = parse("vlds(a,p,1,NORM,POST_UPDATE);")
        node = vf.context[0]
        operand = node.inputs[0]
        for changes, code in (({"address_state_id": None}, "invalid_address_update"),
                              ({"post_update_delta_bytes": None}, "invalid_address_update"),
                              ({"update_mode": "none"}, "invalid_address_update")):
            with self.subTest(changes=changes):
                memory = replace(operand.memory_access, **changes)
                changed = replace(vf, context=(replace(node, inputs=(replace(operand, memory_access=memory),)),))
                result = validate_canonical_vf_info(changed)
                self.assertFalse(result.ok)
                self.assertIn(code, [error.code for error in result.errors])

    def test_delta_affine_validation(self):
        vf = parse("vlds(a,p,1,NORM,POST_UPDATE);")
        node = vf.context[0]
        operand = node.inputs[0]
        for delta in (AffineExpression(True), AffineExpression(2**63), "bad"):
            memory = replace(operand.memory_access, post_update_delta_bytes=delta)
            changed = replace(vf, context=(replace(node, inputs=(replace(operand, memory_access=memory),)),))
            self.assertFalse(validate_canonical_vf_info(changed).ok)

    def test_latency_strict_positive_integer(self):
        for value in (0, -1, True, "1", 1.0, None, 2**63):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.idu(value)

    @unittest.skipUnless(RUNNER, "set VFSIM_NATIVE_RUNNER for parity tests")
    def test_native_config_migration_and_invalid_latency(self):
        vf = parse("vlds(a,p,1,NORM,POST_UPDATE);")
        for key, value in (("lsu_post_update_ready_latency", 1),
                           ("idu_post_update_ready_latency", 0),
                           ("idu_post_update_ready_latency", True),
                           ("idu_post_update_ready_latency", "1")):
            with self.subTest(key=key, value=value), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "input.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(
                    replace(vf, uarch={key: value}))))
                proc = subprocess.run([RUNNER, "--trace", str(path)], capture_output=True,
                                      text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(key, proc.stderr)
                config = Path(tmp) / "uarch.json"
                config.write_text(json.dumps({key: value}))
                with self.assertRaises(ValueError):
                    ParamDB(base_dir=str(ROOT), uarch_path=str(config))
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc = subprocess.run([RUNNER, "--trace", str(path)], capture_output=True,
                                      text=True, timeout=30,
                                      env={**os.environ, "UARCH_JSON_PATH": str(config)})
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(key, proc.stderr)

    @unittest.skipUnless(RUNNER, "set VFSIM_NATIVE_RUNNER for parity tests")
    def test_python_native_dynamic_parity(self):
        load = "vlds(a,p,64,NORM,POST_UPDATE);"
        compute = "vadds(b,a,1.0f,mask);"
        store = "vsts(b,p,64,NORM_B32,mask,POST_UPDATE);"
        cases = [
            load * 3,
            "__ubuf__ float *r=p;" + load + "vlds(b,r,64,NORM,POST_UPDATE);",
            "vlds(a,p,0,NORM);vlds(b,p,0,NORM);",
            load + "vlds(b,p,0,NORM);",
            load + compute + store + load,
            load + compute + "mem_bar(VLD_VST);" + store + "mem_bar(VST_VLD);" + load,
        ]
        for unroll in (1, 2, 4):
            cases.append(f"#pragma unroll({unroll})\nfor(int i=0;i<8;++i){{"
                         + load + "vlds(c,q,64,NORM,POST_UPDATE);" + compute + store + "}")
        cases.append("for(int j=0;j<2;++j){for(int i=0;i<2;++i){" + load + "}}" + load)
        cases.append("vector_align state;vlds(a,q,0,NORM);"
                     "vcmax(b,a,mask,MODE_ZEROING);vstus(state,1,b,p,POST_UPDATE);"
                     "vstas(state,p,0,POST_UPDATE);vlds(c,p,0,BRC_B32);")
        for body in cases:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                vf = replace(parse(body), uarch={"idu_post_update_ready_latency": 3,
                                                "idu_to_ooo_delay": 7})
                path = root / "input.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                py, cpp = root / "py", root / "cpp"
                result = CoreVfCostModel(base_dir=ROOT, out_dir=py).run_vf_info(vf)
                proc = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", str(cpp),
                                       "--max-cycles", "10000"], check=True,
                                      text=True, capture_output=True, timeout=30)
                fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
                self.assertEqual(result["vf_end_cycle"], int(fields["vfEndCycle"]))
                def starts(folder):
                    rows = json.loads((folder / "sim_history.json").read_text())
                    return sorted((r["stream_seq"], r["static_instruction_id"],
                                   [(p["loop_id"], p["iteration"]) for p in r["iteration_path"]],
                                   r["start"], r["done"])
                                  for r in rows if r["event"] == "start")
                self.assertEqual(starts(py), starts(cpp))
                for filename in ("idu_to_ooo.json", "idu_address_blocked.json"):
                    def idu_records(folder):
                        records = [json.loads(line) for line in (folder / filename).read_text().splitlines()]
                        return [(r["cy"], r["event"], r["inst_id"], r["stream_seq"],
                                 r.get("blocked_reason"), r["address_dependencies"]) for r in records]
                    self.assertEqual(idu_records(py), idu_records(cpp))

    @unittest.skipUnless(RUNNER, "set VFSIM_NATIVE_RUNNER for parity tests")
    def test_native_rejects_invalid_update_metadata(self):
        vf = parse("vlds(a,p,1,NORM,POST_UPDATE);")
        for field, value in (("address_state_id", None), ("post_update_delta_bytes", None),
                             ("update_mode", "none")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                payload = canonical_vf_info_to_dict(vf)
                payload["context"][0]["inputs"][0]["memory_access"][field] = value
                path = Path(tmp) / "input.json"
                path.write_text(json.dumps(payload))
                result = subprocess.run([RUNNER, "--trace", str(path)],
                                        capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("invalid_address_update", result.stderr)
