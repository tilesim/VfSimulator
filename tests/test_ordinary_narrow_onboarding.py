import json
from pathlib import Path
import tempfile
import unittest

from api.frontend import DEFAULT_INSTRUCTION_CATALOG
from api.input_api import InputAPI
from api.simulator_costmodel import CoreVfCostModel
from core.param_db import ParamDB


ROOT = Path(__file__).resolve().parents[1]
BATCH3 = ROOT / "tests/fixtures/ordinary_instructions/batch3.cce"
MEASURED = {
    "VDUP": ("int8", "int8", 6),
    "VBR": ("int8", "int8", 6),
    "VCVT_S32_TO_U8": ("s32_to_u8", "uint8", 7),
}


class OrdinaryNarrowOnboardingTest(unittest.TestCase):
    def test_archived_cce_calls_have_exact_opcode_and_form(self):
        expected = {
            "dup_s8": ("VDUP", "int8", 2),
            "br_s8": ("VBR", "int8", 2),
            "narrow_s32": ("VCVT_S32_TO_U8", "s32_to_u8", 4),
        }
        for kernel, (opcode, form, count) in expected.items():
            with self.subTest(kernel=kernel):
                vf = InputAPI.load_cce_canonical(BATCH3, kernel)
                selected = [
                    node for node in vf.context
                    if getattr(node, "opcode", None) == opcode
                ]
                self.assertEqual(len(selected), count)
                self.assertEqual({node.form for node in selected}, {form})
                if opcode in {"VDUP", "VBR"}:
                    self.assertEqual(
                        {vf.values[node.inputs[0].value_id].dtype
                         for node in selected},
                        {"int8"},
                    )

        self.assertEqual(DEFAULT_INSTRUCTION_CATALOG.lookup("VBR").signature,
                         "broadcast")
        self.assertEqual(
            DEFAULT_INSTRUCTION_CATALOG.lookup("VCVT_S32_TO_U8").signature,
            "vcvt_s32_to_u8",
        )

    def test_only_measured_dv100_latency_and_ports_are_configured(self):
        isa = json.loads(
            (ROOT / "configs/isa.json").read_text()
        )["instructions"]
        db = ParamDB(base_dir=str(ROOT))
        for opcode, (form, dtype, latency) in MEASURED.items():
            with self.subTest(opcode=opcode):
                fields = isa[opcode]["forms"][form]
                self.assertEqual(
                    set(fields),
                    {"latency", "EXU", "dispatch_exu", "src_dtypes",
                     "dst_dtypes", "dtype"},
                )
                profile = db.resolve_inst(opcode, form, dtype)
                self.assertEqual(profile.latency, latency)
                self.assertEqual(profile.fu_type, "ALU")
                self.assertEqual(profile.dispatch_exu, "EXU01")
        self.assertEqual(db.get_warnings(), [])

    def test_unmeasured_forwarding_and_ii_keep_fallback_warnings(self):
        db = ParamDB(base_dir=str(ROOT))
        for opcode, (form, dtype, _) in MEASURED.items():
            db.get_ii(
                opcode, opcode, dtype,
                prev_form=form, cur_form=form,
            )
            db.get_forwarding_cycles(
                opcode, "VSTS", dtype,
                producer_form=form,
                consumer_form=("uint8" if dtype == "uint8" else "int8"),
            )
        warnings = db.get_warnings()
        self.assertEqual(
            {warning["prev"] for warning in warnings
             if warning["kind"] == "missing_ii_pair"},
            {f"{opcode}.{form}" for opcode, (form, _, _) in MEASURED.items()},
        )
        self.assertEqual(
            {warning["producer"] for warning in warnings
             if warning["kind"] == "missing_forwarding_pair"},
            {f"{opcode}.{form}" for opcode, (form, _, _) in MEASURED.items()},
        )

    def test_archived_cce_reaches_core_with_real_opcodes(self):
        for kernel, opcode in {
            "dup_s8": "VDUP",
            "br_s8": "VBR",
            "narrow_s32": "VCVT_S32_TO_U8",
        }.items():
            with self.subTest(kernel=kernel), tempfile.TemporaryDirectory() as name:
                root = Path(name)
                vf = InputAPI.load_cce_canonical(BATCH3, kernel)
                result = CoreVfCostModel(
                    base_dir=ROOT,
                    out_dir=root,
                ).run_canonical_vf_info(vf)
                self.assertGreater(result["vf_end_cycle"], 0)
                history = json.loads((root / "sim_history.json").read_text())
                self.assertTrue(any(
                    event.get("event") == "start" and event.get("op") == opcode
                    for event in history
                ))


if __name__ == "__main__":
    unittest.main()
