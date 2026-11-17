"""Acceleration must preserve readiness and optional trace semantics."""
import tempfile
import unittest
from unittest.mock import patch
from core.execution.instruction import Uop
from core.params.database import ParamDB
from core.simulator import CoreVfCostModel
from core.trace.recorder import TraceRecorder
from tests.core_support import ROOT, component_engine, parse_memory_body


class AccelerationTests(unittest.TestCase):
    def test_pending_source_and_dynamic_queue_floor(self):
        db = ParamDB(base_dir=ROOT)
        regs = component_engine(db.base_uarch, db).registers
        u = Uop(1, "VADD", "fp32", [], [], ["v0", "v1"], [])
        regs.preg_producer["v0"] = ("VADD", "fp32", 100, "COMPUTE")
        regs.preg_pending.add("v1")
        with patch.object(regs, "_ready_time_for_src", wraps=regs._ready_time_for_src) as resolve:
            self.assertEqual(regs.ready_cycle(u), 10**9)
            self.assertEqual(regs.ready_cycle(u), 10**9)
            self.assertEqual(resolve.call_count, 1)
            regs.preg_pending.remove("v1")
            regs.preg_producer["v1"] = ("VADD", "fp32", 120, "COMPUTE")
            ready = regs.ready_cycle(u)
            self.assertGreaterEqual(ready, 120)
            u.shq_ready_cycle = ready + 20
            self.assertEqual(regs.ready_cycle(u), ready + 20)
            self.assertEqual(resolve.call_count, 2)

    def test_disabled_trace_does_not_inspect_instruction(self):
        trace = TraceRecorder(None)
        trace.enabled = False
        trace._log_start_simple(None)
        trace._log_done_simple(None)
        self.assertEqual(trace.cyc_start_log, [])
        self.assertEqual(trace.cyc_done_log, [])

    def test_headless_matches_traced_prediction(self):
        vf = parse_memory_body("vlds(a, p, 0, NORM); vadd(b, a, a, mask); vsts(b, q, 0, NORM_B32, mask);")
        with tempfile.TemporaryDirectory() as output:
            traced = CoreVfCostModel(ROOT, output).run_vf_info(vf)
            headless = CoreVfCostModel(ROOT, None).run_vf_info(vf)
        for result in (traced, headless):
            result.pop("trace_path", None)
            result.pop("results_dir", None)
        self.assertEqual(traced, headless)
