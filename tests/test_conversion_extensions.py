"""Mixed-dtype conversion signatures, explicit views and value dependencies."""
from pathlib import Path
import tempfile
import unittest

from api.input_api import InputAPI
from api.common import DEFAULT_INSTRUCTION_CATALOG
from core.params.database import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class ConversionExtensionsTest(unittest.TestCase):
    def parse(self, body):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'conversion.cce'
            path.write_text('void vf(__ubuf__ uint8_t *input){__VEC_SCOPE__{'+body+'}}')
            return InputAPI.load_cce(path)

    def test_signatures_follow_each_conversion_pair(self):
        vf = InputAPI.load_cce(ROOT/'VFtest/cce/contracts/vector_conversions.cce')
        converted = [i for i in vf.context if i.opcode.startswith('VCVT')]
        self.assertEqual([(i.opcode,i.form) for i in converted], [
            ('VCVT_F16_TO_S16','f16_to_s16'), ('VCVT_S16_TO_F16','s16_to_f16'),
            ('VCVT_F16_TO_F32','f16_to_f32'), ('VCVT_F32_TO_S32','f32_to_s32'),
            ('VCVT_S32_TO_U16','s32_to_u16'), ('VCVT_U8_TO_U32','u8_to_u32'),
            ('VCVT_BF16_TO_F16','bf16_to_f16')])
        self.assertTrue(all(len(i.inputs)==2 for i in converted))

    def test_required_and_disallowed_configuration_arguments(self):
        cases = [
            ('vector_f32 a;vector_s32 b;', 'vcvt(b,a,p,ROUND_R,RS_DISABLE,PART_EVEN);'),
            ('vector_f16 a;vector_f32 b;', 'vcvt(b,a,p,ROUND_R,RS_DISABLE,PART_EVEN);'),
            ('vector_f16 a;vector_s16 b;', 'vcvt(b,a,p,ROUND_O,RS_ENABLE);'),
            ('vector_u8 a;vector_u32 b;', 'vcvt(b,a,p,PART_EVEN);'),
            ('vector_bf16 a;vector_f16 b;', 'vcvt(b,a,p,ROUND_R,RS_ENABLE);'),
            ('vector_s32 a;vector_u16 b;', 'vcvt(b,a,p,RS_ENABLE,PART_EVEN,MODE_MERGING);'),
        ]
        for declaration, call in cases:
            with self.subTest(call=call), self.assertRaises(ValueError):
                self.parse(declaration+'vector_bool p=pset_b8(PAT_ALL);'+call)

    def test_explicit_conversion_view_keeps_underlying_value_dependency(self):
        vf = self.parse('''vector_f32 a;vector_s32 b,c; vector_bool p=pset_b8(PAT_ALL);
            vlds(a,(__ubuf__ float *)input,0,NORM);
            vcvt(b,a,p,ROUND_R,RS_ENABLE);
            vcvt(c,(vector_f32)b,p,ROUND_R,RS_ENABLE);
        ''')
        first, second = [i for i in vf.context if i.opcode.startswith('VCVT')]
        self.assertEqual(second.form, 'f32_to_s32')
        self.assertEqual(second.inputs[0].value_id,first.outputs[0].value_id)
        self.assertEqual(vf.values[second.inputs[0].value_id].dtype,'int32')

    def test_pack_views_select_form_and_preserve_in_place_rename(self):
        vf = self.parse('''vector_f16 a; vector_bool p=pset_b8(PAT_ALL);
            vlds(a,(__ubuf__ half *)input,0,NORM);
            vpack((vector_u16 &)a,(vector_u32)a,HIGHER,MODE_ZEROING);
        ''')
        pack = vf.context[-1]
        self.assertEqual((pack.opcode,pack.form),('VPACK','u32_to_u16'))
        self.assertNotEqual(pack.inputs[0].value_id,pack.outputs[0].value_id)
        self.assertEqual(vf.values[pack.inputs[0].value_id].logical_id,
                         vf.values[pack.outputs[0].value_id].logical_id)
        with self.assertRaises(ValueError):
            self.parse('vector_u16 a;vector_u8 b;vpack(b,a,UPPER);')

    def test_physical_opcode_rejects_mismatched_conversion_types(self):
        with self.assertRaisesRegex(ValueError,'requires f32_to_f16'):
            self.parse('vector_f16 a;vector_f32 b;vector_bool p=pset_b8(PAT_ALL);'
                       'vcvt_f32_to_f16(b,a,p,ROUND_R,RS_DISABLE,PART_EVEN);')

    def test_64_bit_forms_and_shared_pack_value_views(self):
        vf=InputAPI.load_cce(ROOT/'VFtest/cce/contracts/vector_conversion_views.cce')
        self.assertEqual([i.form for i in vf.context if 's64' in i.form],
                         ['f32_to_s64','s32_to_s64','s64_to_f32','s64_to_s32'])
        self.assertEqual([i.form for i in vf.context if i.opcode in ('VPACK','VUNPACK')],
                         ['u32_to_u16','u16_to_u32'])

    def test_calibrated_conversion_profiles_and_pack_cross_pairs(self):
        db=ParamDB(base_dir=ROOT)
        for op, form, latency, ii, fwd in [
            ('VCVT_F32_TO_F16','f32_to_f16',7,1,3),
            ('VCVT_F16_TO_S32','f16_to_s32',8,2,4),
            ('VCVT_S64_TO_S32','s64_to_s32',7,2,3),
            ('VPACK','u16_to_u8',11,1,8),
            ('VUNPACK','s8_to_s16',11,1,8),
        ]:
            with self.subTest(op=op,form=form):
                self.assertEqual(db.resolve_inst(op,form).latency,latency)
                self.assertEqual(db.get_ii(op,op,form),ii)
                self.assertEqual(db.get_forwarding_cycles(op,op,form),fwd)
        self.assertEqual(db.get_forwarding_cycles('VPACK','VSSTB',producer_form='u32_to_u16',consumer_form='b16'),9)
        self.assertEqual(db.get_forwarding_cycles('VCVT_F32_TO_F16','VPACK',producer_form='f32_to_f16',consumer_form='u32_to_u16'),4)
        self.assertEqual(db.get_warnings(),[])

    def test_unpack_has_no_predicate_or_mode_argument(self):
        self.assertEqual(len(DEFAULT_INSTRUCTION_CATALOG.lookup('VUNPACK').input_operands),1)
        with self.assertRaises(ValueError):
            self.parse('vector_s8 a;vector_s16 b;vector_bool p=pset_b8(PAT_ALL);'
                       'vunpack(b,a,LOWER,p);')


if __name__ == '__main__':
    unittest.main()
