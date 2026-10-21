"""Guard timing extraction against store operands and repeated VF boundaries."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills/vfsim-instruction-benchmark/scripts/vector_compute.py"
)
SPEC = importlib.util.spec_from_file_location("vfsim_vector_benchmark", SCRIPT)
PROBE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROBE
SPEC.loader.exec_module(PROBE)
ANALYSIS_SPEC = importlib.util.spec_from_file_location(
    "vfsim_vector_parameter_analysis", SCRIPT.with_name("analyze_vector_compute.py")
)
ANALYSIS = importlib.util.module_from_spec(ANALYSIS_SPEC)
with patch.dict(sys.modules, {"vector_compute": PROBE}):
    ANALYSIS_SPEC.loader.exec_module(ANALYSIS)


class VectorBenchmarkAnalysisTest(unittest.TestCase):
    def synthetic(self, rows, *, regions=1, target="RV_VADD", dispatch=""):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)

            def line(cycle, pc, identity, op, operands, binary, done):
                encoded = f"(Binary: 0x{binary:x}) " if binary is not None else ""
                return f"[info] [{cycle}] (PC: 0x{pc:x}) {encoded}(ID: {identity}) {op} {operands}\n"

            popped = "VF addr: 0x1000,instr_num: 0x10\n" * regions
            (root / "core0.veccore0.instr_popped_log.dump").write_text(
                popped + "".join(line(*row) for row in rows)
            )
            (root / "core0.veccore0.instr_log.dump").write_text(
                "".join(line(row[-1], *row[1:]) for row in rows)
            )
            (root / "core0.veccore0.rvec.EXU.dump").write_text(
                "".join(
                    f"[PERF] [{row[0] + 1}] instr_name {row[3]} instr_id {row[2]} exu_id:0\n"
                    for row in rows
                    if row[3] == target
                )
            )
            (root / "core0.veccore0.rvec.simd.IDU.DISP.dump").write_text(dispatch)
            golden = bytes(64 * regions)
            (root / "output.bin").write_bytes(golden)
            contracts = [
                dict(
                    api="vadd",
                    dtype="uint8",
                    output_offset=64 * i,
                    output_bytes=32,
                    guard_offset=64 * i + 32,
                    guard_bytes=32,
                    target_count=sum(row[3] == target for row in rows) // regions,
                    target_opcodes=[target],
                )
                for i in range(regions)
            ]
            return PROBE.analyze(root, contracts, golden)

    def test_folded_vf_pc_ranges_are_partitioned_by_dynamic_id(self):
        rows = [
            (10, 0x1000, 1, "RV_VADD", "Vd[2], Vn[0], Vm[1]", None, 17),
            (30, 0x1000, 11, "RV_VADD", "Vd[2], Vn[0], Vm[1]", None, 37),
        ]
        dispatch = "instr_id 1 PC 1000\ninstr_id 11 PC 1000\n"
        reports = self.synthetic(rows, regions=2, dispatch=dispatch)
        self.assertEqual(
            [[event["id"] for event in row["events"]] for row in reports], [[1], [11]]
        )
        self.assertTrue(all(row["single_op_sample"] for row in reports))

    def test_loop_carried_raw_edges_follow_dynamic_order(self):
        rows = [
            (10, 0x1008, 2, "RV_VADD", "Vd[2], Vn[0], Vm[1]", None, 17),
            (14, 0x1004, 3, "RV_VADD", "Vd[3], Vn[2], Vm[1]", None, 21),
        ]
        report = self.synthetic(rows)[0]
        edge = next(
            item for item in report["forwarding_observations"] if item["role"] == "Vn"
        )
        self.assertEqual(
            (edge["producer_id"], edge["consumer_id"], edge["gap"]), (2, 3, 4)
        )

    def test_vdupm_missing_text_uses_encoded_registers(self):
        binary = (2 << 25) | (4 << 20) | (1 << 10)
        rows = [(10, 0x1000, 1, "RV_VDUPM", "", binary, 21)]
        operands = self.synthetic(rows, target="RV_VDUPM")[0]["events"][0]["operands"]
        self.assertIn("Vd[2]", operands)
        self.assertIn("Vn[4]", operands)
        self.assertIn("Pg[1]", operands)

    def analyze(self, *, duplicate=False, perf_offset=1):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            golden = bytes(2048)
            (root / "output.bin").write_bytes(golden)
            rows = [
                (1, 0x1000, 1, "RV_PSET", "Pd[0]", 5),
                (1, 0x1004, 2, "RV_VLDI", "Vd[0]", 10),
                (2, 0x1008, 3, "RV_VLDI", "Vd[1]", 11),
                (12, 0x100C, 4, "RV_VADD", "Vd[0], Vn[0], Vm[1], Pg[0]", 19),
                (16, 0x1010, 5, "RV_VSTI", "Vd[0], Pg[0]", 25),
                (15, 0x1014, 6, "RV_VADD", "Vd[0], Vn[0], Vm[1], Pg[0]", 22),
                (15, 0x1018, 7, "RV_SEND", "", 25),
                (16, 0x1018, 7, "RV_SEND", "", 25),
            ]
            if duplicate:
                rows.append(rows[3])

            def line(cycle, pc, identity, op, operands):
                return f"[info] [{cycle:08}] (PC: 0x{pc:x}) RVECEX : (ID: {identity}) {op} {operands}\n"

            (root / "core0.veccore0.instr_popped_log.dump").write_text(
                "VF addr: 0x1000,instr_num: 0x7\n"
                + "".join(line(c, pc, i, op, args) for c, pc, i, op, args, _ in rows)
            )
            (root / "core0.veccore0.instr_log.dump").write_text(
                "".join(
                    line(done, pc, i, op, args) for _, pc, i, op, args, done in rows
                )
            )
            (root / "core0.veccore0.rvec.EXU.dump").write_text(
                "".join(
                    f"[PERF] [{c + perf_offset}] instr_name {op} instr_id {i} exu_id:0\n"
                    for c, _, i, op, _, _ in rows
                    if op == "RV_VADD"
                )
            )
            contracts = [
                {
                    "api": "vadd",
                    "dtype": "fp32",
                    "output_offset": 0,
                    "output_bytes": 1792,
                    "guard_offset": 1792,
                    "guard_bytes": 256,
                    "target_count": 2,
                }
            ]
            return PROBE.analyze(root, contracts, golden)[0]

    def test_store_source_and_send_retries_do_not_create_producers(self):
        report = self.analyze()
        self.assertTrue(report["passed"])
        self.assertTrue(report["single_op_sample"])
        self.assertEqual(report["latencies"], [7])
        edge = next(
            e
            for e in report["forwarding_observations"]
            if e["consumer_id"] == 6 and e["role"] == "Vn"
        )
        self.assertEqual((edge["producer_id"], edge["gap"]), (4, 3))
        self.assertTrue(edge["other_sources_done_before_producer"])
        self.assertEqual(report["independent_same_port_gaps"], [])

    def test_duplicate_compute_events_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "Duplicate dynamic"):
            self.analyze(duplicate=True)

    def test_perf_cycles_must_match_issue_mapping(self):
        with self.assertRaisesRegex(ValueError, "PERF/issue offset"):
            self.analyze(perf_offset=2)

    def test_binary_forwarding_requires_both_vector_sources(self):
        row = {
            "api": "vadd",
            "dtype": "int32",
            "passed": True,
            "single_op_sample": True,
            "run": "probe",
            "memory_events": [],
            "events": [{"latency": 7, "port": 0}, {"latency": 7, "port": 1}],
            "independent_same_port_gaps": [],
            "forwarding_observations": [
                {
                    "producer_op": "RV_VADD",
                    "consumer_op": "RV_VADD",
                    "role": "Vn",
                    "gap": 3,
                    "other_sources_done_before_producer": True,
                }
            ],
        }
        self.assertNotIn("VADD.int32", ANALYSIS.derive([row])["forwarding"])
        row["forwarding_observations"].append(
            {**row["forwarding_observations"][0], "role": "Vm", "gap": 4}
        )
        self.assertEqual(
            ANALYSIS.derive([row])["forwarding"]["VADD.int32"], {"VADD.int32": 4}
        )

    def test_larger_ii_requires_warm_ready_samples_on_both_ports(self):
        row = {
            "api": "vabs",
            "dtype": "int32",
            "passed": True,
            "single_op_sample": True,
            "run": "probe",
            "warm_operands": True,
            "memory_events": [],
            "events": [{"latency": 7, "port": 0}, {"latency": 7, "port": 1}],
            "forwarding_observations": [],
            "independent_same_port_gaps": [
                {"gap": 2, "port": port, "sources_done_before_previous": False}
                for port in (0, 1)
            ],
        }
        self.assertNotIn("VABS.int32", ANALYSIS.derive([row])["ii"])
        for gap in row["independent_same_port_gaps"]:
            gap["sources_done_before_previous"] = True
        self.assertEqual(ANALYSIS.derive([row])["ii"]["VABS.int32"], {"VABS.int32": 2})


if __name__ == "__main__":
    unittest.main()
