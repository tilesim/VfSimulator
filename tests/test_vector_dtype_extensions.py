"""Vector dtype coverage, accumulator edges and mixed register-bank results."""

from pathlib import Path
import tempfile
import unittest

from api.common.schema import StorageKind
from api.common.symbols import compact_dtype, dtype_element_bytes, normalize_dtype
from api.input_api import InputAPI
from core.params.database import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class VectorDtypeExtensionsTest(unittest.TestCase):
    def test_normalized_scalar_vector_types_and_widths(self):
        for spelling, dtype, compact, width in [
            ("s16", "int16", "s16", 2),
            ("s64", "int64", "s64", 8),
            ("u64", "uint64", "u64", 8),
            ("f8e4m3", "f8e4m3", "f8e4m3", 1),
            ("f4e1m2x2", "f4e1m2x2", "f4e1m2x2", 1),
        ]:
            with self.subTest(spelling=spelling):
                self.assertEqual(normalize_dtype(spelling), dtype)
                self.assertEqual(compact_dtype(spelling), compact)
                self.assertEqual(dtype_element_bytes(spelling), width)

    def test_accumulator_and_unsigned_shift_keep_operand_types(self):
        vf = InputAPI.load_cce(ROOT / "VFtest/cce/contracts/ordinary_vector_types.cce")
        shift = next(n for n in vf.context if n.opcode == "VSHL")
        self.assertEqual(shift.form, "uint16")
        self.assertEqual(
            [vf.values[o.value_id].dtype for o in shift.inputs],
            ["uint16", "int16", "bool"],
        )
        acc = next(n for n in vf.context if n.opcode == "VMULA")
        old, new = vf.values[acc.inputs[0].value_id], vf.values[acc.outputs[0].value_id]
        self.assertEqual(old.logical_id, new.logical_id)
        self.assertNotEqual(old.definition_id, new.definition_id)

    def test_carry_results_and_multiply_long_are_versioned(self):
        vf = InputAPI.load_cce(ROOT / "VFtest/cce/contracts/ordinary_vector_types.cce")
        first = next(n for n in vf.context if n.opcode == "VADDC")
        second = next(n for n in vf.context if n.opcode == "VADDCS")
        self.assertEqual(
            [vf.values[o.value_id].storage for o in first.outputs],
            [StorageKind.PREDICATE_REGISTER, StorageKind.REGISTER],
        )
        self.assertEqual(second.inputs[-2].value_id, first.outputs[0].value_id)
        self.assertNotEqual(second.outputs[0].value_id, first.outputs[0].value_id)
        mul = next(n for n in vf.context if n.opcode == "VMULL")
        self.assertEqual(len(mul.outputs), 2)
        self.assertNotEqual(mul.outputs[0].value_id, mul.outputs[1].value_id)

    def test_bf16_scalar_multiply_lowers_to_broadcast_and_multiply(self):
        vf = InputAPI.load_cce(ROOT / "VFtest/cce/contracts/ordinary_vector_types.cce")
        dup = next(n for n in vf.context if n.opcode == "VDUPS")
        mul = next(n for n in vf.context if n.opcode == "VMUL")
        self.assertEqual((dup.form, mul.form), ("bf16", "bf16"))
        self.assertEqual(dup.outputs[0].value_id, mul.inputs[1].value_id)
        self.assertEqual(dup.source_location, mul.source_location)
        self.assertNotIn("VMULS", [n.opcode for n in vf.context])

    def test_post_update_uses_signed_sixteen_bit_element_width(self):
        source = """void vf(__ubuf__ int16_t *ptr) {__VEC_SCOPE__ {
            vector_s16 a; vlds(a, ptr, 128, NORM, POST_UPDATE);
        }}"""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "probe.cce"
            path.write_text(source)
            vf = InputAPI.load_cce(path)
        memory = vf.context[0].inputs[0].memory_access
        self.assertEqual(memory.post_update_delta_bytes.constant, 256)

    def test_measured_dtype_profiles_and_mask_forwarding(self):
        db = ParamDB(base_dir=ROOT)
        for op, form, latency, ii, fwd in [
            ("VNEG", "fp16", 5, 1, 2),
            ("VNEG", "int16", 7, 2, 3),
            ("VADD", "bf16", 7, 1, 3),
            ("VADD", "uint16", 7, 2, 3),
            ("VDIV", "fp16", 22, 8, 19),
            ("VDIV", "fp32", 17, 4, 14),
            ("VNOT", "bf16", 6, 1, 2),
            ("VCMP_EQ", "bf16", 6, 1, 2),
        ]:
            with self.subTest(op=op, form=form):
                profile = db.resolve_inst(op, form)
                self.assertEqual(profile.latency, latency)
                self.assertEqual(profile.dispatch_exu, "EXU01")
                self.assertEqual(db.get_ii(op, op, form), ii)
                self.assertEqual(db.get_forwarding_cycles(op, op, form), fwd)
        self.assertEqual(db.resolve_inst("VDUPS", "bf16").latency, 6)
        self.assertEqual(db.resolve_inst("VSTS", "int16").latency, 9)
        self.assertEqual(db.get_forwarding_cycles("VCMP_EQ", "VCMP_EQ", "fp32"), 2)
        self.assertEqual(db.get_warnings(), [])


if __name__ == "__main__":
    unittest.main()
