import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce.adapter import parse_cce_canonical_vf_info
from api.json.serialization import canonical_vf_info_to_dict, canonical_vf_info_from_dict
from api.common.validator import validate_canonical_vf_info
from core.simulator import CoreVfCostModel
from core.params.database import ParamDB
from tests.core_support import records, ROOT


class PredicateLogicTest(unittest.TestCase):
    def test_measured_profiles_and_explicit_self_dependencies(self):
        db = ParamDB(base_dir=str(ROOT))
        for op, latency, forwarding in (("PNOT",6,2), ("PXOR",7,3), ("PSEL",7,3), ("PAND",7,3)):
            with self.subTest(op=op):
                profile = db.resolve_inst(op, "b8", "bool")
                self.assertEqual((profile.latency, profile.dispatch_exu), (latency, "EXU01"))
                self.assertEqual(db.get_forwarding_cycles(op,op,producer_form="b8",consumer_form="b8"),forwarding)
        pnot = db.resolve_inst("PNOT", "b8", "bool")
        self.assertEqual(db.get_ii_for_profiles(pnot,pnot),1)

    def parse(self, call):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "predicate.cce"
            path.write_text("void vf(__ubuf__ float *a,__ubuf__ float *out){__VEC_SCOPE__{"
                            "vector_bool all=pset_b32(PAT_ALL);vector_bool p,q;vector_f32 x,y;"
                            "vlds(x,a,0,NORM);vcmp_gt(p,x,x,all);" + call + ";"
                            "vsel(y,x,x,q);vsts(y,out,0,NORM_B32,all);}}")
            return parse_cce_canonical_vf_info(path)

    def test_predicate_bank_dependencies_and_native_parity(self):
        for call, op, count in (("pnot(q,p,all)", "PNOT", 2),
                                ("pxor(q,p,p,all)", "PXOR", 3),
                                ("pmov(q,p)", "PAND", 3),
                                ("pmov(q,p,all)", "PAND", 3),
                                ("psel(q,p,p,all)", "PSEL", 3)):
            with self.subTest(call=call), tempfile.TemporaryDirectory() as tmp:
                vf = self.parse(call)
                node = vf.context[3]
                self.assertEqual((node.opcode, node.form, len(node.inputs)), (op, "b8", count))
                self.assertTrue(all(vf.values[x.value_id].storage.value == "PredicateRegister"
                                    for x in (*node.inputs, *node.outputs)))
                payload = canonical_vf_info_to_dict(vf)
                self.assertEqual(canonical_vf_info_from_dict(payload), vf)
                output = Path(tmp) / "python"
                result = CoreVfCostModel(out_dir=output).run_vf_info(vf)
                issued = records(output / "start_by_cycle.json")
                item = next(x for x in issued if x["op"] == op)
                producer = next(x for x in issued if x["op"] == "VCMP_GT")
                consumer = next(x for x in issued if x["op"] == "VSEL")
                self.assertIn(producer["preg_dst"][0], item["preg_src"])
                self.assertIn(item["preg_dst"][0], consumer["preg_src"])
                self.assertTrue(all(p.startswith("pred") for p in item["preg_dst"]))
                runner = os.environ.get("VFSIM_NATIVE_RUNNER")
                if runner:
                    source = Path(tmp) / "vf.json"
                    source.write_text(json.dumps(payload))
                    native = Path(tmp) / "native"
                    proc = subprocess.run([runner, "--trace", str(source), "--out-dir", str(native),
                                           "--max-cycles", "10000"], capture_output=True, text=True, timeout=30)
                    self.assertEqual(proc.returncode, 0, proc.stderr)
                    self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                    key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                    for file in ("start_by_cycle.json", "done_by_cycle.json"):
                        self.assertEqual(key(records(output / file)), key(records(native / file)))

    def test_required_sources_and_mask_kinds(self):
        for call in ("pmov(q)", "pnot(q,p)", "pxor(q,p,p)",
                     "psel(q,p,p)", "pmov(q,p,x)", "pnot(q,x,all)"):
            with self.subTest(call=call), self.assertRaises(ValueError):
                self.parse(call)

    def test_move_lowering_and_virtual_canonical_rejection(self):
        vf = self.parse("pmov(q,p)")
        from dataclasses import replace
        node = vf.context[3]
        self.assertEqual(node.opcode, "PAND")
        self.assertEqual(len({o.value_id for o in node.inputs}), 1)
        invalid = replace(vf, context=(*vf.context[:3], replace(node, inputs=()), *vf.context[4:]))
        result = validate_canonical_vf_info(invalid)
        self.assertFalse(result.ok)
        self.assertIn("catalog_operand_count_mismatch", {d.code for d in result.errors})
        invalid = replace(vf, context=(*vf.context[:3], replace(node, opcode="PMOV"), *vf.context[4:]))
        self.assertIn("catalog_specialization_required", {
            d.code for d in validate_canonical_vf_info(invalid).errors})


if __name__ == "__main__":
    unittest.main()
