import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict, canonical_vf_info_from_dict, validate_canonical_vf_info
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB
from tests.test_predicate_registers import ROOT, records


def parse(body):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "spill.cce"
        path.write_text("void vf(__ubuf__ float *input,__ubuf__ float *out,"
                        "__ubuf__ uint32_t *spill){__VEC_SCOPE__{"
                        "vector_bool all,p,q;vector_f32 x,y;" + body + "}}")
        return parse_cce_canonical_vf_info(path)


class PredicateSpillTest(unittest.TestCase):
    def run_case(self, vf):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = CoreVfCostModel(out_dir=root / "python").run_vf_info(vf)
            self.assertLess(result["vf_end_cycle"], 10000)
            starts = records(root / "python/start_by_cycle.json")
            runner = os.environ.get("VFSIM_NATIVE_RUNNER")
            if runner:
                path = root / "input.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc = subprocess.run([runner, "--trace", str(path), "--out-dir", str(root / "native"),
                                       "--max-cycles", "10000"], capture_output=True, text=True, timeout=30)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                self.assertEqual(key(starts), key(records(root / "native/start_by_cycle.json")))
            return sorted(starts, key=lambda r: r["stream_seq"])

    def test_byte_address_and_predicate_storage(self):
        vf = parse("__ubuf__ uint32_t *slot=spill+8;plds(p,slot,-32,NORM);psts(p,slot,32,NORM);")
        load, store = vf.context
        for node, memory, expected in ((load, load.inputs[0].memory_access, 0),
                                       (store, store.outputs[0].memory_access, 64)):
            self.assertEqual(node.form, "b8")
            self.assertEqual((memory.offset.constant, memory.span, memory.address_unit_bytes), (expected, 32, 1))
            self.assertEqual(DEFAULT_INSTRUCTION_CATALOG.lookup(node.opcode).ub_transfer_bytes, 256)
        self.assertEqual(vf.values[load.outputs[0].value_id].storage.value, "PredicateRegister")
        self.assertEqual(canonical_vf_info_from_dict(canonical_vf_info_to_dict(vf)), vf)
        rows = self.run_case(vf)
        self.assertEqual(rows[0]["preg_dst"], rows[1]["preg_src"])
        self.assertEqual(rows[1]["preg_dst"], [])
        self.assertEqual(rows[1]["cy"]-rows[0]["cy"], 8)

    def test_loop_spill_reload_and_release(self):
        vf = parse("all=pset_b32(PAT_ALL);for(int i=0;i<80;++i){"
                   "vlds(x,input,i*64,NORM);vcmp_eq(p,x,x,all);"
                   "mem_bar(VLD_VST);psts(p,spill,-1376,NORM);"
                   "mem_bar(VST_VLD);plds(q,spill,-1376,NORM);"
                   "vsel(y,x,x,q);vsts(y,out,i*64,NORM_B32,all);}")
        rows = self.run_case(vf)
        self.assertEqual(sum(r["op"] == "PLDS" for r in rows), 80)
        for i, row in enumerate(rows):
            if row["op"] == "PSTS":
                compare = next(r for r in reversed(rows[:i]) if r["op"] == "VCMP_EQ")
                self.assertEqual(row["preg_src"], compare["preg_dst"])
                self.assertGreaterEqual(row["cy"], compare["cy"]+4)
            if row["op"] == "PLDS":
                store = next(r for r in reversed(rows[:i]) if r["op"] == "PSTS")
                select = next(r for r in rows[i+1:] if r["op"] == "VSEL")
                self.assertEqual(select["preg_src"][-1:], row["preg_dst"])
                self.assertGreaterEqual(row["cy"], store["cy"]+9)
                self.assertGreaterEqual(select["cy"], row["cy"]+6)

    def test_signature_and_address_validation(self):
        for body in ("plds(x,spill,0,NORM);", "psts(x,spill,0,NORM);",
                     "plds(p,spill,0,BRC_B32);", "plds(p,spill,0,NORM,POST_UPDATE);"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse(body)
        vf = parse("plds(p,spill,0,NORM);")
        load = vf.context[0]
        for unit in (None, 4, True, "1"):
            operand = replace(load.inputs[0], memory_access=replace(load.inputs[0].memory_access, address_unit_bytes=unit))
            invalid = replace(vf, context=(replace(load, inputs=(operand,)),))
            self.assertFalse(validate_canonical_vf_info(invalid).ok)

    def test_reload_mask_consumers(self):
        for instruction, output_op in (("vadds(y,x,1.0f,p,MODE_ZEROING);", "VADDS"),
                                       ("vcmp_eq(q,x,x,p);vsel(y,x,x,q);", "VCMP_EQ"),
                                       ("pand(q,p,p,all);vsel(y,x,x,q);", "PAND"),
                                       ("vsel(y,x,x,p);", "VSEL")):
            vf = parse("all=pset_b32(PAT_ALL);vlds(x,input,0,NORM);plds(p,spill,32,NORM);"
                       + instruction + "vsts(y,out,0,NORM_B32,all);")
            rows = self.run_case(vf)
            load = next(r for r in rows if r["op"] == "PLDS")
            consumer = next(r for r in rows if r["op"] == output_op)
            self.assertIn(load["preg_dst"][0], consumer["preg_src"])
            self.assertGreaterEqual(consumer["cy"], load["cy"] + 6)

    def test_measured_profiles(self):
        db = ParamDB(base_dir=str(ROOT))
        for op, op_class in (("PLDS", "LOAD"), ("PSTS", "STORE")):
            profile = db.resolve_inst(op, "b8", "fp32")
            self.assertEqual((profile.op_class, profile.latency), (op_class, 9))
        for producer, pform, consumer, cform, fwd in (
            ("VCMP_EQ", "fp32", "PSTS", "b8", 4),
            ("PSET_B8", "b8", "PSTS", "b8", 4),
            ("PSET_B16", "b16", "PSTS", "b8", 4),
            ("PSET_B32", "b32", "PSTS", "b8", 4),
            ("PLDS", "b8", "PSTS", "b8", 8),
            ("PLDS", "b8", "VSEL", "fp32", 6),
            ("PLDS", "b8", "VADDS", "fp32", 6),
            ("PLDS", "b8", "PAND", "b8", 6),
            ("PLDS", "b8", "VCMP_EQ", "fp32", 6),
            ("PLDS", "b8", "VSTS", "fp32", 8),
        ):
            self.assertEqual(db.get_forwarding_cycles(producer, consumer,
                producer_form=pform, consumer_form=cform), fwd)
