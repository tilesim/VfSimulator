import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from api.simulator_costmodel import CoreVfCostModel
from core.ooo_factory import create_ooo_core
from core.param_db import ParamDB
from core.program_analysis import ProgramAnalyzer
from main import scan_instruction_fallback_warnings, write_warning_log


ROOT = Path(__file__).resolve().parents[1]


class FallbackDtypeCleanupTest(unittest.TestCase):
    def test_cost_model_accepts_legacy_keyword_without_changing_fallback(self):
        self.assertEqual(CoreVfCostModel().fallback_dtype, "fp32")
        self.assertEqual(CoreVfCostModel(fallback_dtype="fp16").fallback_dtype, "fp16")
        self.assertEqual(CoreVfCostModel(dtype="fp16").fallback_dtype, "fp16")
        with self.assertRaises(ValueError):
            CoreVfCostModel(dtype="fp32", fallback_dtype="fp16")

    def test_explicit_forms_override_core_fallback(self):
        db = ParamDB(base_dir=str(ROOT))
        core = create_ooo_core(db.get_uarch(), db, dtype="fp16")
        self.assertEqual(core.fallback_dtype, "fp16")
        for opcode, form in (("VADD", "fp32"), ("VADD", "fp16"),
                             ("VCVT_F32_TO_F16", "f32_to_f16")):
            with self.subTest(opcode=opcode, form=form):
                profile = core._profile(opcode, form)
                self.assertEqual(profile.requested_form, form)
                self.assertEqual(profile.latency, db.get_inst_form(opcode, form=form)["latency"])
        self.assertEqual(core._profile("VADD").requested_form, "fp16")

    def test_warning_scan_preserves_instruction_forms(self):
        class RecordingDB:
            def __init__(self):
                self.queries = []

            def get_inst_form(self, op, *, form, dtype):
                self.queries.append((op, form, dtype))

        db = RecordingDB()
        program = [{"type": "inst", "op": "VADD", "form": form}
                   for form in ("fp32", "fp16", None)]
        scan_instruction_fallback_warnings(program, db, "fp16")
        self.assertEqual(db.queries, [("VADD", "fp32", "fp16"),
                                      ("VADD", "fp16", "fp16"),
                                      ("VADD", "fp16", "fp16")])

    def test_old_capacity_heuristic_removed_and_fallback_warnings_preserved(self):
        self.assertFalse(hasattr(ProgramAnalyzer, "collect_vreg_capacity_warnings"))
        warnings = [{"kind": "missing_forwarding_pair"}]
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            write_warning_log(directory, [])
            path = Path(directory) / "model_warnings.json"
            self.assertFalse(path.exists())
            write_warning_log(directory, warnings)
            payload = json.loads(path.read_text())
        self.assertEqual(payload["instruction_fallback_warnings"], warnings)
        self.assertEqual(payload["vreg_capacity_warnings"], [])


if __name__ == "__main__":
    unittest.main()
