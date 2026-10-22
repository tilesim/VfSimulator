"""Reduction encoding selection and mixed-width source/result contracts."""
from pathlib import Path
import tempfile
import unittest

from api.input_api import InputAPI
from core.params.database import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class StructuralVectorExtensionsTest(unittest.TestCase):
    def test_reduction_form_uses_source_width(self):
        vf = InputAPI.load_cce(ROOT/"VFtest/cce/contracts/vector_reductions.cce")
        reductions = [n for n in vf.context if n.opcode == "VCADD"]
        self.assertEqual([n.form for n in reductions], ["int16", "uint16", "fp32"])
        self.assertEqual([(vf.values[n.inputs[0].value_id].dtype,
                           vf.values[n.outputs[0].value_id].dtype) for n in reductions],
                         [("int16", "int32"), ("uint16", "uint32"), ("fp32", "fp32")])
        self.assertEqual([n.opcode for n in vf.context if n.opcode.startswith("VCG")],
                         ["VCGADD", "VCGMIN", "VCGMAX"])

    def test_invalid_integer_sum_destination_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"invalid.cce"
            path.write_text('''void vf(__ubuf__ int16_t *input) {__VEC_SCOPE__ {
                vector_s16 a, out; vector_bool p=pset_b16(PAT_ALL);
                vlds(a,input,0,NORM); vcadd(out,a,p);
            }}''')
            with self.assertRaisesRegex(ValueError, "requires a int32 destination"):
                InputAPI.load_cce(path)

    def test_measured_profiles_and_result_aggregate_forwarding(self):
        db = ParamDB(base_dir=ROOT)
        for op, form, latency, ii, fwd, ports in [
            ("VCADD", "int16", 16, 1, 13, "EXU0_ONLY"),
            ("VCGADD", "uint16", 13, 1, 10, "EXU0_ONLY"),
            ("VCGMAX", "fp16", 14, 2, 11, "EXU0_ONLY"),
            ("VCPADD", "fp32", 12, 1, 9, "EXU0_ONLY"),
            ("VMULL", "uint32", 8, 2, 5, "EXU01"),
            ("VINTLV", "bf16", 11, 2, 9, "EXU0_ONLY"),
            ("VSEL", "fp16", 6, 1, 2, "EXU01"),
        ]:
            with self.subTest(op=op, form=form):
                profile = db.resolve_inst(op, form)
                self.assertEqual((profile.latency, profile.dispatch_exu), (latency, ports))
                self.assertEqual(db.get_ii(op, op, form), ii)
                self.assertEqual(db.get_forwarding_cycles(op, op, form), fwd)
        self.assertEqual(db.get_warnings(), [])


if __name__ == "__main__":
    unittest.main()
