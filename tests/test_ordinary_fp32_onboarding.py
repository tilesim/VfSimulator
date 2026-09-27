import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from api.frontend import DEFAULT_INSTRUCTION_CATALOG, canonical_vf_info_to_dict
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]
OPS = {'VSQRT': (17, 14, 15), 'VLN': (18, 15, 16)}


class OrdinaryFp32OnboardingTest(unittest.TestCase):
    def test_only_measured_dv100_forms_and_fields(self):
        db = ParamDB(base_dir=str(ROOT))
        isa = json.loads((ROOT/'configs/isa.json').read_text())
        for op, (latency, _, _) in OPS.items():
            self.assertEqual(set(isa['instructions'][op]['forms']), {'fp32'})
            profile = db.resolve_inst(op, 'fp32', 'fp32')
            self.assertEqual(profile.latency, latency)
            self.assertEqual(profile.dispatch_exu, 'EXU01')
            self.assertEqual(profile.fu_type, 'SFU')
            fields = isa['instructions'][op]['forms']['fp32']
            self.assertFalse({'throughput', 'pipeline_startup_cost', 'pipeline_drain_cost',
                              'data_load_cost', 'data_store_cost'} & set(fields))
        self.assertEqual(db.get_warnings(), [])

    def test_exact_forwarding_and_self_ii(self):
        db = ParamDB(base_dir=str(ROOT))
        forwarding = json.loads((ROOT/'configs/forwarding.json').read_text())['forwarding']
        ii = json.loads((ROOT/'configs/InitiationInterval.json').read_text())['InitiationInterval']
        for op, (_, own, store) in OPS.items():
            self.assertEqual(db.get_forwarding_cycles('VLDS', op, 'fp32'), 6)
            self.assertEqual(db.get_forwarding_cycles(op, op, 'fp32'), own)
            self.assertEqual(db.get_forwarding_cycles(op, 'VSTS', 'fp32'), store)
            self.assertEqual(db.get_ii(op, op, 'fp32'), 4)
            self.assertEqual(forwarding[op+'.fp32'], {op+'.fp32': own, 'VSTS.fp32': store})
            self.assertEqual(ii[op+'.fp32'], {op+'.fp32': 4})
        self.assertEqual(db.get_warnings(), [])

    def test_unmeasured_pairs_keep_warnings(self):
        db = ParamDB(base_dir=str(ROOT))
        db.get_ii('VSQRT', 'VLN', 'fp32')
        db.get_ii('VLN', 'VSQRT', 'fp32')
        for op in OPS:
            db.get_forwarding_cycles(op, 'VADD', 'fp32')
        warnings = db.get_warnings()
        self.assertEqual(sum(w['kind']=='missing_ii_pair' for w in warnings), 2)
        self.assertEqual(sum(w['kind']=='missing_forwarding_pair' for w in warnings), 2)

    def test_catalog_is_minimal_and_does_not_hide_dv100_drift(self):
        for op in OPS:
            spec = DEFAULT_INSTRUCTION_CATALOG.lookup(op)
            self.assertEqual(spec.signature, 'unary')
            self.assertEqual(set(spec.forms), {'fp32'})
            self.assertTrue(spec.timing_optional)
            with self.assertRaises(ValueError):
                DEFAULT_INSTRUCTION_CATALOG.resolve_and_validate_form(op, 'fp16')
        for soc in ('DV100',):
            isa = json.loads((ROOT/'configs/isa.json').read_text())
            self.assertFalse(DEFAULT_INSTRUCTION_CATALOG.compare_timing_config(isa).has_semantic_conflicts)
        # timing_optional is global; test_only_measured_dv100_forms_and_fields
        # separately requires the approved DV100 entries.

    @staticmethod
    def source(op):
        return f'''void ordinary_probe(__ubuf__ float *input, __ubuf__ float *output) {{
          __VEC_SCOPE__ {{
            vector_f32 a, b, c;
            vector_bool mask = pset_b32(PAT_ALL);
            vlds(a, input, 0, NORM);
            {op.lower()}(b, a, mask, MODE_ZEROING);
            {op.lower()}(c, b, mask, MODE_ZEROING);
            vsts(c, output, 0, NORM_B32, mask);
          }}
        }}'''

    def test_cce_canonical_and_python_timing(self):
        for op, (latency, _, _) in OPS.items():
            with self.subTest(op=op), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root/'probe.cce'
                source.write_text(self.source(op))
                vf = InputAPI.load_cce(source, 'ordinary_probe')
                instructions = [node for node in vf.context if getattr(node, 'opcode', None)==op]
                self.assertEqual(len(instructions), 2)
                self.assertEqual(instructions[1].inputs[0].value_id, instructions[0].outputs[0].value_id)
                result = CoreVfCostModel(base_dir=ROOT, out_dir=root/'python').run_vf_info(vf)
                self.assertGreater(result['vf_end_cycle'], 0)
                history = json.loads((root/'python/sim_history.json').read_text())
                self.assertEqual(sum(e.get('event')=='start' and e.get('op')==op for e in history), 2)

    @unittest.skipUnless(os.environ.get('VFSIM_STAGE1_NATIVE_RUNNER'), 'set freshly built native runner')
    def test_python_native_cycle_parity(self):
        for op in OPS:
            with self.subTest(op=op), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                source = root/'probe.cce'
                source.write_text(self.source(op))
                vf = InputAPI.load_cce(source, 'ordinary_probe')
                trace = root/'trace.json'
                trace.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                result = CoreVfCostModel(base_dir=ROOT, out_dir=root/'python').run_vf_info(vf)
                native = subprocess.run([os.environ['VFSIM_STAGE1_NATIVE_RUNNER'],
                                         '--trace', str(trace), '--out-dir', str(root/'native')], cwd=ROOT, check=True,
                                        capture_output=True, text=True)
                match = re.search(r'vfEndCycle\s*=\s*(\d+)', native.stdout)
                self.assertIsNotNone(match, native.stdout)
                self.assertEqual(int(match[1]), result['vf_end_cycle'])


if __name__ == '__main__':
    unittest.main()
