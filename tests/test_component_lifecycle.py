import unittest
from pathlib import Path

from core.execution.instruction import Uop
from core.params.database import ParamDB
from tests.core_support import component_engine

ROOT = Path(__file__).resolve().parents[1]


class ComponentLifecycleTest(unittest.TestCase):
    def engine(self, **overrides):
        db = ParamDB(base_dir=ROOT)
        return component_engine({**db.get_uarch(), **overrides}, db)

    def uop(self, engine, inst_id, op='VADD'):
        return Uop(inst_id, op, 'fp32', [], [], [], [],
                   profile=engine.db.resolve_inst(op, 'fp32', 'fp32'))

    def test_exq_keeps_blocked_head_and_can_select_other_fu(self):
        engine = self.engine(exq_issue_inflight_cap_per_port=0)
        head = self.uop(engine, 0)
        head.shq_ready_cycle = 110
        tail = self.uop(engine, 1)
        other_fu = self.uop(engine, 2, 'VEXP')
        for u in (head, tail, other_fu):
            engine.exq.enqueue(u, 0, u.profile.fu_type, 100, 100)
        self.assertEqual(engine.exq.pop_ready(0, 100), ('SFU', other_fu))
        self.assertEqual(engine.exq.occupancy(0), 2)
        self.assertIsNone(engine.exq.pop_ready(0, 100))
        self.assertEqual(engine.exq.pop_ready(0, 110), ('ALU', head))
        engine.exu.start(head, 0, 'ALU', 110, queued=True)
        self.assertIsNone(engine.exq.pop_ready(0, 110))
        next_cycle = engine.exu.next_issue_cycle(tail.profile, 0)
        self.assertEqual(engine.exq.pop_ready(0, next_cycle), ('ALU', tail))

    def test_exq_waits_for_reception_and_exu_completion_capacity(self):
        engine = self.engine(exq_issue_inflight_cap_per_port=1)
        candidate = self.uop(engine, 1)
        engine.exq.enqueue(candidate, 0, 'ALU', 103, 103)
        self.assertIsNone(engine.exq.pop_ready(0, 102))
        producer = self.uop(engine, 0)
        engine.exu.start(producer, 0, 'ALU', 100, queued=True)
        self.assertFalse(engine.exu.can_accept(0, queued=True))
        self.assertIsNone(engine.exq.pop_ready(0, 103))
        completed = engine.exu.finish(producer.done_cycle)
        self.assertEqual(completed, [producer])
        self.assertEqual(producer.state, 'done')
        self.assertTrue(engine.exu.can_accept(0, queued=True))
        self.assertEqual(engine.exq.pop_ready(0, producer.done_cycle), ('ALU', candidate))

    def test_exq_admission_can_count_running_instructions(self):
        for counts_inflight in (False, True):
            with self.subTest(counts_inflight=counts_inflight):
                engine = self.engine(exq_depth=2, exq_capacity_counts_inflight=counts_inflight)
                waiting = self.uop(engine, 1)
                engine.exq.enqueue(waiting, 0, 'ALU', 100, 100)
                running = self.uop(engine, 0)
                engine.exu.start(running, 0, 'ALU', 100, queued=True)
                self.assertEqual(engine.exq.occupancy(0), 1)
                self.assertEqual(engine.exq.can_accept(0), not counts_inflight)
                engine.exu.finish(running.done_cycle)
                self.assertTrue(engine.exq.can_accept(0))

    def test_exu_direct_and_queued_issue_keep_distinct_ii_rules(self):
        engine = self.engine(enable_cross_fu_ii=False, exq_issue_inflight_cap_per_port=0)
        producer = self.uop(engine, 0, 'VEXP')
        consumer = self.uop(engine, 1)
        engine.exu.start(producer, 0, 'SFU', 100, queued=True)
        self.assertTrue(engine.exu.can_issue(consumer.profile, 0, 100))
        self.assertFalse(engine.exu.can_issue(consumer.profile, 0, 100, queued=True))
        self.assertFalse(engine.exu.can_issue(consumer.profile, 0, 100, queued=True))
        self.assertEqual(len(engine.exu.running), 1)
        next_cycle = engine.exu.next_issue_cycle(consumer.profile, 0)
        self.assertTrue(engine.exu.can_issue(consumer.profile, 0, next_cycle, queued=True))

    def test_shq_release_is_delayed_and_published_once(self):
        engine = self.engine(shq_release_delay=2, idu_visible_shq_delay=1,
                             enable_credit_visibility_delay=True, enable_shq_credit_model=True)
        u = self.uop(engine, 0)
        capacity = engine.shq.get_free_shq()
        engine.shq.reserve(u)
        self.assertEqual(engine.shq.get_free_shq(), capacity - 1)
        engine.shq.release_credit(u, 100)
        engine.shq.release_credit(u, 100)
        engine.shq.run_shq_release_events(101)
        self.assertEqual(engine.shq.shq_used, 1)
        engine.shq.run_shq_release_events(102)
        self.assertEqual(engine.shq.shq_used, 0)
        self.assertEqual(engine.shq.get_free_shq(), capacity - 1)
        self.assertEqual(engine.shq.update_visibility(103), 1)
        self.assertEqual(engine.shq.get_free_shq(), capacity)
        self.assertEqual(engine.shq.update_visibility(104), 0)


if __name__ == '__main__':
    unittest.main()
