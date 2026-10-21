"""Protect contention conclusions from readiness, occupancy and index errors."""

import importlib
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills/vfsim-instruction-benchmark/scripts"
)
sys.path.insert(0, str(SCRIPTS))
ANALYSIS = importlib.import_module("analyze_fu_contention")
PROBE = importlib.import_module("fu_contention")
sys.path.remove(str(SCRIPTS))


def event(identity, op, cycle, latency=7):
    return dict(
        id=identity,
        op=op,
        cycle=cycle,
        done=cycle + latency,
        latency=latency,
        port=0,
        source_done_cycle=0,
        hardware_source_ready_cycle=0,
        dispatch_cycle=0,
        exq_recv_cycle=0,
        hardware_blocks=[],
    )


def row(name, events, sequence):
    return dict(
        name=name,
        run="test",
        replica=0,
        passed=True,
        single_op_sample=True,
        events=events,
        sequence=sequence,
        supporting_compute_events=[],
        independent_same_port_gaps=[
            dict(
                previous_id=a["id"],
                current_id=b["id"],
                port=0,
                gap=b["cycle"] - a["cycle"],
            )
            for a, b in zip(events, events[1:])
        ],
    )


class FuContentionAnalysisTests(unittest.TestCase):
    def test_forwarded_source_must_be_ready_by_next_issue_slot(self):
        a = event(1, "RV_VCMAX", 10)
        b = event(2, "RV_VADD", 13)
        b.update(
            source_done_cycle=15, hardware_source_ready_cycle=11, exq_recv_cycle=11
        )
        self.assertTrue(ANALYSIS.qualified_after(a, b))
        b["hardware_source_ready_cycle"] = 12
        self.assertFalse(ANALYSIS.qualified_after(a, b))

    def test_self_ii_wait_is_not_a_cross_pair_interval(self):
        control = row(
            "vexp_self",
            [event(1, "RV_VEXP", 10), event(2, "RV_VEXP", 14)],
            ["vexp", "vexp"],
        )
        mixed = row(
            "vexp_vadd_vexp",
            [event(3, "RV_VEXP", 20), event(4, "RV_VADD", 21), event(5, "RV_VEXP", 24)],
            ["vexp", "vadd", "vexp"],
        )
        summary = ANALYSIS.summarize([control, mixed])
        pair = next(
            p
            for p in summary["adjacent_pairs"]
            if (p["previous"], p["current"]) == ("RV_VADD", "RV_VEXP")
        )
        self.assertIsNone(pair["minimum"])
        self.assertEqual(summary["triples"][0]["outer_gap_counts"], {4: 1})

    def resource_row(self):
        a = event(1, "RV_VCMAX", 10, 16)
        b = event(2, "RV_VADD", 22, 7)
        b.update(
            source_done_cycle=20, hardware_source_ready_cycle=11, exq_recv_cycle=11
        )
        b["hardware_blocks"] = [
            dict(cycle=19, port=0, reason="DST_PORT_LIMIT", line=42)
        ]
        return row("vcmax_then_vadd", [a, b], ["vcmax", "vadd"])

    def test_idle_ready_port_can_still_have_destination_conflict(self):
        result = ANALYSIS.resource_counterexamples(
            [self.resource_row()], {"RV_VCMAX": 1, "RV_VADD": 1}
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["proposed_done"], 26)
        self.assertEqual(result[0]["completion_conflicts"][0]["id"], 1)

    def test_busy_port_and_unready_sources_do_not_prove_missing_resource(self):
        r = self.resource_row()
        r["events"].append(event(3, "RV_VMUL", 19))
        self.assertEqual(
            ANALYSIS.resource_counterexamples([r], {"RV_VCMAX": 1, "RV_VADD": 1}), []
        )
        r = self.resource_row()
        r["events"][1]["hardware_source_ready_cycle"] = 20
        self.assertEqual(
            ANALYSIS.resource_counterexamples([r], {"RV_VCMAX": 1, "RV_VADD": 1}), []
        )

    def test_reduction_index_is_checked_as_integer_bits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "cases.json").write_text(json.dumps([]))
            golden = struct.pack("<fI", 1.5, 3)
            (root / "golden.bin").write_bytes(golden)
            for index, expected in [(3, True), (4, False)]:
                (root / "output.bin").write_bytes(struct.pack("<fI", 1.5, index))
                with patch.object(
                    PROBE.compute,
                    "analyze",
                    return_value=[dict(passed=True, exact_regions=[[0, 8]])],
                ):
                    self.assertEqual(PROBE.analyze(root)[0]["passed"], expected)


if __name__ == "__main__":
    unittest.main()
