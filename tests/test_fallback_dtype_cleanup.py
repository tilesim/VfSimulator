import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from core.simulator import CoreVfCostModel
from tests.core_support import component_engine
from core.params.database import ParamDB


ROOT = Path(__file__).resolve().parents[1]


class FallbackDtypeCleanupTest(unittest.TestCase):
    def test_cost_model_uses_explicit_fallback_dtype(self):
        self.assertEqual(CoreVfCostModel().fallback_dtype, "fp32")
        self.assertEqual(CoreVfCostModel(fallback_dtype="fp16").fallback_dtype, "fp16")
        with self.assertRaises(TypeError):
            CoreVfCostModel(dtype="fp16")

    def test_explicit_forms_override_core_fallback(self):
        db = ParamDB(base_dir=str(ROOT))
        core = component_engine(db.get_uarch(), db, dtype="fp16")
        self.assertEqual(core.timing.fallback_dtype, "fp16")
        for opcode, form in (("VADD", "fp32"), ("VADD", "fp16"),
                             ("VCVT_F32_TO_F16", "f32_to_f16")):
            with self.subTest(opcode=opcode, form=form):
                profile = core.timing._profile(opcode, form)
                self.assertEqual(profile.requested_form, form)
                self.assertEqual(profile.latency, db.get_inst_form(opcode, form=form)["latency"])
        self.assertEqual(core.timing._profile("VADD").requested_form, "fp16")




if __name__ == "__main__":
    unittest.main()
