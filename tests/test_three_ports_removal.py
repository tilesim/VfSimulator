import contextlib
import io
import json
import os
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api.input_api import InputAPI
from api.common.diagnostics import VfInfoValidationError
from api.common.uarch_validation import UARCH_FIELD_SPECS, validate_uarch_overrides
from core.params.database import ParamDB
from core.params.timing import TimingQuery
from core.simulator import CoreVfCostModel
from main import build_arg_parser

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/canonical_vf_info/v1_valid_loop.json"


class ThreePortsRemovalTest(unittest.TestCase):
    def test_model_constructor_and_cli_reject_removed_switch(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), self.assertRaises(TypeError):
                CoreVfCostModel(three_ports=enabled)
        parser = build_arg_parser()
        self.assertNotIn("--three-ports", parser.format_help())
        with contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as captured:
            parser.parse_args(["--three-ports"])
        self.assertEqual(captured.exception.code, 2)
        self.assertIn("unrecognized arguments", stderr.getvalue())

    def test_canonical_json_and_builder_reject_removed_mode(self):
        self.assertNotIn("three_ports_mode", UARCH_FIELD_SPECS)
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                result = validate_uarch_overrides({"three_ports_mode": enabled})
                self.assertEqual({error.code for error in result.errors}, {"deprecated_uarch_field"})
                self.assertTrue(all("A6" in error.message for error in result.errors))
                builder = InputAPI.new_builder(uarch={"three_ports_mode": enabled})
                with self.assertRaises(VfInfoValidationError):
                    builder.build()
                payload = json.loads(FIXTURE.read_text())
                payload["uarch"] = {"three_ports_mode": enabled}
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / "input.json"
                    path.write_text(json.dumps(payload))
                    with self.assertRaises(VfInfoValidationError):
                        InputAPI.load_json(path)

    def test_direct_canonical_input_cannot_enable_removed_mode_or_extra_ports(self):
        vf = InputAPI.load_json(FIXTURE)
        overrides = ({"three_ports_mode": True}, {"three_ports_mode": False},
                     {"issue_ports": 3}, {"issue_ports": 4})
        for uarch in overrides:
            with self.subTest(uarch=uarch), self.assertRaisesRegex(ValueError, "A6"):
                CoreVfCostModel(out_dir=None).run_vf_info(replace(vf, uarch=uarch))

    def test_uarch_file_cannot_enable_removed_mode_or_extra_ports(self):
        vf = InputAPI.load_json(FIXTURE)
        cfg = json.loads((ROOT / "configs/uarch.json").read_text())
        for overrides in ({"three_ports_mode": True}, {"three_ports_mode": False}, {"issue_ports": 3}):
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "uarch.json"
                path.write_text(json.dumps({**cfg, **overrides}))
                with patch.dict(os.environ, {"UARCH_JSON_PATH": str(path)}):
                    with self.assertRaisesRegex(ValueError, "A6"):
                        CoreVfCostModel(out_dir=None).run_vf_info(vf)

    def test_default_two_port_model_and_schedule_remain_unchanged(self):
        vf = InputAPI.load_json(FIXTURE)
        result = CoreVfCostModel(out_dir=None).run_vf_info(vf)
        self.assertEqual((result["cycles_executed"], result["vf_end_cycle"]), (35, 47))
        db = ParamDB()
        timing = TimingQuery(db.get_uarch(), db)
        for op, form, expected in (("VADD", "fp32", [0, 1]), ("VPACK", "b32", [0])):
            with self.subTest(op=op):
                profile = db.resolve_inst(op, form, form)
                self.assertEqual(timing._eligible_exu_ports(op, form, profile), expected)


if __name__ == "__main__":
    unittest.main()
