import unittest

from core.param_db import ParamDB
from tests import test_predicate_logic as helpers
from tests.test_predicate_registers import parse


class InterleaveTest(unittest.TestCase):
    def test_pair_outputs_inplace_loop_and_consumers(self):
        runner = helpers.PredicateLogicTest()
        for op in ("vintlv", "vdintlv"):
            with self.subTest(op=op):
                rows = runner.run_program(
                    "mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);vlds(y,a,64,NORM);"
                    f"for(int i=0;i<3;++i){{{op}(x,y,x,y);"
                    "vsts(x,out,128*i,NORM_B32,mask);"
                    "vsts(y,out,128*i+64,NORM_B32,mask);}")
                loads = [r for r in rows if r["op"] == "VLDS"]
                pairs = [r for r in rows if r["op"] == op.upper()]
                stores = [r for r in rows if r["op"] == "VSTS"]
                self.assertEqual(len(pairs), 3)
                previous = [r["preg_dst"][0] for r in loads]
                for index, pair in enumerate(pairs):
                    self.assertEqual(pair["preg_src"], previous)
                    self.assertEqual(len(set(pair["preg_dst"])), 2)
                    self.assertFalse(set(pair["preg_src"]) & set(pair["preg_dst"]))
                    for lane in (0, 1):
                        store = stores[2 * index + lane]
                        self.assertEqual(store["preg_src"][0], pair["preg_dst"][lane])
                        self.assertGreaterEqual(store["cy"], pair["cy"] + 9)
                    previous = pair["preg_dst"]

    def test_parameters_and_invalid_calls(self):
        db = ParamDB(base_dir=str(helpers.ROOT))
        for op in ("VINTLV", "VDINTLV"):
            for form in ("fp32", "b32"):
                profile = db.resolve_inst(op, form, "fp32")
                self.assertEqual(profile.latency, 11)
                self.assertEqual(profile.dispatch_exu, "EXU0_ONLY")
                self.assertEqual(db.get_forwarding_cycles(
                    op, "VADD", producer_form=form, consumer_form="fp32"), 8)
                for consumer_form in ("fp32", "b32"):
                    self.assertEqual(db.get_forwarding_cycles(
                        op, "VSTS", producer_form=form, consumer_form=consumer_form), 9)
            for call in (f"{op}(x,x,x,y);", f"{op}(x,y,x);", f"{op}(x,y,x,p);",
                         f"{op}(x,y,x,y,p);", f"vector_f16 z;{op}(x,y,z,z);"):
                with self.subTest(call=call), self.assertRaises(ValueError):
                    parse(call)
