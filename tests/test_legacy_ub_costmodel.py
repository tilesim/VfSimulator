import json
from pathlib import Path
import unittest

from api.legacy_ub_costmodel import predict_legacy_ub_instruction
from api.program_adapter import program_to_canonical
from core.program_ir import VfSimInst, VfSimProgram

ROOT=Path(__file__).resolve().parents[1]


class LegacyUbCostModelTest(unittest.TestCase):
    def test_predicts_independently_verified_camodel_runs(self):
        evidence=json.loads((ROOT/'tests/fixtures/legacy_ub/vbs32_measurements.json').read_text())
        for row in evidence['measurements']:
            with self.subTest(case=row['case']):
                result=predict_legacy_ub_instruction('VBITSORT',dtype='fp32',hardware_repeat=row['hardware_repeat'])
                self.assertEqual(result.cycles,row['cycles'])
                self.assertEqual(result.execution_domain,'legacy_VEC')

    def test_rejects_unmeasured_forms_and_invalid_repeat_encoding(self):
        for op,dtype in [('VADDS','fp32'),('VBITSORT','fp16')]:
            with self.assertRaises(NotImplementedError):
                predict_legacy_ub_instruction(op,dtype=dtype,hardware_repeat=32)
        for repeat in (0,256,True,1.5):
            with self.subTest(repeat=repeat),self.assertRaises(ValueError):
                predict_legacy_ub_instruction('VBITSORT',dtype='fp32',hardware_repeat=repeat)

    def test_merge_configs_match_verified_proposals_and_hardware_repeat(self):
        evidence=json.loads((ROOT/'tests/fixtures/legacy_ub/vms4_measurements.json').read_text())
        for row in evidence['measurements']:
            result=predict_legacy_ub_instruction('VMRGSORT4',dtype='fp32',
                hardware_repeat=row['hardware_repeat'],list_lengths=tuple(row['list_lengths']))
            self.assertEqual(result.cycles,row['cycles'])
        with self.assertRaises(NotImplementedError):
            predict_legacy_ub_instruction('VMRGSORT4',dtype='fp32',hardware_repeat=8,list_lengths=(128,)*4)

    def test_remainder_ub_copy_matches_verified_pto_topk_trace(self):
        evidence=json.loads((ROOT/'tests/fixtures/legacy_ub/topk_copy_measurements.json').read_text())
        result=predict_legacy_ub_instruction('COPY_UBUF_TO_UBUF',dtype='fp32',hardware_repeat=1,payload_bytes=160)
        self.assertEqual(result.execution_domain,'legacy_VEC')
        self.assertEqual([result.cycles]*8,[e['busy'] for e in evidence['events']])
        with self.assertRaises(NotImplementedError):
            predict_legacy_ub_instruction('COPY_UBUF_TO_UBUF',dtype='fp32',hardware_repeat=1,payload_bytes=128)

    def test_legacy_ub_instruction_cannot_silently_use_vf_alu_fallback(self):
        for op in ('VBITSORT','VBS32','VMRGSORT4','VMS4'):
            with self.subTest(op=op),self.assertRaisesRegex(ValueError,'legacy UB VEC'):
                program_to_canonical(VfSimProgram([VfSimInst(op,form='fp32',config={'vfsim_fallback':True})]))

    def test_wheel_namespace_exposes_the_same_timing(self):
        from vfsimulator import predict_legacy_ub_instruction as packaged
        self.assertEqual(packaged('VBS32',dtype='fp32',hardware_repeat=32).cycles,547)


if __name__=='__main__':
    unittest.main()
