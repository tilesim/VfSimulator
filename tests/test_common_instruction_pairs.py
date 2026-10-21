"""Exercise mixed arithmetic and predicate forwarding in both simulator cores."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.input_api import InputAPI
from core.params.database import ParamDB
from core.simulator import CoreVfCostModel
from tests.core_support import ROOT, records


class CommonInstructionPairTests(unittest.TestCase):
    def test_mixed_chains_and_native_issue_cycles(self):
        for dtype in ["fp32", "fp16", "bf16"]:
            with self.subTest(dtype=dtype), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                fixture = (
                    ROOT / f"VFtest/cce/contracts/common_instruction_pairs_{dtype}.cce"
                )
                vf = InputAPI.load_cce(fixture)
                model = CoreVfCostModel(base_dir=ROOT, out_dir=root / "python")
                result = model.run_vf_info(vf)
                starts = records(root / "python/start_by_cycle.json")
                by_op = {row["op"]: row for row in starts}
                db = ParamDB(base_dir=ROOT)
                pairs = [("VADDS", "VMUL"), ("VMAX", "VSUB"), ("VCMP_GT", "VSEL")]
                if dtype != "bf16":
                    pairs.append(("VMUL", "VRELU"))
                for p, c in pairs:
                    gap = db.get_forwarding_cycles(
                        p, c, dtype, producer_form=dtype, consumer_form=dtype
                    )
                    self.assertGreaterEqual(by_op[c]["cy"] - by_op[p]["cy"], gap)
                self.assertEqual(db.get_ii("VADD", "VMUL", dtype), 1)
                self.assertEqual(db.get_ii("VMUL", "VADD", dtype), 2)
                self.assertEqual(db.get_warnings(), [])
                runner = os.environ.get("VFSIM_NATIVE_RUNNER")
                if runner:
                    completed = subprocess.run(
                        [
                            runner,
                            "--cce",
                            str(fixture),
                            "--out-dir",
                            str(root / "native"),
                            "--max-cycles",
                            "10000",
                        ],
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn(
                        f"vfEndCycle={result['vf_end_cycle']}", completed.stdout
                    )
                    compare = lambda rows: sorted(
                        (r["inst_id"], r["op"], r["cy"]) for r in rows
                    )
                    self.assertEqual(
                        compare(starts),
                        compare(records(root / "native/start_by_cycle.json")),
                    )


if __name__ == "__main__":
    unittest.main()
