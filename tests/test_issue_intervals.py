"""Check persistent self-II against interleaved issue and EXQ prediction."""

from pathlib import Path
import unittest
from unittest.mock import patch

from core.execution.instruction import Uop
from core.params.database import ParamDB
from tests.core_support import component_engine


ROOT = Path(__file__).resolve().parents[1]


class IssueIntervalTests(unittest.TestCase):
    def engine(self):
        db = ParamDB(base_dir=ROOT)
        return component_engine(
            {**db.get_uarch(), "enable_cross_fu_ii": True,
             "exq_issue_inflight_cap_per_port": 0}, db
        )

    def uop(self, engine, identity, op, form="fp32"):
        return Uop(identity, op, form, [], [], [], [],
                   profile=engine.db.resolve_inst(op, form),
                   writeback_banks=frozenset({"vector"}))

    def start(self, engine, uop, cycle, port=0, queued=True):
        self.assertTrue(engine.exu.can_issue(uop, port, cycle, queued=queued))
        engine.exu.start(uop, port, uop.profile.fu_type, cycle, queued=queued)

    def test_interleaving_preserves_self_ii_in_direct_and_queue_modes(self):
        for queued in (False, True):
            for middle_op in ("VADD", "VCMAX"):
                with self.subTest(queued=queued, middle=middle_op):
                    engine = self.engine()
                    first = self.uop(engine, 0, "VEXP")
                    middle = self.uop(engine, 1, middle_op)
                    last = self.uop(engine, 2, "VEXP")
                    self.start(engine, first, 100, queued=queued)
                    self.start(engine, middle, 101, queued=queued)
                    self.assertEqual(engine.exu.next_issue_cycle(last, 0), 104)
                    for cycle in (102, 103):
                        self.assertFalse(engine.exu.can_issue(last, 0, cycle,
                                                             queued=queued))
                    self.assertTrue(engine.exu.can_issue(last, 0, 104,
                                                        queued=queued))

    def test_self_ii_uses_its_own_issue_origin(self):
        engine = self.engine()
        first = self.uop(engine, 0, "VEXP")
        middle = self.uop(engine, 1, "VADD")
        last = self.uop(engine, 2, "VEXP")
        self.start(engine, first, 100)
        self.start(engine, middle, 103)
        self.assertEqual(engine.exu.next_issue_cycle(last, 0), 104)
        self.assertTrue(engine.exu.can_issue(last, 0, 104, queued=True))

    def test_new_same_form_issue_advances_the_self_ii_deadline(self):
        engine = self.engine()
        for identity, op, cycle in [(0, "VEXP", 100), (1, "VADD", 101),
                                    (2, "VEXP", 104), (3, "VADD", 105)]:
            self.start(engine, self.uop(engine, identity, op), cycle)
        candidate = self.uop(engine, 9, "VEXP")
        self.assertFalse(engine.exu.can_issue(candidate, 0, 106, queued=True))
        self.assertEqual(engine.exu.next_issue_cycle(candidate, 0), 108)

    def test_history_is_independent_between_execution_ports(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, "VEXP"), 100)
        self.start(engine, self.uop(engine, 1, "VADD"), 101)
        candidate = self.uop(engine, 9, "VEXP")
        self.assertFalse(engine.exu.can_issue(candidate, 0, 102, queued=True))
        self.assertTrue(engine.exu.can_issue(candidate, 1, 102, queued=True))

    def test_history_tracks_each_opcode_form(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, "VEXP", "fp32"), 100)
        self.start(engine, self.uop(engine, 1, "VEXP", "fp16"), 101)
        self.start(engine, self.uop(engine, 2, "VADD"), 102)
        fp32 = self.uop(engine, 3, "VEXP", "fp32")
        fp16 = self.uop(engine, 4, "VEXP", "fp16")
        self.assertEqual(engine.exu.next_issue_cycle(fp32, 0), 104)
        self.assertEqual(engine.exu.next_issue_cycle(fp16, 0), 109)

    def test_exq_selection_waits_after_same_fu_interleaving(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, "VEXP"), 100)
        self.start(engine, self.uop(engine, 1, "VCMAX"), 101)
        last = self.uop(engine, 2, "VEXP")
        engine.exq.enqueue(last, 0, "SFU", 102, 102)
        self.assertIsNone(engine.exq.pop_ready(0, 102))
        self.assertEqual(engine.exq.pop_ready(0, 104), ("SFU", last))

    def test_exq_prediction_preserves_issued_same_form_history(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, "VEXP"), 100)
        engine.exq.enqueue(self.uop(engine, 1, "VCMAX"), 0, "SFU", 101, 101)
        candidate = self.uop(engine, 9, "VEXP")
        self.assertEqual(engine.exq.predict_issue_cycle(0, candidate, 102), 104)

    def test_exq_prediction_preserves_pending_same_form_history(self):
        engine = self.engine()
        engine.exq.enqueue(self.uop(engine, 0, "VEXP"), 0, "SFU", 100, 100)
        engine.exq.enqueue(self.uop(engine, 1, "VCMAX"), 0, "SFU", 101, 101)
        candidate = self.uop(engine, 9, "VEXP")
        self.assertEqual(engine.exq.predict_issue_cycle(0, candidate, 102), 104)

    def test_exp_div_transition_intervals_remain_directional(self):
        for first_op, middle_op, gap in [("VEXP", "VDIV", 6), ("VDIV", "VEXP", 5)]:
            with self.subTest(first=first_op):
                engine = self.engine()
                self.start(engine, self.uop(engine, 0, first_op), 100)
                middle = self.uop(engine, 1, middle_op)
                self.assertFalse(engine.exu.can_issue(middle, 0, 100 + gap - 1,
                                                     queued=True))
                self.start(engine, middle, 100 + gap)
                candidate = self.uop(engine, 9, first_op)
                self.assertEqual(engine.exu.next_issue_cycle(candidate, 0), 111)

    def test_completion_does_not_erase_an_unexpired_interval(self):
        engine = self.engine()
        original = engine.db.get_ii_for_profiles

        def interval(previous, current):
            if previous is not None and previous.op == current.op == "VEXP":
                return 20
            return original(previous, current)

        with patch.object(engine.db, "get_ii_for_profiles", side_effect=interval):
            first = self.uop(engine, 0, "VEXP")
            self.start(engine, first, 100)
            self.assertEqual(engine.exu.finish(116), [first])
            self.start(engine, self.uop(engine, 1, "VADD"), 117)
            candidate = self.uop(engine, 9, "VEXP")
            self.assertFalse(engine.exu.can_issue(candidate, 0, 118, queued=True))
            self.assertEqual(engine.exu.next_issue_cycle(candidate, 0), 120)


if __name__ == "__main__":
    unittest.main()
