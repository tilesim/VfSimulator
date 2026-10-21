"""Protect mixed-dtype reference layouts and timing eligibility conditions."""

import importlib
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills/vfsim-instruction-benchmark/scripts"
)
with patch.object(sys, "path", [str(SCRIPTS), *sys.path]):
    PROBE = importlib.import_module("vector_conversion")
    ANALYSIS = importlib.import_module("analyze_vector_conversion")
    LOW = importlib.import_module("low_precision")
    SOFTWARE = importlib.import_module("vector_software")


class ConversionBenchmarkAnalysisTest(unittest.TestCase):
    def test_low_precision_known_encodings_and_packed_signed_nibbles(self):
        for dtype, one in [
            ("f8e4m3", 0x38),
            ("f8e5m2", 0x3C),
            ("hif8", 0x08),
            ("f4e1m2x2", 0x04),
            ("f4e2m1x2", 0x02),
        ]:
            with self.subTest(dtype=dtype):
                self.assertEqual(LOW.value(one, dtype), 1.0)
                self.assertEqual(LOW.code(1.0, dtype), one)
        self.assertEqual(LOW.decode(bytes([0x8F, 0x70]), "s4x2"), [-1, -8, 0, 7])
        self.assertEqual(LOW.encode([-1, -8, 0, 7], "s4x2"), bytes([0x8F, 0x70]))

    def test_integer_to_float32_rounding_handles_halfway_and_directions(self):
        for sign in (-1, 1):
            value = sign * 16777217
            self.assertEqual(SOFTWARE.round_float32(value, "ROUND_R"), sign * 16777216)
            self.assertEqual(SOFTWARE.round_float32(value, "ROUND_A"), sign * 16777218)
            self.assertEqual(
                SOFTWARE.round_float32(value, "ROUND_F"),
                -16777218 if sign < 0 else 16777216,
            )
            self.assertEqual(
                SOFTWARE.round_float32(value, "ROUND_C"),
                -16777216 if sign < 0 else 16777218,
            )
            self.assertEqual(SOFTWARE.round_float32(value, "ROUND_Z"), sign * 16777216)

    def test_parts_select_interleaved_lanes(self):
        raw = struct.pack("<128h", *range(128))
        case = {"api": "vcvt", "form": "s16_to_s32", "part": "PART_ODD"}
        self.assertEqual(
            struct.unpack("<64i", PROBE.reference(raw, case)), tuple(range(1, 128, 2))
        )
        raw = struct.pack("<64i", *range(64))
        case = {"api": "vcvt", "form": "s32_to_u8", "pp": "PART_P2"}
        expected = bytearray(256)
        expected[2::4] = bytes(range(64))
        self.assertEqual(PROBE.reference(raw, case), bytes(expected))
        self.assertEqual(PROBE.reference(raw, case, True)[128:], bytes(128))

    def test_narrowing_saturates_or_wraps_and_rounding_is_explicit(self):
        raw = struct.pack("<128h", *([-2, 257] * 64))
        case = {"api": "vcvt", "form": "s16_to_u8"}
        self.assertEqual(PROBE.reference(raw, case)[:8], b"\x00\x00\xff\x00" * 2)
        self.assertEqual(
            PROBE.reference(raw, {**case, "saturation": "RS_DISABLE"})[:8],
            b"\x00\x00\x01\x00" * 2,
        )
        self.assertEqual(PROBE.scalar_convert(-2.5, "fp32", "int32", "ROUND_R"), -2)
        self.assertEqual(PROBE.scalar_convert(-2.5, "fp32", "int32", "ROUND_A"), -3)
        self.assertEqual(PROBE.scalar_convert(-2.5, "fp32", "int32", "ROUND_F"), -3)

    def test_pack_checks_defined_lanes_and_preserves_each_unwritten_byte(self):
        source, data, golden, contracts = PROBE.make_batch(
            [{"api": "vpack", "form": "u16_to_u8"}]
        )
        self.assertIn("vor(observed", source)
        self.assertEqual(
            contracts[0]["defined_output_byte_counts"],
            [128, 128, 128, 128, 64, 32, 128],
        )
        for slot, count in enumerate(contracts[0]["defined_output_byte_counts"]):
            self.assertEqual(
                golden[256 * slot + count : 256 * (slot + 1)],
                data[256 * slot + count : 256 * (slot + 1)],
            )
        self.assertEqual(golden[1792:2048], data[1792:2048])

    def test_failed_or_expanded_calls_cannot_supply_parameters(self):
        row = {
            "api": "vcvt",
            "form": "f32_to_s32",
            "result_dtype": "int32",
            "passed": False,
            "single_op_sample": True,
        }
        self.assertFalse(ANALYSIS.derive([row])["isa"])
        self.assertFalse(
            ANALYSIS.derive([{**row, "passed": True, "single_op_sample": False}])["isa"]
        )

    def test_source_readiness_and_port_competition_are_required(self):
        row = {
            "api": "vcvt",
            "form": "f32_to_s32",
            "result_dtype": "int32",
            "passed": True,
            "single_op_sample": True,
            "run": "probe",
            "target_opcodes": ["RV_VCVT_F2I"],
            "events": [{"latency": 8, "port": 0, "cycle": 10}],
            "port_pressure_events": [],
            "independent_same_port_gaps": [],
            "forwarding_observations": [
                {
                    "producer_op": "RV_VCVT_F2I",
                    "consumer_op": "RV_VCVT_F2I",
                    "producer_role": "Vd",
                    "role": "Vn",
                    "other_sources_ready_at_producer_issue": False,
                    "gap": 4,
                }
            ],
        }
        report = ANALYSIS.derive([row])
        self.assertFalse(report["isa"])
        self.assertFalse(report["forwarding"])
        row["events"].append({"latency": 8, "port": 1, "cycle": 10})
        row["forwarding_observations"][0]["other_sources_ready_at_producer_issue"] = (
            True
        )
        report = ANALYSIS.derive([row])
        self.assertEqual(
            report["isa"]["VCVT_F32_TO_S32.f32_to_s32"]["dispatch_exu"], "EXU01"
        )
        self.assertEqual(
            report["forwarding"]["VCVT_F32_TO_S32.f32_to_s32"],
            {"VCVT_F32_TO_S32.f32_to_s32": 4},
        )


if __name__ == "__main__":
    unittest.main()
