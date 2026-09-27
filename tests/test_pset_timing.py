from pathlib import Path
import unittest
from core.param_db import ParamDB


class PsetTimingTest(unittest.TestCase):
    def test_explicit_pair_overrides_producer_default(self):
        db = ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
        db._fwd_table["PSET_B32.b32"]["VADD.fp32"] = 4
        self.assertEqual(db.get_forwarding_cycles("PSET_B32", "VADD",
                         producer_form="b32", consumer_form="fp32"), 4)
        self.assertEqual(db.get_forwarding_cycles("PSET_B32", "VSTS",
                         producer_form="b32", consumer_form="fp32"), 2)

    def test_assumed_timing(self):
        db = ParamDB(base_dir=str(Path(__file__).resolve().parents[1]))
        for width in (8, 16, 32):
            op, form = f"PSET_B{width}", f"b{width}"
            p = db.resolve_inst(op, form=form)
            self.assertEqual((p.latency, p.fu_type, p.dispatch_exu), (6, "ALU", "EXU01"))
            self.assertEqual(db.get_forwarding_cycles(op, "VADD", producer_form=form, consumer_form="fp32"), 2)
            self.assertEqual(db.get_forwarding_cycles(op, "VSTS", producer_form=form, consumer_form="fp32"), 2)
            self.assertEqual(db.get_ii(op, op, prev_form=form, cur_form=form), 1)
            self.assertEqual(db.get_ii("VADD", op, prev_form="fp32", cur_form=form), 2)
            self.assertEqual(db.get_ii(op, "VADD", prev_form=form, cur_form="fp32"), 1)


if __name__ == "__main__":
    unittest.main()
