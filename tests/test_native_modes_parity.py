"""Compare component scheduling in Python and C++ across supported modes."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.input_api import InputAPI
from api.json.serialization import canonical_vf_info_to_dict
from core.simulator import CoreVfCostModel

ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


@unittest.skipUnless(RUNNER, "VFSIM_NATIVE_RUNNER is not configured")
class NativeModesParityTests(unittest.TestCase):
    def compare(self, vf, overrides, folder):
        accepted = InputAPI.from_canonical(replace(vf, uarch=overrides))
        folder.mkdir(parents=True)
        result = CoreVfCostModel(base_dir=ROOT, out_dir=folder / "python").run_vf_info(accepted)
        payload = folder / "canonical.json"
        payload.write_text(json.dumps(canonical_vf_info_to_dict(accepted)), encoding="utf-8")
        proc = subprocess.run([RUNNER, "--trace", str(payload), "--out-dir", str(folder / "native")],
                              check=True, capture_output=True, text=True, timeout=60)
        fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
        self.assertEqual(int(fields["cyclesExecuted"]), result["cycles_executed"])
        self.assertEqual(int(fields["vfEndCycle"]), result["vf_end_cycle"])
        self.assertEqual(int(fields["linearInstCount"]), result["linear_inst_count"])
        for name in ("start_by_cycle.json", "done_by_cycle.json"):
            def events(backend):
                return sorted((row["cy"], row["inst_id"], row["op"])
                              for row in map(json.loads, (folder / backend / name).read_text().splitlines()))
            self.assertEqual(events("python"), events("native"), name)

    def test_frontend_transport_credits_and_theoretical_modes(self):
        vf = InputAPI.load_json(ROOT / "VFtest/canonical/fixtures/v1_valid_loop.json")
        cases = [(f"delay_{delay}_explicit_{explicit}", {
            "idu_to_ooo_delay": delay, "use_explicit_idu_credit_bank": explicit, "vreg_num": 2})
                 for delay in (0, 1, 7) for explicit in (False, True)]
        cases += [
            ("direct", {"enable_isu_queue_model": False}),
            ("credit_gate", {"global_shq_preg_gate": True, "vreg_num": 1}),
            ("theoretical_vloop", {"theoretical_limit_mode": True, "theoretical_limit_vloop_only": True}),
            ("theoretical_direct", {"theoretical_limit_mode": True, "theoretical_limit_vloop_only": True,
                                    "theoretical_limit_direct_issue": True, "theoretical_limit_legacy_forwarding": True}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for name, overrides in cases:
                with self.subTest(mode=name):
                    self.compare(vf, overrides, Path(directory) / name)

    def test_compute_queue_policies_and_execution_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mixed.cce"
            source.write_text(
                'void vf(__ubuf__ float *p,__ubuf__ float *q){__VEC_SCOPE__{'
                'vector_f32 a,b,c,d;vector_bool mask=pset_b32(PAT_ALL);'
                'for(int i=0;i<4;++i){vlds(a,p,64,NORM,POST_UPDATE);'
                'vadd(b,a,a,mask);vexp(c,b,mask);vcmax(d,c,mask,MODE_ZEROING);'
                'vsts(d,q,0,NORM_B32,mask);}}}', encoding="utf-8")
            vf = InputAPI.load_cce(source)
            cases = [
                ("normal", {}),
                ("fifo", {"shq_exq_dispatch_policy": "fu_round_robin_fifo"}),
                ("static_rr", {"shq_exq_dispatch_policy": "predicted_issue", "shq_exq_static_rr": True}),
                ("predicted", {"shq_exq_dispatch_policy": "predicted_issue", "predict_exq_issue_with_cross_fu": True}),
                ("capacity", {"exq_capacity_counts_inflight": True, "exq_depth": 2}),
                ("single_inflight", {"exq_issue_inflight_cap_per_port": 1}),
                ("cross_fu_queue", {"enable_cross_fu_ii": True}),
            ]
            cases += [(f"direct_{greedy}_{cross}", {"enable_isu_queue_model": False,
                       "enable_exq_greedy_balance": greedy, "enable_cross_fu_ii": cross})
                      for greedy in (False, True) for cross in (False, True)]
            for name, overrides in cases:
                with self.subTest(mode=name):
                    self.compare(vf, overrides, root / name)
