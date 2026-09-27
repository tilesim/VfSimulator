import json
from pathlib import Path
import tempfile
import unittest

from api.frontend import DEFAULT_INSTRUCTION_CATALOG
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]
BATCH2 = ROOT / 'tests/fixtures/ordinary_instructions/batch2.cce'


class OrdinaryVorVciOnboardingTest(unittest.TestCase):
    def test_archived_cce_calls_keep_real_forms(self):
        expected = {'or_eight': ('VOR', 'b32', 8), 'ci_eight': ('VCI', 'int32', 8)}
        for kernel, (opcode, form, count) in expected.items():
            with self.subTest(kernel=kernel):
                vf = InputAPI.load_cce(BATCH2, kernel)
                selected = [node for node in vf.context if getattr(node, 'opcode', None) == opcode]
                self.assertEqual(len(selected), count)
                self.assertEqual({node.form for node in selected}, {form})
                if opcode == 'VCI':
                    self.assertEqual({vf.values[node.inputs[0].value_id].dtype
                                      for node in selected}, {'int32'})
        self.assertEqual(DEFAULT_INSTRUCTION_CATALOG.lookup('VOR').signature, 'binary')
        self.assertEqual(DEFAULT_INSTRUCTION_CATALOG.lookup('VCI').signature, 'index_generate')

    def test_vci_default_order_and_cast_index(self):
        source = '''void probe(__ubuf__ int *out) {
            __VEC_SCOPE__ {
                vector_s32 a, b;
                vci(a, (int32_t)-17, INC_ORDER);
                vci(b, 3);
            }
        }'''
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / 'probe.cce'
            path.write_text(source)
            vf = InputAPI.load_cce(path, 'probe')
        self.assertEqual([(node.opcode, node.form) for node in vf.context],
                         [('VCI', 'int32'), ('VCI', 'int32')])

    def test_measured_params_and_unmeasured_warnings(self):
        db = ParamDB(base_dir=str(ROOT))
        isa = json.loads((ROOT / 'configs/isa.json').read_text())['instructions']
        for op, form, latency, dtype in [('VOR', 'b32', 6, 'uint32'),
                                         ('VCI', 'int32', 7, 'int32')]:
            fields = isa[op]['forms'][form]
            self.assertEqual(set(fields), {'latency', 'EXU', 'dispatch_exu',
                                            'src_dtypes', 'dst_dtypes', 'dtype'})
            self.assertEqual(fields['dtype'], dtype)
            profile = db.resolve_inst(op, form, dtype)
            self.assertEqual(profile.latency, latency)
            self.assertEqual(profile.dispatch_exu, 'EXU01')
            self.assertEqual(profile.fu_type, 'ALU')
        self.assertEqual(db.get_forwarding_cycles('VLDS', 'VOR', 'uint32',
                         producer_form='uint32', consumer_form='b32'), 6)
        self.assertEqual(db.get_forwarding_cycles('VOR', 'VSTS', 'uint32',
                         producer_form='b32', consumer_form='uint32'), 4)
        self.assertEqual(db.get_forwarding_cycles('VCI', 'VSTS', 'int32',
                         producer_form='int32', consumer_form='int32'), 5)
        self.assertEqual(db.get_ii('VCI', 'VCI', 'int32'), 2)
        self.assertEqual(db.get_ii('VOR', 'VOR', 'uint32',
                         prev_form='b32', cur_form='b32'), 1)
        for op, form in [('VLDS', 'uint32'), ('VSTS', 'uint32'), ('VSTS', 'int32')]:
            db.resolve_inst(op, form)
        warnings = db.get_warnings()
        self.assertEqual({(w['op'], w['form']) for w in warnings
                          if w['kind'] == 'unsupported_isa_form'},
                         {('VLDS', 'uint32'), ('VSTS', 'uint32'), ('VSTS', 'int32')})
        self.assertEqual([(w['prev'], w['cur']) for w in warnings
                          if w['kind'] == 'missing_ii_pair'],
                         [('VOR.b32', 'VOR.b32')])

    def test_cce_to_core_runs_real_opcodes(self):
        cases = {
            'VOR': '''void probe(__ubuf__ unsigned *in, __ubuf__ unsigned *out) {
                __VEC_SCOPE__ {
                    vector_bool p = pset_b32(PAT_ALL);
                    vector_u32 a, b, y;
                    vlds(a, in, 0, NORM);
                    vlds(b, in, 64, NORM);
                    vor(y, a, b, p, MODE_ZEROING);
                    vsts(y, out, 0, NORM_B32, p);
                }
            }''',
            'VCI': '''void probe(__ubuf__ int *out) {
                __VEC_SCOPE__ {
                    vector_bool p = pset_b32(PAT_ALL);
                    vector_s32 y;
                    vci(y, (int32_t)-17, INC_ORDER);
                    vsts(y, out, 0, NORM_B32, p);
                }
            }''',
        }
        for opcode, source in cases.items():
            with self.subTest(opcode=opcode), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                path = root / 'probe.cce'
                path.write_text(source)
                vf = InputAPI.load_cce(path, 'probe')
                selected = [n for n in vf.context if getattr(n, 'opcode', None) == opcode]
                self.assertEqual(len(selected), 1)
                if opcode == 'VCI':
                    self.assertEqual(vf.values[selected[0].inputs[0].value_id].dtype, 'int32')
                result = CoreVfCostModel(base_dir=ROOT, out_dir=root / 'run').run_vf_info(vf)
                self.assertGreater(result['vf_end_cycle'], 0)
                history = json.loads((root / 'run/sim_history.json').read_text())
                self.assertEqual(sum(e.get('event') == 'start' and e.get('op') == opcode
                                     for e in history), 1)


if __name__ == '__main__':
    unittest.main()
