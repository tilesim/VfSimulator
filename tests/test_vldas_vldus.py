import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB

ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def parse(body, dtype="fp32"):
    ctype, vector = {"fp32": ("float", "f32"), "fp16": ("half", "f16"),
                     "int32": ("int32_t", "s32")}[dtype]
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "probe.cce"
        source.write_text(f"void vf(__ubuf__ {ctype} *p,__ubuf__ {ctype} *q){{"
                          f"__VEC_SCOPE__{{vector_{vector} a,b;vector_align s,t;"
                          f"vector_bool mask=pset_b{16 if dtype == 'fp16' else 32}(PAT_ALL);"
                          + body + "}}")
        return parse_cce_canonical_vf_info(source)


class UnalignedLoadTest(unittest.TestCase):
    def run_case(self, vf):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            py = root / "python"
            result = CoreVfCostModel(out_dir=str(py)).run_vf_info(vf)
            self.assertLess(result["vf_end_cycle"], 1000)
            rows = [json.loads(line) for line in (py / "start_by_cycle.json").read_text().splitlines()]
            if RUNNER:
                source = root / "program.json"
                source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                out = root / "native"
                proc = subprocess.run([RUNNER,"--trace",str(source),"--out-dir",str(out),"--max-cycles","1000"],
                                      capture_output=True,text=True)
                self.assertEqual(proc.returncode,0,proc.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}",proc.stdout)
                native = [json.loads(line) for line in (out / "start_by_cycle.json").read_text().splitlines()]
                key = lambda rs: sorted((r["inst_id"],r["op"],r["cy"]) for r in rs)
                self.assertEqual(key(rows),key(native))
            return sorted(rows,key=lambda r:r["stream_seq"])

    def test_straight_init_use_and_vector_credit(self):
        for dtype, lanes, bits in (("fp32",64,32),("fp16",128,16),("int32",64,32)):
            vf = parse(f"vldas(s,p);vldus(a,s,p,{lanes},POST_UPDATE);"
                       f"vsts(a,q,0,NORM_B{bits},mask);",dtype)
            rows = self.run_case(vf)
            init = next(r for r in rows if r["op"] == "VLDAS")
            use = next(r for r in rows if r["op"] == "VLDUS")
            self.assertEqual(init["preg_dst"],[])
            self.assertEqual(len(use["preg_dst"]),1)
            self.assertEqual(use["cy"],init["cy"]+1)

    def test_loops_and_independent_reinitialized_states(self):
        for unroll in (1,2):
            vf = parse("vldas(s,p);vldas(t,q);"
                       f"\n#pragma unroll({unroll})\nfor(int i=0;i<4;++i){{"
                       "vldus(a,s,p,64,POST_UPDATE);vldus(b,t,q,64,POST_UPDATE);"
                       "vadd(a,a,b,mask);vsts(a,q,0,NORM_B32,mask);}"
                       "vldas(s,p);vldus(a,s,p);vsts(a,q,0,NORM_B32,mask);")
            rows = self.run_case(vf)
            self.assertEqual(sum(r["op"] == "VLDAS" for r in rows),3)
            self.assertEqual(sum(r["op"] == "VLDUS" for r in rows),9)
            initializers = [r for r in rows if r["op"] == "VLDAS"]
            uses = [r for r in rows if r["op"] == "VLDUS"]
            for i, use in enumerate(uses[:-1]):
                self.assertGreaterEqual(use["cy"], initializers[i % 2]["cy"]+1)
            self.assertGreaterEqual(uses[-1]["cy"], initializers[-1]["cy"]+1)

    def test_uninitialized_state_and_zero_loop_are_rejected(self):
        for body in ("vldus(a,s,p);", "for(int i=0;i<0;++i){vldas(s,p);}vldus(a,s,p);"):
            vf = parse(body)
            with tempfile.TemporaryDirectory() as tmp:
                with self.assertRaisesRegex(ValueError,"Uninitialized load align state"):
                    CoreVfCostModel(out_dir=tmp).run_vf_info(vf)
                if RUNNER:
                    source=Path(tmp)/"invalid.json"
                    source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                    proc=subprocess.run([RUNNER,"--trace",str(source),"--out-dir",str(Path(tmp)/"native")],capture_output=True,text=True)
                    self.assertNotEqual(proc.returncode,0)
                    self.assertIn("Uninitialized load align state",proc.stderr)

    def test_forwarding_and_latency(self):
        db=ParamDB(base_dir=str(ROOT))
        for form in ("fp32","fp16","int32"):
            for op in ("VLDAS","VLDUS"):
                self.assertEqual(db.resolve_inst(op,form,form).latency,9)
            self.assertEqual(db.get_forwarding_cycles("VLDAS","VLDUS",form,form,form),1)
            for consumer in ("VADD","VSTS"):
                self.assertEqual(db.get_forwarding_cycles("VLDUS",consumer,form,form,form),
                                 db.get_forwarding_cycles("VLDS",consumer,form,form,form))
