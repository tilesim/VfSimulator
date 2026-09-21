from pathlib import Path
import tempfile
import unittest

from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]
BATCH2 = ROOT / "tests/fixtures/ordinary_instructions/batch2.cce"


class OrdinaryBf16OnboardingTest(unittest.TestCase):
    def test_archived_bf16_vf_runs(self):
        vf = InputAPI.load_cce_canonical(BATCH2, "bf_corrected")
        instructions = [(node.opcode, node.form) for node in vf.context if hasattr(node, "opcode")]
        self.assertEqual(instructions, [
            ("VLDS", "bf16"),
            ("VCVT_BF16_TO_F32", "bf16_to_f32"),
            ("VCVT_BF16_TO_F32", "bf16_to_f32"),
            ("VSTS", "fp32"),
            ("VSTS", "fp32"),
        ])
        with tempfile.TemporaryDirectory() as directory:
            result = CoreVfCostModel(base_dir=ROOT, out_dir=Path(directory)).run_canonical_vf_info(vf)
            self.assertGreater(result["vf_end_cycle"], 0)

    def test_rejects_part_from_a_different_conversion_family(self):
        source = """void probe() { __VEC_SCOPE__ {
            vector_bool p = pset_b16(PAT_ALL);
            vector_bf16 src;
            vector_f32 dst;
            vcvt(dst, src, p, PART_P0, MODE_ZEROING);
        }}"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probe.cce"
            path.write_text(source)
            with self.assertRaises(ValueError):
                InputAPI.load_cce_canonical(path, "probe")

    def test_measured_latency_does_not_silence_unmeasured_pairs(self):
        db = ParamDB(base_dir=str(ROOT))
        profile = db.resolve_inst(
            "VCVT_BF16_TO_F32", "bf16_to_f32", "fp32"
        )
        self.assertEqual(profile.latency, 7)
        self.assertEqual(profile.fu_type, "ALU")
        self.assertEqual(profile.dispatch_exu, "EXU01")
        db.get_ii(
            "VCVT_BF16_TO_F32", "VCVT_BF16_TO_F32", "fp32",
            prev_form="bf16_to_f32", cur_form="bf16_to_f32",
        )
        db.get_forwarding_cycles(
            "VCVT_BF16_TO_F32", "VSTS", "fp32",
            producer_form="bf16_to_f32", consumer_form="fp32",
        )
        self.assertEqual(
            {warning["kind"] for warning in db.get_warnings()},
            {"missing_ii_pair", "missing_forwarding_pair"},
        )


if __name__ == "__main__":
    unittest.main()
