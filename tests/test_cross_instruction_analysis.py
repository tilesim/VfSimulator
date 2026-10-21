"""Reject confounded cross-instruction measurements before configuration writes."""

import importlib
from pathlib import Path
import sys
import unittest

SCRIPTS = (
    Path(__file__).resolve().parents[1] / "skills/vfsim-instruction-benchmark/scripts"
)
sys.path.insert(0, str(SCRIPTS))
ANALYSIS = importlib.import_module("analyze_instruction_pairs")
sys.path.remove(str(SCRIPTS))


def fwd_row(role="Vn", gap=3, *, streams=6, passed=True):
    return dict(
        kind="fwd",
        previous="vadd",
        current="vmul",
        source_role=role,
        previous_key="VADD.fp32",
        current_key="VMUL.fp32",
        passed=passed,
        single_op_sample=True,
        run="test",
        replica=0,
        streams=streams,
        forwarding_observations=[
            dict(
                producer_id=1,
                consumer_id=2,
                producer_op="RV_VADD",
                consumer_op="RV_VMUL",
                role=role,
                gap=gap,
                other_sources_done_before_producer=True,
            )
        ]
        * 2,
    )


def ii_row(*, gap=1, ready=True):
    events = []
    gaps = []
    for port in [0, 1]:
        a = port * 10 + 1
        b = a + 1
        events += [
            dict(id=a, op="RV_VADD", port=port, cycle=10),
            dict(id=b, op="RV_VMUL", port=port, cycle=10 + gap),
        ]
        gaps.append(
            dict(
                previous_id=a,
                current_id=b,
                port=port,
                gap=gap,
                sources_done_before_previous=ready,
            )
        )
    return dict(
        kind="ii",
        previous="vadd",
        current="vmul",
        previous_key="VADD.fp32",
        current_key="VMUL.fp32",
        passed=True,
        single_op_sample=True,
        run="test",
        replica=0,
        order="alternating",
        events=events,
        independent_same_port_gaps=gaps,
    )


class CrossInstructionAnalysisTests(unittest.TestCase):
    def derive(self, rows, table=None):
        isa = dict(
            instructions={
                op: dict(forms=dict(fp32=dict(dispatch_exu="EXU01")))
                for op in ["VADD", "VMUL"]
            }
        )
        return ANALYSIS.derive(rows, isa, table or {})[0]

    def test_pair_covers_all_consumer_source_positions(self):
        self.assertEqual(self.derive([fwd_row()])["forwarding"], {})
        parameters = self.derive([fwd_row("Vn", 3), fwd_row("Vm", 4)])
        self.assertEqual(parameters["forwarding"], {"VADD.fp32": {"VMUL.fp32": 4}})

    def test_numeric_failure_and_unready_sources_cannot_set_forwarding(self):
        a = fwd_row("Vn")
        b = fwd_row("Vm", passed=False)
        self.assertEqual(self.derive([a, b])["forwarding"], {})
        b = fwd_row("Vm")
        b["forwarding_observations"][0]["other_sources_done_before_producer"] = False
        self.assertEqual(self.derive([a, b])["forwarding"], {})

    def test_store_burst_requires_single_pair_control(self):
        row = fwd_row("Vd", 5)
        row.update(current="vsts", current_key="VSTS.fp32")
        for edge in row["forwarding_observations"]:
            edge["consumer_op"] = "RV_VSTS"
        self.assertEqual(self.derive([row])["forwarding"], {})
        row["streams"] = 1
        self.assertEqual(
            self.derive([row])["forwarding"], {"VADD.fp32": {"VSTS.fp32": 5}}
        )

    def test_ii_requires_repeated_ready_samples_on_both_ports(self):
        self.assertEqual(self.derive([ii_row()])["ii"], {})
        self.assertEqual(self.derive([ii_row(gap=2, ready=False)] * 2)["ii"], {})
        result = self.derive([ii_row(), ii_row()])
        self.assertEqual(result["ii"], {"VADD.fp32": {"VMUL.fp32": 1}})
        self.assertNotIn("VMUL.fp32", result["ii"])

    def test_earlier_consumer_self_ii_is_not_a_cross_pair_interval(self):
        row = ii_row(gap=3)
        row["events"] += [
            dict(id=100 + port, op="RV_VMUL", port=port, cycle=9) for port in [0, 1]
        ]
        self.assertEqual(
            self.derive([row, row], {"VMUL.fp32": {"VMUL.fp32": 4}})["ii"], {}
        )

    def test_unit_interval_proves_lower_bound_with_forwarded_shared_gate(self):
        rows = [ii_row(ready=False), ii_row(ready=False)]
        self.assertEqual(self.derive(rows)["ii"], {"VADD.fp32": {"VMUL.fp32": 1}})

    def test_late_dispatch_cannot_calibrate_an_issue_interval(self):
        row = ii_row(gap=2)
        for event in row["events"]:
            if event["op"] == "RV_VMUL":
                event["dispatch_cycle"] = 10
        self.assertEqual(self.derive([row, row])["ii"], {})


if __name__ == "__main__":
    unittest.main()
