import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.frontend.instruction_catalog import instruction_catalog_from_dict
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB

ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def parse(body, bits=32):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/"pstu.cce"
        path.write_text(f"void vf(__ubuf__ uint{bits}_t *out,__ubuf__ uint{bits}_t *other,__ubuf__ float *input){{"
                        f"__VEC_SCOPE__{{vector_align s,t;vector_bool p=pset_b{bits}(PAT_ALL);"
                        + body + "}}")
        return parse_cce_canonical_vf_info(path)


class PredicateStoreTest(unittest.TestCase):
    def run_case(self, vf):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=CoreVfCostModel(out_dir=str(root/"python")).run_vf_info(vf)
            self.assertLess(result["vf_end_cycle"],1000)
            rows=[json.loads(line) for line in (root/"python/start_by_cycle.json").read_text().splitlines()]
            if RUNNER:
                path=root/"program.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc=subprocess.run([RUNNER,"--trace",str(path),"--out-dir",str(root/"native"),"--max-cycles","1000"],capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}",proc.stdout)
                native=[json.loads(line) for line in (root/"native/start_by_cycle.json").read_text().splitlines()]
                key=lambda rs: sorted((r["inst_id"],r["op"],r["cy"]) for r in rs)
                self.assertEqual(key(rows),key(native))
            return sorted(rows,key=lambda r:r["stream_seq"])

    def test_packing_and_predicate_dependency(self):
        for bits, delta, span in ((32,8,2),(16,16,8)):
            vf=parse("pstu(s,p,out);vstas(s,out,0,POST_UPDATE);",bits)
            store=vf.context[1]
            access=store.outputs[0].memory_access
            self.assertEqual(access.update_mode,"post_update")
            self.assertEqual(access.post_update_delta_bytes.constant,delta)
            self.assertEqual(access.span,span)
            self.assertEqual(store.inputs[0].role.value,"predicate")
            rows=self.run_case(vf)
            pset, pstu, flush=rows
            self.assertEqual(pstu["preg_src"],pset["preg_dst"])
            self.assertEqual(pstu["preg_dst"],[])
            self.assertGreaterEqual(pstu["cy"],pset["cy"]+2)
            self.assertEqual(flush["cy"],pstu["cy"]+1)
            db=ParamDB(base_dir=str(ROOT))
            form=f"uint{bits}"
            self.assertEqual(db.resolve_inst("PSTU",form,form).latency,8)

    def test_groups_loops_and_independent_states(self):
        vf=parse("for(int i=0;i<4;++i){pstu(s,p,out);pstu(t,p,other);}"
                 "vstas(s,out,0,POST_UPDATE);vstas(t,other,0,POST_UPDATE);"
                 "mem_bar(VST_VLD);pstu(s,p,out);vstas(s,out,0,POST_UPDATE);")
        rows=self.run_case(vf)
        stores=[r for r in rows if r["op"]=="PSTU"]
        flushes=[r for r in rows if r["op"]=="VSTAS"]
        self.assertEqual(len(stores),9)
        self.assertEqual(len(flushes),3)
        self.assertGreaterEqual(flushes[0]["cy"],max(r["cy"] for r in stores[:8:2])+1)
        self.assertGreaterEqual(flushes[1]["cy"],max(r["cy"] for r in stores[1:8:2])+1)
        self.assertLess(flushes[0]["cy"],stores[-1]["cy"])
        self.assertGreaterEqual(flushes[-1]["cy"],stores[-1]["cy"]+1)

    def test_invalid_implicit_update_contract(self):
        vf=parse("pstu(s,p,out);vstas(s,out,0,POST_UPDATE);")
        store=vf.context[1]
        operand=store.outputs[0]
        memory=replace(operand.memory_access,update_mode="none",post_update_delta_bytes=None)
        invalid=replace(vf,context=(vf.context[0],replace(store,outputs=(replace(operand,memory_access=memory),)),vf.context[2]))
        self.assertIn("catalog_implicit_update_mismatch",[e.code for e in validate_canonical_vf_info(invalid).errors])
        if RUNNER:
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/"invalid.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(invalid)))
                proc=subprocess.run([RUNNER,"--trace",str(path)],capture_output=True,text=True)
                self.assertNotEqual(proc.returncode,0)
                self.assertIn("catalog_implicit_update_mismatch",proc.stderr)

    def test_compare_forwarding_and_sealed_generation(self):
        vf = parse("vector_f32 x;vector_bool q;"
                   "vlds(x,input,0,NORM);vcmp_gt(q,x,x,p);"
                   "pstu(s,q,out);vstas(s,out,0,POST_UPDATE);"
                   "mem_bar(VST_VLD);vlds(x,input,64,NORM);"
                   "vcmp_gt(q,x,x,p);pstu(s,q,out);vstas(s,out,0,POST_UPDATE);")
        rows = self.run_case(vf)
        compares = [r for r in rows if r["op"] == "VCMP_GT"]
        stores = [r for r in rows if r["op"] == "PSTU"]
        flushes = [r for r in rows if r["op"] == "VSTAS"]
        loads = [r for r in rows if r["op"] == "VLDS"]
        db = ParamDB(base_dir=str(ROOT))
        forwarding = db.get_forwarding_cycles(
            "VCMP_GT", "PSTU", producer_form="fp32", consumer_form="uint32")
        for compare, store, flush in zip(compares, stores, flushes):
            self.assertEqual(store["preg_src"], compare["preg_dst"])
            self.assertGreaterEqual(store["cy"], compare["cy"] + forwarding)
            self.assertEqual(flush["cy"], store["cy"] + 1)
        self.assertGreaterEqual(loads[1]["cy"], flushes[0]["cy"] + 8)
        self.assertLess(flushes[0]["cy"], stores[1]["cy"])

    def test_unsupported_pointer_and_extra_argument(self):
        with self.assertRaises(ValueError):
            parse("pstu(s,p,out,POST_UPDATE);")
        with self.assertRaises(ValueError):
            parse("pstu(s,p,out+1);")

    def test_catalog_rejects_invalid_memory_form_parameters(self):
        for field in ("implicit_post_update_bytes", "memory_span_by_form"):
            for value in ([], {"uint32": True}, {"uint32": 0},
                          {"uint32": 2**63}, {"fp32": 8}):
                with self.subTest(field=field, value=value):
                    payload = json.loads((ROOT / "configs/instruction_catalog.json").read_text())
                    payload["instructions"]["PSTU"][field] = value
                    with self.assertRaises(ValueError):
                        instruction_catalog_from_dict(payload)
