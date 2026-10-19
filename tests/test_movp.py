import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.json.serialization import canonical_vf_info_to_dict
from api.common import validate_canonical_vf_info
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel
from core.params.database import ParamDB


ROOT = Path(__file__).resolve().parents[1]


def parse(body, width=32):
    ctype, vector = ("half", "f16") if width == 16 else ("float", "f32")
    with tempfile.TemporaryDirectory() as name:
        path = Path(name)/"movp.cce"
        path.write_text(f"void vf(__ubuf__ {ctype} *out) {{ __VEC_SCOPE__ {{"
                        f"vector_{vector} x,y;vector_bool mask=pset_b{width}(PAT_ALL);"
                        + body + "}}")
        return InputAPI.load_cce(path)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


class MoveMaskTest(unittest.TestCase):
    def run_core(self, vf):
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            result = CoreVfCostModel(base_dir=ROOT, out_dir=root/"python").run_vf_info(vf)
            rows = records(root/"python/start_by_cycle.json")
            runner = os.environ.get("VFSIM_NATIVE_RUNNER")
            if runner:
                source = root/"canonical.json"
                source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                process = subprocess.run([runner, "--trace", str(source), "--out-dir", str(root/"native")],
                                         capture_output=True, text=True, timeout=30)
                self.assertEqual(process.returncode, 0, process.stdout+process.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", process.stdout)
                key = lambda events: sorted((r["inst_id"], r["op"], r["cy"]) for r in events)
                self.assertEqual(key(rows), key(records(root/"native/start_by_cycle.json")))
            return rows

    def test_parameters_are_explicit_for_both_forms(self):
        db = ParamDB(base_dir=str(ROOT))
        for width in (16, 32):
            opcode, form = f"MOVP_B{width}", f"b{width}"
            dtype = "fp16" if width == 16 else "fp32"
            with self.subTest(width=width):
                profile = db.resolve_inst(opcode, form, dtype)
                self.assertEqual((profile.latency, profile.fu_type, profile.dispatch_exu), (6, "ALU", "EXU01"))
                self.assertEqual(db.get_ii_for_profiles(profile, profile), 1)
                for consumer, consumer_form, expected in (("VDUP", dtype, 2), ("VSEL", dtype, 2)):
                    self.assertEqual(db.get_forwarding_cycles(opcode, consumer, dtype,
                        producer_form=form, consumer_form=consumer_form), expected)
                forwarding = json.loads((ROOT/"configs/forwarding.json").read_text())["forwarding"]
                self.assertEqual(forwarding[f"{opcode}.{form}"]["PSTS.b8"], 4)
        self.assertEqual(db.get_warnings(), [])

    def test_return_value_produces_a_real_predicate_dependency(self):
        for width in (16, 32):
            with self.subTest(width=width):
                vf = parse(f"vector_bool p=movp_b{width}();vdup(x,1.0f,p,MODE_ZEROING);"
                           f"vsts(x,out,0,NORM_B{width},mask);", width)
                producer = next(n for n in vf.context if n.opcode == f"MOVP_B{width}")
                self.assertEqual(producer.inputs, ())
                self.assertEqual(producer.form, f"b{width}")
                value = vf.values[producer.outputs[0].value_id]
                self.assertEqual((value.storage.value, value.dtype), ("PredicateRegister", "bool"))
                rows = self.run_core(vf)
                move = next(r for r in rows if r["op"] == producer.opcode)
                consumer = next(r for r in rows if r["op"] == "VDUP")
                self.assertTrue(move["preg_dst"][0].startswith("pred"))
                self.assertEqual(move["preg_src"], [])
                self.assertIn(move["preg_dst"][0], consumer["preg_src"])
                self.assertGreaterEqual(consumer["cy"], move["cy"]+2)

    def test_reassignment_uses_distinct_ssa_definitions(self):
        vf = parse("vector_bool p;movp_b32(p);vdup(x,1.0f,p,MODE_ZEROING);"
                   "p=movp_b32();vdup(y,2.0f,p,MODE_ZEROING);"
                   "vsts(x,out,0,NORM_B32,mask);vsts(y,out,64,NORM_B32,mask);")
        producers = [n for n in vf.context if n.opcode == "MOVP_B32"]
        consumers = [n for n in vf.context if n.opcode == "VDUP"]
        self.assertNotEqual(producers[0].outputs[0].value_id, producers[1].outputs[0].value_id)
        for producer, consumer in zip(producers, consumers):
            self.assertIn(producer.outputs[0].value_id, [o.value_id for o in consumer.inputs])
        self.run_core(vf)

    def test_invalid_form_destination_and_arguments_are_rejected(self):
        for body in ("mask=movp_b8();", "movp_b32(x);", "movp_b16(mask,x);"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse(body)
