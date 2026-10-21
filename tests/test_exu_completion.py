"""Check per-EXU completion bandwidth without changing instruction latency."""

from pathlib import Path
import unittest

from core.execution.instruction import Uop
from core.params.database import ParamDB
from tests.core_support import component_engine


ROOT = Path(__file__).resolve().parents[1]


class ExuCompletionTests(unittest.TestCase):
    def engine(self, **overrides):
        db = ParamDB(base_dir=ROOT)
        return component_engine(
            {**db.get_uarch(), 'exq_issue_inflight_cap_per_port': 0,
             'enable_cross_fu_ii': True, **overrides}, db)

    def uop(self, engine, identity, op):
        return Uop(identity, op, 'fp32', [], [], [], [],
                   profile=engine.db.resolve_inst(op, 'fp32'))

    def start(self, engine, uop, cycle, port=0, queued=True):
        self.assertTrue(engine.exu.can_issue(uop.profile, port, cycle, queued=queued))
        engine.exu.start(uop, port, uop.profile.fu_type, cycle, queued=queued)

    def test_current_issue_cycle_checks_future_completion_slot(self):
        for queued in (False, True):
            for cross in (False, True):
                with self.subTest(queued=queued, cross=cross):
                    engine = self.engine(enable_cross_fu_ii=cross)
                    self.start(engine, self.uop(engine, 0, 'VEXP'), 100, queued=queued)
                    candidate = self.uop(engine, 1, 'VADD')
                    self.assertFalse(engine.exu.can_issue(candidate.profile, 0, 109,
                                                         queued=queued))
                    self.start(engine, candidate, 110, queued=queued)
                    self.assertEqual((candidate.start_cycle, candidate.done_cycle), (110, 117))
                    self.assertEqual(candidate.done_cycle - candidate.start_cycle,
                                     candidate.profile.latency)

    def test_consecutive_reserved_slots_delay_more_than_one_cycle(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        self.start(engine, self.uop(engine, 1, 'VCMAX'), 101)
        candidate = self.uop(engine, 2, 'VADD')
        for cycle in (109, 110):
            self.assertFalse(engine.exu.can_issue(candidate.profile, 0, cycle, queued=True))
        self.start(engine, candidate, 111)
        self.assertEqual(candidate.done_cycle, 118)

    def test_older_instruction_keeps_its_completion_reservation(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        self.start(engine, self.uop(engine, 1, 'VADD'), 101)
        profile = engine.db.resolve_inst('VMUL', 'fp32')
        self.assertFalse(engine.exu.can_issue(profile, 0, 108, queued=True))
        self.assertTrue(engine.exu.can_issue(profile, 0, 109, queued=True))

    def test_two_execution_ports_can_complete_in_the_same_cycle(self):
        engine = self.engine()
        first = self.uop(engine, 0, 'VEXP')
        second = self.uop(engine, 1, 'VADD')
        self.start(engine, first, 100, port=0)
        self.start(engine, second, 109, port=1)
        self.assertEqual(first.done_cycle, second.done_cycle)
        self.assertEqual(set(u.inst_id for u in engine.exu.finish(116)), {0, 1})

    def test_port_selection_uses_completion_slots_at_the_candidate_cycle(self):
        for greedy in (False, True):
            with self.subTest(greedy=greedy):
                engine = self.engine(enable_exq_greedy_balance=greedy)
                self.start(engine, self.uop(engine, 0, 'VEXP'), 100, port=0)
                self.start(engine, self.uop(engine, 1, 'VEXP'), 101, port=1)
                profile = engine.db.resolve_inst('VADD', 'fp32')
                self.assertEqual(engine.exu.pick_port(profile, 109, [False, False]), 1)

    def test_exq_prediction_checks_reserved_completions_with_or_without_a_tail(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                engine = self.engine()
                self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
                if pending:
                    tail = self.uop(engine, 1, 'VADD')
                    engine.exq.enqueue(tail, 0, 'ALU', 107, 107)
                profile = engine.db.resolve_inst('VADD', 'fp32')
                self.assertEqual(engine.exq.predict_issue_cycle(0, profile, 109), 110)
                self.assertEqual(engine.exq.predict_issue_cycle(0, profile, 109), 110)

    def test_exq_waits_for_completion_slot(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        candidate = self.uop(engine, 1, 'VADD')
        engine.exq.enqueue(candidate, 0, 'ALU', 109, 109)
        self.assertIsNone(engine.exq.pop_ready(0, 109))
        self.assertEqual(engine.exq.pop_ready(0, 110), ('ALU', candidate))

    def test_pipelined_independent_instructions_still_overlap(self):
        engine = self.engine()
        for identity in range(6):
            self.start(engine, self.uop(engine, identity, 'VADD'), 100 + identity)
        self.assertEqual(len(engine.exu.running), 6)
        self.assertEqual(len({u.done_cycle for u in engine.exu.running.values()}), 6)

    def test_start_rejects_collision_without_mutating_the_candidate(self):
        engine = self.engine()
        first = self.uop(engine, 0, 'VEXP')
        self.start(engine, first, 100)
        candidate = self.uop(engine, 1, 'VADD')
        with self.assertRaises(RuntimeError):
            engine.exu.start(candidate, 0, 'ALU', 109, queued=True)
        self.assertIsNone(candidate.start_cycle)
        self.assertEqual(len(engine.exu.running), 1)
        self.assertEqual(engine.exu.finish(116), [first])
        self.start(engine, candidate, 117)
        self.assertEqual(engine.exu.finish(124), [candidate])
        self.assertEqual(engine.exu.exq_inflight, [0, 0])


if __name__ == '__main__':
    unittest.main()
