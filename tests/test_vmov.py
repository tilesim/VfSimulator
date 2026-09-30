import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from dataclasses import replace

from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB
from tests.test_predicate_registers import parse, records, ROOT


class VectorMoveTest(unittest.TestCase):
    def test_signature_is_unmasked_register_copy(self):
        vf = parse("mask=pset_b32(PAT_ALL);vdup(x,1.5f,mask,MODE_ZEROING);vmov(y,x);")
        move = vf.context[-1]
        self.assertEqual(move.opcode, "VMOV")
        self.assertEqual(len(move.inputs), 1)
        self.assertEqual(len(move.outputs), 1)
        self.assertEqual(move.inputs[0].value_id, vf.context[-2].outputs[0].value_id)
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        for bad in (replace(move, inputs=()), replace(move, inputs=(*move.inputs, *move.inputs))):
            self.assertFalse(validate_canonical_vf_info(replace(vf, context=(*vf.context[:-1], bad))).ok)
        for call in ("vmov(y,x,mask);", "vmov(y,1.0f);"):
            with self.assertRaises(ValueError):
                parse("mask=pset_b32(PAT_ALL);vdup(x,1.5f,mask,MODE_ZEROING);" + call)

    def test_copy_is_real_instruction_and_preserves_dependencies(self):
        for form in ("fp32", "b32"):
            vf = parse("mask=pset_b32(PAT_ALL);vdup(x,1.5f,mask,MODE_ZEROING);"
                       + "vmov(x,x);" * 16 + "vsts(x,out,0,NORM_B32,mask);")
            vf = replace(vf, context=tuple(replace(n, form=form) if n.opcode in ("VDUP", "VMOV") else n
                                           for n in vf.context))
            with self.subTest(form=form), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = CoreVfCostModel(out_dir=root / "python").run_vf_info(vf)
                starts = records(root / "python/start_by_cycle.json")
                chain = sorted((r for r in starts if r["op"] in ("VDUP", "VMOV")), key=lambda r:r["inst_id"])
                self.assertEqual(len(chain), 17)
                for prev, cur in zip(chain, chain[1:]):
                    self.assertEqual(cur["preg_src"], prev["preg_dst"])
                    self.assertEqual(cur["cy"] - prev["cy"], 2)
                store = next(r for r in starts if r["op"] == "VSTS")
                self.assertEqual(store["cy"] - chain[-1]["cy"], 4)
                runner = os.environ.get("VFSIM_NATIVE_RUNNER")
                if runner:
                    path = root / "input.json"
                    path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                    proc = subprocess.run([runner, "--trace", str(path), "--out-dir", str(root / "native")],
                                          capture_output=True, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                    key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                    self.assertEqual(key(starts), key(records(root / "native/start_by_cycle.json")))

    def test_profile(self):
        db = ParamDB(base_dir=str(ROOT))
        for form in ("fp32", "b32"):
            profile = db.resolve_inst("VMOV", form, "fp32")
            self.assertEqual((profile.latency, profile.fu_type, profile.dispatch_exu), (6, "ALU", "EXU01"))
            self.assertEqual(db.get_ii_for_profiles(profile, profile), 1)

    def test_load_to_move_forwarding(self):
        for form in ("fp32", "b32"):
            vf = parse("mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);"
                       "vmov(y,x);vsts(y,out,0,NORM_B32,mask);")
            vf = replace(vf, context=tuple(replace(n, form=form) if n.opcode == "VMOV" else n
                                           for n in vf.context))
            with self.subTest(form=form), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = CoreVfCostModel(out_dir=root / "python").run_vf_info(vf)
                starts = records(root / "python/start_by_cycle.json")
                by_op = {r["op"]: r for r in starts}
                self.assertEqual(by_op["VMOV"]["preg_src"], by_op["VLDS"]["preg_dst"])
                self.assertEqual(by_op["VMOV"]["cy"] - by_op["VLDS"]["cy"], 6)
                runner = os.environ.get("VFSIM_NATIVE_RUNNER")
                if runner:
                    path = root / "input.json"
                    path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                    proc = subprocess.run([runner, "--trace", str(path), "--out-dir", str(root / "native")],
                                          capture_output=True, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                    key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                    self.assertEqual(key(starts), key(records(root / "native/start_by_cycle.json")))
