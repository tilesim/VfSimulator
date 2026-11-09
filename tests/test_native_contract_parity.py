"""Compare complete public outputs against the C++ executable."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.input_api import InputAPI
from api.json.serialization import canonical_vf_info_to_dict
from core.simulator import CoreVfCostModel

ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def read_json(path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


@unittest.skipUnless(RUNNER, "VFSIM_NATIVE_RUNNER is not configured")
class NativeContractParityTests(unittest.TestCase):
    def compare(self, vf, root, *, arguments=None, vloop_only=False, direct_issue=False):
        result = CoreVfCostModel(
            base_dir=ROOT, out_dir=root / "python",
            theoretical_limit_vloop_only=vloop_only,
            theoretical_limit_direct_issue=direct_issue,
        ).run_vf_info(vf)
        if arguments is None:
            path = root / "input.json"
            path.write_text(json.dumps(canonical_vf_info_to_dict(vf)), encoding="utf-8")
            arguments = ["--trace", str(path)]
        if vloop_only:
            arguments += ["--theoretical-limit-vloop-only"]
        if direct_issue:
            arguments += ["--theoretical-limit-vloop-only-legacy-forwarding-direct-issue"]
        process = subprocess.run(
            [RUNNER, *arguments, "--out_dir", str(root / "cpp")],
            check=True, capture_output=True, text=True, timeout=60,
        )
        fields = dict(line.split("=", 1) for line in process.stdout.splitlines() if "=" in line)
        self.assertEqual(int(fields["cyclesExecuted"]), result["cycles_executed"])
        self.assertEqual(int(fields["vfEndCycle"]), result["vf_end_cycle"])
        self.assertEqual(int(fields["linearInstCount"]), result["linear_inst_count"])
        self.assertEqual(json.loads(fields['compilerOptimizationStats']), result['compiler_optimization_stats'])
        py_files = {p.name for p in (root / "python").glob("*.json")}
        cpp_files = {p.name for p in (root / "cpp").glob("*.json")}
        self.assertEqual(py_files, cpp_files)
        for name in sorted(py_files):
            with self.subTest(output=name):
                self.assertEqual(read_json(root / "python" / name), read_json(root / "cpp" / name))

    def test_complete_canonical_fixture_outputs(self):
        for fixture in sorted((ROOT / "VFtest/canonical/fixtures").rglob("*.json")):
            if "invalid" in fixture.name:
                continue
            with self.subTest(fixture=fixture.name), tempfile.TemporaryDirectory() as folder:
                self.compare(InputAPI.load_json(fixture), Path(folder))

    def test_cce_input_and_kernel_selection(self):
        source = """
        void other(){__VEC_SCOPE__{vector_bool p=pset_b32(PAT_ALL);}}
        void vf(__ubuf__ float *a, __ubuf__ float *b){__VEC_SCOPE__{
          vector_f32 x,y; vector_bool p=pset_b32(PAT_ALL);
          for(int i=0;i<4;++i){
            vlds(x,a,64,NORM,POST_UPDATE); y=x;
            vadds(x,y,1.0f,p); mem_bar(VLD_VST);
            vsts(x,b,64,NORM_B32,p,POST_UPDATE); mem_bar(VST_VLD);
          }
        }}
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "input.cce"
            path.write_text(source, encoding="utf-8")
            vf = InputAPI.load_cce(path, kernel_name="vf")
            self.compare(vf, root, arguments=["--cce", str(path), "--cce-kernel", "vf"])
            rejected = subprocess.run([RUNNER, "--cce", str(path)], capture_output=True, text=True)
            self.assertNotEqual(rejected.returncode, 0)

    def test_loop_invariant_scalar_dataflow_and_exit_consumers(self):
        bodies = (
        'for(int i=0;i<4;++i){uint32_t k=n;p=plt_b32(k,POST_UPDATE);pnot(q,p,all);vdup(y,2.5f,q,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);}',
        'uint32_t k=n;for(int i=0;i<4;++i){p=plt_b32(k,POST_UPDATE);vlds(x,a,i*64,NORM);vsel(y,x,x,p);vsts(y,out,i*64,NORM_B32,all);}',
        'for(int j=0;j<2;++j){uint32_t outer=n+j*7;for(int i=0;i<4;++i){uint32_t k=outer;p=plt_b32(k,POST_UPDATE);vdup(y,2.5f,p,MODE_ZEROING);vsts(y,out,j*256+i*64,NORM_B32,all);}}',
        'for(int i=0;i<4;++i){p=pltm_b32((uint16_t)i,n);vdup(y,(uint32_t)i+3u,p,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);}',
        'vlds(x,a,0,NORM);\n#pragma unroll(2)\nfor(int i=0;i<4;++i){vadds(y,x,1.0f,all);}vadd(z,y,y,all);vsts(z,out,0,NORM_B32,all);',
        'volatile uint32_t k=37;for(int i=0;i<4;++i){p=pltm_b32(0,k);vdup(y,2.5f,p,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);}',
        'for(int j=0;j<2;++j){for(int i=0;i<4;++i){p=pset_b32(PAT_VL32);vdup(y,2.5f,p,MODE_ZEROING);vsts(y,out,j*256+i*64,NORM_B32,all);}}vsts(y,out,512,NORM_B32,all);',
        'uint32_t k=n;for(int i=0;i<4;++i){p=pltm_b32(0,k);vdup(y,2.5f,p,MODE_ZEROING);vsts(y,out,i*64,NORM_B32,all);k+=7;}',
        )
        for index, body in enumerate(bodies):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / 'licm.cce'
                path.write_text('void vf(__ubuf__ float *a,__ubuf__ float *out,uint32_t n){__VEC_SCOPE__{'
                                'vector_f32 x,y,z;vector_bool all,p,q;all=pset_b32(PAT_ALL);'+body+'}}')
                vf = InputAPI.load_cce(path)
                self.compare(vf, root, arguments=['--cce',str(path)])
                self.compare(vf, root)

    def test_sub_exu_resources_and_interleaved_exceptions(self):
        source = '''void vf(__ubuf__ float *input, __ubuf__ float *output) { __VEC_SCOPE__ {
          vector_f32 x,a,b,c,d,e; vector_bool p=pset_b32(PAT_ALL);
          for(int i=0;i<8;++i){
            vlds(x,input,i*64,NORM);
            vexp(a,x,p,MODE_ZEROING); vadd(b,x,x,p,MODE_ZEROING);
            vdiv(c,x,x,p,MODE_ZEROING); vmul(d,b,x,p,MODE_ZEROING);
            vexp(e,x,p,MODE_ZEROING);
            vsts(a,output,i*320,NORM_B32,p);
            vsts(c,output,i*320+64,NORM_B32,p);
            vsts(d,output,i*320+128,NORM_B32,p);
            vsts(e,output,i*320+192,NORM_B32,p);
          }
        }}'''
        for queued in (False, True):
            with self.subTest(queued=queued), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / 'resources.cce'
                path.write_text(source)
                vf = InputAPI.load_cce(path)
                vf = InputAPI.from_canonical(replace(vf, uarch={
                    'issue_ports': 1, 'enable_isu_queue_model': queued}))
                self.compare(vf, root)

    def test_cce_fixture_outputs(self):
        for fixture in sorted((ROOT / "VFtest/cce/contracts").glob("*.cce")):
            with self.subTest(fixture=fixture.name), tempfile.TemporaryDirectory() as folder:
                self.compare(InputAPI.load_cce(fixture), Path(folder), arguments=["--cce", str(fixture)])

    def test_theoretical_public_options(self):
        fixture = ROOT / "VFtest/canonical/fixtures/v1_valid_loop.json"
        for direct in (False, True):
            with self.subTest(direct=direct), tempfile.TemporaryDirectory() as folder:
                self.compare(InputAPI.load_json(fixture), Path(folder), vloop_only=not direct, direct_issue=direct)

    def test_membar_does_not_bypass_unroll_validation(self):
        fixture = ROOT / "VFtest/canonical/fixtures/timing/membar_disables_innermost_unroll_with_warning.json"
        vf = InputAPI.load_json(fixture)
        vf = InputAPI.from_canonical(replace(vf, context=(replace(vf.context[0], count=3),)))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(ValueError, "not divisible"):
                CoreVfCostModel(base_dir=ROOT, out_dir=root / "python").run_vf_info(vf)
            path = root / "invalid.json"
            path.write_text(json.dumps(canonical_vf_info_to_dict(vf)), encoding="utf-8")
            process = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", str(root / "cpp")], capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("not divisible", process.stderr)
            self.assertFalse((root / "cpp").exists())

    def test_timeouts_leave_no_partial_output(self):
        fixture = ROOT / "VFtest/canonical/fixtures/v1_valid_loop.json"
        vf = InputAPI.load_json(fixture)
        vf = InputAPI.from_canonical(replace(vf, params={**vf.params, "max_cycles": 1}))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with self.assertRaisesRegex(RuntimeError, "max_cycles"):
                CoreVfCostModel(base_dir=ROOT, out_dir=root / "python").run_vf_info(vf)
            path = root / "timeout.json"
            path.write_text(json.dumps(canonical_vf_info_to_dict(vf)), encoding="utf-8")
            process = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", str(root / "cpp")], capture_output=True, text=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertFalse((root / "python").exists())
            self.assertFalse((root / "cpp").exists())
