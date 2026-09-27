import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.simulator_costmodel import CoreVfCostModel
from tests.test_post_update_address_state import parse
from tests import test_post_update_address_state as address_tests


RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


class DeinterleaveLoadTest(unittest.TestCase):
    def test_multi_load_bandwidth_and_native_parity(self):
        vf = parse("vector_f32 d;vlds(a,b,p,0,DINTLV_B32);"
                   "vlds(c,d,p,128,DINTLV_B32);"
                   "vsts(a,q,0,NORM_B32,mask);vsts(b,q,64,NORM_B32,mask);"
                   "vsts(c,q,128,NORM_B32,mask);vsts(d,q,192,NORM_B32,mask);")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            CoreVfCostModel(out_dir=str(root / "python")).run_vf_info(vf)
            outputs = [root / "python"]
            if RUNNER:
                source = root / "program.json"
                source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc = subprocess.run([RUNNER, "--trace", str(source), "--out-dir", str(root / "native")],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                outputs.append(root / "native")
            timelines = []
            for output in outputs:
                rows = [json.loads(line) for line in (output / "start_by_cycle.json").read_text().splitlines()]
                usage = {}
                for r in rows:
                    if r["op"] not in ("VLDS", "VSTS"):
                        continue
                    usage[r["cy"]] = usage.get(r["cy"], 0) + (512 if r["op"] == "VLDS" else 256)
                self.assertTrue(all(n <= 512 for n in usage.values()))
                starts = sorted(r["cy"] for r in rows if r["op"] == "VLDS")
                self.assertEqual(starts[1] - starts[0], 1)
                timelines.append(sorted((r["inst_id"], r["cy"]) for r in rows))
            if RUNNER:
                self.assertEqual(timelines[0], timelines[1])

    def program(self, update=False):
        call = "vlds(a,b,p,128,DINTLV_B32,POST_UPDATE);" if update else "vlds(a,b,p,8,DINTLV_B32);"
        return parse(call + "vsts(a,q,0,NORM_B32,mask);vsts(b,q,64,NORM_B32,mask);")

    def test_two_definitions_one_load_and_memory_span(self):
        vf = self.program()
        load = vf.context[1]
        self.assertEqual(load.opcode, "VLDS")
        self.assertEqual(load.attributes["catalog_mode"], "DINTLV_B32")
        self.assertEqual(len(load.outputs), 2)
        self.assertEqual(load.inputs[0].memory_access.span, 128)
        self.assertEqual(load.inputs[0].memory_access.offset.constant, 8)
        for index, output in enumerate(load.outputs):
            self.assertEqual(vf.values[output.value_id].producer_node_id, load.instruction_id)
            self.assertEqual(vf.context[index + 2].inputs[0].value_id, output.value_id)

    def test_post_update_not_confused_with_single_result(self):
        vf = self.program(True)
        access = vf.context[1].inputs[0].memory_access
        self.assertEqual(access.offset.constant, 0)
        self.assertEqual(access.post_update_delta_bytes.constant, 512)
        single = parse("vlds(a,p,64,NORM,POST_UPDATE);")
        self.assertEqual(len(single.context[1].outputs), 1)
        self.assertNotIn("catalog_mode", single.context[1].attributes)

    def test_invalid_calls_and_missing_mode_fail_closed(self):
        for call in ("vlds(a,p,0,DINTLV_B32);", "vlds(a,a,p,0,DINTLV_B32);",
                     "vlds(a,b,p,0,NORM);", "vlds(a,b,p,0,DINTLV_B32,INVALID);"):
            with self.subTest(call=call), self.assertRaises(ValueError):
                parse(call)
        vf = self.program()
        load = replace(vf.context[1], attributes={})
        invalid = replace(vf, context=(vf.context[0], load, *vf.context[2:]))
        self.assertFalse(validate_canonical_vf_info(invalid).ok)

    def test_atomic_two_register_credit(self):
        idu, core = address_tests.PostUpdateTests().idu()
        inst = {"inst_id": 0, "stream_seq": 0, "op": "VLDS", "form": "fp32",
                "src": [], "dst": ["V0", "V1"]}
        idu.accept(inst)
        original = core.get_free_preg
        core.get_free_preg = lambda: 1
        self.assertEqual(idu.dispatch(100, core), [])
        core.get_free_preg = original
        self.assertEqual(idu.dispatch(101, core), [inst])

    def test_loop_destinations_recycle_independently(self):
        for unroll in (1, 2):
            vf = parse(f"\n#pragma unroll({unroll})\nfor(int i=0;i<8;++i){{"
                       "vlds(a,b,p,128,DINTLV_B32,POST_UPDATE);"
                       "vsts(a,q,128*i,NORM_B32,mask);"
                       "vsts(b,q,128*i+64,NORM_B32,mask);}")
            vf = replace(vf, uarch={"vreg_num": 4})
            with tempfile.TemporaryDirectory() as tmp:
                result = CoreVfCostModel(out_dir=tmp).run_vf_info(vf)
                self.assertLess(result["vf_end_cycle"], 1000)
                rows = [json.loads(line) for line in (Path(tmp) / "start_by_cycle.json").read_text().splitlines()]
                loads = [r for r in rows if r["op"] == "VLDS"]
                self.assertEqual(len(loads), 8)
                self.assertTrue(all(len(set(r["preg_dst"])) == 2 for r in loads))

    def test_execution_and_native_parity(self):
        for update in (False, True):
            vf = self.program(update)
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                py = root / "python"
                result = CoreVfCostModel(out_dir=str(py)).run_vf_info(vf)
                rows = [json.loads(line) for line in (py / "start_by_cycle.json").read_text().splitlines()]
                load = next(r for r in rows if r["op"] == "VLDS")
                self.assertEqual(len(load["preg_dst"]), 2)
                stores = sorted((r for r in rows if r["op"] == "VSTS"), key=lambda r:r["inst_id"])
                self.assertEqual([r["preg_src"][0] for r in stores], load["preg_dst"])
                self.assertLess(result["vf_end_cycle"], 1000)
                if RUNNER:
                    source = root / "program.json"
                    source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                    out = root / "native"
                    proc = subprocess.run([RUNNER, "--trace", str(source), "--out-dir", str(out),
                                           "--max-cycles", "1000"], capture_output=True, text=True)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                    native = [json.loads(line) for line in (out / "start_by_cycle.json").read_text().splitlines()]
                    timings = lambda rs: sorted((r["inst_id"], r["op"], r["cy"]) for r in rs)
                    self.assertEqual(timings(rows), timings(native))

    @unittest.skipUnless(RUNNER, "Set VFSIM_NATIVE_RUNNER for cross-language validation")
    def test_native_and_python_reject_invalid_mode_contract(self):
        vf = self.program()
        load = vf.context[1]
        memory = load.inputs[0]
        cases = [
            (replace(load, attributes={"catalog_mode": "UNKNOWN"}), "unsupported_catalog_mode"),
            (replace(load, outputs=load.outputs[:1]), "catalog_operand_count_mismatch"),
            (replace(load, inputs=(replace(memory, memory_access=replace(memory.memory_access, span=64)),)),
             "catalog_memory_span_mismatch"),
        ]
        for bad_load, code in cases:
            invalid = replace(vf, context=(vf.context[0], bad_load, *vf.context[2:]))
            self.assertIn(code, [d.code for d in validate_canonical_vf_info(invalid).errors])
            with tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "invalid.json"
                source.write_text(json.dumps(canonical_vf_info_to_dict(invalid)))
                proc = subprocess.run([RUNNER, "--trace", str(source)], capture_output=True, text=True)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn(code, proc.stderr)
