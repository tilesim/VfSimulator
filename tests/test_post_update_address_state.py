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
    def core(self, latency=1):
        db = ParamDB(base_dir=str(ROOT))
        core = OoOCoreMainline({**db.get_uarch(), "lsu_post_update_ready_latency": latency}, db)
        core.cycle = 100
        return core

    def accept(self, core, i, state="p", update=True, op="VLDS", ready=True):
        core.accept({"inst_id": i, "stream_seq": i, "op": op, "form": "fp32",
                     "src": [], "dst": [], "memory_accesses": [access(state, update)]})
        u = core.LSQ[-1]
        u.state = "ready" if ready else "blocked"
        u.store_dependencies_resolved = True
        return u

    def test_same_pointer_raw_and_repeated_issue_call(self):
        for latency in (1, 3):
            core = self.core(latency)
            first = self.accept(core, 0)
            second = self.accept(core, 1, update=False)
            budget = core._issue_ready_lsu(100, 0, 0, 0)
            core._issue_ready_lsu(100, *budget)
            self.assertEqual(first.start_cycle, 100)
            self.assertIsNone(second.start_cycle)
            core._issue_ready_lsu(100 + latency, 0, 0, 0)
            self.assertEqual(second.start_cycle, 100 + latency)

    def test_independent_pointers_and_read_only_dual_issue(self):
        for state, update in (("q", True), ("p", False)):
            core = self.core()
            first = self.accept(core, 0, update=update)
            second = self.accept(core, 1, state=state, update=update)
            core._issue_ready_lsu(100, 0, 0, 0)
            self.assertEqual((first.start_cycle, second.start_cycle), (100, 100))

    def test_store_update_cannot_be_bypassed_by_load_priority(self):
        core = self.core()
        store = self.accept(core, 0, op="VSTS", ready=False)
        load = self.accept(core, 1)
        unrelated = self.accept(core, 2, state="q")
        core._issue_ready_lsu(100, 0, 0, 0)
        self.assertIsNone(load.start_cycle)
        self.assertEqual(unrelated.start_cycle, 100)
        store.state = "ready"
        budget = core._issue_ready_lsu(101, 0, 0, 0)
        core._issue_ready_lsu(101, *budget)
        self.assertEqual(store.start_cycle, 101)
        self.assertIsNone(load.start_cycle)
        core._issue_ready_lsu(102, 0, 0, 0)
        self.assertEqual(load.start_cycle, 102)

    def test_war_waits_for_reader_start_without_extra_delay(self):
        core = self.core()
        reader = self.accept(core, 0, update=False, op="VSTS", ready=False)
        updater = self.accept(core, 1)
        core._issue_ready_lsu(100, 0, 0, 0)
        self.assertIsNone(updater.start_cycle)
        reader.state = "ready"
        budget = core._issue_ready_lsu(101, 0, 0, 0)
        core._issue_ready_lsu(101, *budget)
        self.assertEqual((reader.start_cycle, updater.start_cycle), (101, 101))

    def test_events_survive_producer_queue_removal(self):
        core = self.core(3)
        first = self.accept(core, 0)
        core._issue_ready_lsu(100, 0, 0, 0)
        core.ROB.clear()
        del first
        second = self.accept(core, 1)
        core._issue_ready_lsu(101, 0, 0, 0)
        self.assertIsNone(second.start_cycle)
        core._issue_ready_lsu(103, 0, 0, 0)
        self.assertEqual(second.start_cycle, 103)

    def test_membar_blocks_address_producer(self):
        core = self.core()
        producer = self.accept(core, 0)
        consumer = self.accept(core, 1)
        core._blocked_by_control_unit = lambda u: u is producer
        core._issue_ready_lsu(100, 0, 0, 0)
        self.assertIsNone(consumer.start_cycle)
        core._blocked_by_control_unit = lambda u: False
        core._issue_ready_lsu(101, 0, 0, 0)
        core._issue_ready_lsu(102, 0, 0, 0)
        self.assertEqual((producer.start_cycle, consumer.start_cycle), (101, 102))

    def test_reader_records_do_not_grow_with_history(self):
        tracker = AddressStateTracker(1)
        for i in range(1000):
            binding = tracker.bind(i, [access(update=False)])
            tracker.notify_start(binding, i)
        self.assertEqual(tracker.readers["p"], [])
        self.assertEqual(tracker.updates, {})

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
                self.core(value)

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
                vf = replace(parse(body), uarch={"lsu_post_update_ready_latency": 3})
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
                                   r["start"], r["done"], r["address_dependencies"])
                                  for r in rows if r["event"] == "start")
                self.assertEqual(starts(py), starts(cpp))

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
