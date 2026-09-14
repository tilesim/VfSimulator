"""Run with VFSIM_NATIVE_RUNNER pointing to the built canonical JSON runner."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend.builder import VfInfoBuilder
from tools.membar_fixture_export import canonical_vf_info_to_dict
from api.simulator_costmodel import CoreVfCostModel


ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


@unittest.skipUnless(RUNNER, "set VFSIM_NATIVE_RUNNER to run cross-language tests")
class MembarNativeParityTests(unittest.TestCase):
    def test_absent_timing_uses_compatibility_in_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = json.loads((ROOT/'configs/uarch.json').read_text())
            del cfg['membar_timing']
            config_path = root/'uarch.json'
            config_path.write_text(json.dumps(cfg))
            builder = VfInfoBuilder()
            builder.add_membar('tail', barrier='VST_VLD')
            trace = root/'input.json'
            trace.write_text(json.dumps(canonical_vf_info_to_dict(builder.build())))
            with patch.dict(os.environ, {'UARCH_JSON_PATH': str(config_path)}):
                subprocess.run([RUNNER, '--trace', str(trace),
                                '--out-dir', str(root/'native')],
                               capture_output=True, text=True, timeout=30, check=True)
            self.assertFalse((root/'native/membar_history.json').exists())

    def test_invalid_timing_is_rejected_by_both_public_entries(self):
        for value in ({}, None, [], False, 0, '', {'admission_delay': 1}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                cfg = json.loads((ROOT/'configs/uarch.json').read_text())
                cfg['membar_timing'] = value
                config_path = root/'uarch.json'
                config_path.write_text(json.dumps(cfg))
                vf = VfInfoBuilder().build()
                input_path = root/'input.json'
                input_path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                with patch.dict(os.environ, {'UARCH_JSON_PATH': str(config_path)}):
                    with self.assertRaisesRegex(ValueError, 'membar_timing'):
                        CoreVfCostModel(base_dir=ROOT, out_dir=root/'py').run_canonical_vf_info(vf)
                    proc = subprocess.run([RUNNER, '--trace', str(input_path)],
                                          capture_output=True, text=True, timeout=30)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn('membar_timing', proc.stderr)

    def compare(self, vf, soc, root):
        path = root / "input.json"
        path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
        py_dir, cpp_dir = root / "python", root / "native"
        result = CoreVfCostModel(base_dir=ROOT, out_dir=py_dir).run_canonical_vf_info(vf)
        proc = subprocess.run([RUNNER, "--trace", str(path), "--out-dir", str(cpp_dir),
                                "--max-cycles", "10000"],
                              capture_output=True, text=True, timeout=30, check=True)
        fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        self.assertEqual(result["vf_end_cycle"], int(fields["vfEndCycle"]))
        self.assertEqual(result["cycles_executed"], int(fields["cyclesExecuted"]))
        for filename in ("start_by_cycle.json", "done_by_cycle.json", "membar_history.json"):
            def events(folder):
                text = (folder / filename).read_text()
                rows = json.loads(text) if filename == "membar_history.json" else [
                    json.loads(line) for line in text.splitlines() if line.strip()]
                return sorted((r["stream_seq"], r["cy"], r.get("op", ""),
                               r.get("event", ""), r.get("barrier", "")) for r in rows)
            self.assertEqual(events(py_dir), events(cpp_dir), filename)

    def test_shared_canonical_programs(self):
        load = "vlds(a, input, 0, NORM);"
        store = "vsts(b, output, 0, NORM_B32, mask);"
        compute = "vadds(b, a, 1.0f, mask);"
        cases = {
            "no_barrier": load + compute + store,
            "single": load + compute + "mem_bar(VLD_VST);" + store + "mem_bar(VST_VLD);" + load,
            "clustered": load + compute + "mem_bar(VLD_VST);" * 8 + store + "mem_bar(VST_VLD);" * 8 + load,
            "interleaved": load + compute + ("mem_bar(VLD_VST);" + store) * 8 + ("mem_bar(VST_VLD);" + load) * 8,
            "loop": "for (int i = 0; i < 3; ++i) {" + load + compute + "mem_bar(VLD_VST);" + store + "mem_bar(VST_VLD);}",
        }
        for soc in ("A5",):
            for name, body in cases.items():
                with self.subTest(case=name), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    cce = root / "input.cce"
                    cce.write_text("void vf(__ubuf__ float *input, __ubuf__ float *output) {"
                                   "__VEC_SCOPE__ {vector_f32 a, b;"
                                   "vector_bool mask = pset_b32(PAT_ALL);" + body + "}}")
                    self.compare(parse_cce_canonical_vf_info(cce), soc, root)
            for kind in ("VLD_VST", "VST_VLD"):
                with self.subTest(tail=kind), tempfile.TemporaryDirectory() as tmp:
                    builder = VfInfoBuilder()
                    builder.add_membar("tail", barrier=kind)
                    self.compare(builder.build(), soc, Path(tmp))


if __name__ == "__main__":
    unittest.main()
