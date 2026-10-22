"""Check per-EXU completion bandwidth without changing instruction latency."""

from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
import unittest

from core.execution.instruction import DynamicInstruction, Uop
from core.params.database import ParamDB
from tests.core_support import component_engine, engine_for, parse_vector_body


ROOT = Path(__file__).resolve().parents[1]


class ExuCompletionTests(unittest.TestCase):
    def engine(self, **overrides):
        db = ParamDB(base_dir=ROOT)
        return component_engine(
            {**db.get_uarch(), 'exq_issue_inflight_cap_per_port': 0,
             'enable_cross_fu_ii': True, **overrides}, db)

    def uop(self, engine, identity, op, *, banks=("vector",), form="fp32"):
        return Uop(identity, op, form, [], [], [], [],
                   profile=engine.db.resolve_inst(op, form),
                   writeback_banks=frozenset(banks))

    def start(self, engine, uop, cycle, port=0, queued=True):
        self.assertTrue(engine.exu.can_issue(uop, port, cycle, queued=queued))
        engine.exu.start(uop, port, uop.profile.fu_type, cycle, queued=queued)

    def test_current_issue_cycle_checks_future_completion_slot(self):
        for queued in (False, True):
            for cross in (False, True):
                with self.subTest(queued=queued, cross=cross):
                    engine = self.engine(enable_cross_fu_ii=cross)
                    self.start(engine, self.uop(engine, 0, 'VEXP'), 100, queued=queued)
                    candidate = self.uop(engine, 1, 'VADD')
                    self.assertFalse(engine.exu.can_issue(candidate, 0, 109,
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
            self.assertFalse(engine.exu.can_issue(candidate, 0, cycle, queued=True))
        self.start(engine, candidate, 111)
        self.assertEqual(candidate.done_cycle, 118)

    def test_older_instruction_keeps_its_completion_reservation(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        self.start(engine, self.uop(engine, 1, 'VADD'), 101)
        candidate = self.uop(engine, 9, 'VMUL')
        self.assertFalse(engine.exu.can_issue(candidate, 0, 108, queued=True))
        self.assertTrue(engine.exu.can_issue(candidate, 0, 109, queued=True))

    def test_two_execution_ports_can_complete_in_the_same_cycle(self):
        engine = self.engine()
        first = self.uop(engine, 0, 'VEXP')
        second = self.uop(engine, 1, 'VADD')
        self.start(engine, first, 100, port=0)
        self.start(engine, second, 109, port=1)
        self.assertEqual(first.done_cycle, second.done_cycle)
        self.assertEqual(set(u.inst_id for u in engine.exu.finish(116)), {0, 1})

    def test_vector_and_predicate_can_complete_on_the_same_port(self):
        for queued in (False, True):
            for cross in (False, True):
                with self.subTest(queued=queued, cross=cross):
                    engine = self.engine(enable_cross_fu_ii=cross)
                    vector = self.uop(engine, 0, 'VEXP')
                    predicate = self.uop(engine, 1, 'VCMP_GT', banks=('predicate',))
                    self.start(engine, vector, 100, queued=queued)
                    self.start(engine, predicate, 110, queued=queued)
                    self.assertEqual(vector.done_cycle, predicate.done_cycle)
                    self.assertEqual({u.inst_id for u in engine.exu.finish(116)}, {0, 1})
                    self.assertEqual(engine.exu.exq_inflight, [0, 0])
                    # Released slots can be reused independently for either bank.
                    for banks in (('vector',), ('predicate',), ('vector', 'predicate')):
                        candidate = self.uop(engine, 2, 'VADD', banks=banks)
                        self.assertEqual(engine.exu.next_completion_issue_cycle(candidate, 0, 109), 109)

    def test_two_predicate_results_still_require_separate_cycles(self):
        engine = self.engine()
        first = self.uop(engine, 0, 'PAND', banks=('predicate',), form='b32')
        candidate = self.uop(engine, 1, 'VCMP_GT', banks=('predicate',))
        self.start(engine, first, 100)
        self.assertFalse(engine.exu.can_issue(candidate, 0, 103, queued=True))
        with self.assertRaises(RuntimeError):
            engine.exu.start(candidate, 0, 'ALU', 103, queued=True)
        self.assertIsNone(candidate.start_cycle)
        self.start(engine, candidate, 104)
        self.assertEqual((first.done_cycle, candidate.done_cycle), (109, 110))

    def test_instruction_writing_both_banks_needs_both_slots_atomically(self):
        for busy_bank in ('vector', 'predicate'):
            with self.subTest(busy_bank=busy_bank):
                engine = self.engine()
                self.start(engine, self.uop(engine, 0, 'VEXP', banks=(busy_bank,)), 100)
                both = self.uop(engine, 1, 'VADD', banks=('vector', 'predicate'))
                self.assertEqual(engine.exu.next_issue_cycle(both, 0, not_before=109), 110)
                with self.assertRaises(RuntimeError):
                    engine.exu.start(both, 0, 'ALU', 109, queued=True)
                self.assertIsNone(both.start_cycle)
                other_bank = 'predicate' if busy_bank == 'vector' else 'vector'
                other = self.uop(engine, 2, 'VADD', banks=(other_bank,))
                self.assertTrue(engine.exu.can_issue(other, 0, 109, queued=True))
                self.start(engine, both, 110)
                for bank in ('vector', 'predicate'):
                    candidate = self.uop(engine, 3, 'VADD', banks=(bank,))
                    self.assertFalse(engine.exu.can_issue(candidate, 0, 110, queued=True))
                    self.assertEqual(engine.exu.next_completion_issue_cycle(candidate, 0, 110), 111)
                self.assertEqual(len(engine.exu.finish(117)), 2)
                self.assertEqual(engine.exu.exq_inflight, [0, 0])

    def test_instruction_without_register_outputs_uses_no_writeback_slot(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        candidate = self.uop(engine, 1, 'VADD', banks=())
        self.start(engine, candidate, 109)
        self.assertEqual({u.inst_id for u in engine.exu.finish(116)}, {0, 1})

    def test_rename_uses_destination_banks_and_ignores_predicate_inputs(self):
        vf = parse_vector_body(
            'mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);'
            'vcmp_gt(p,x,x,mask);vsel(y,x,x,p);vsts(y,out,0,NORM_B32,mask);')
        for theoretical in (False, True):
            with self.subTest(theoretical=theoretical):
                engine = engine_for(replace(vf, uarch={'theoretical_limit_mode': theoretical}))
                uops = {inst.op: engine.admit(inst) for inst in engine.program.instructions
                        if inst.type == 'inst'}
                for op in ('PSET_B32', 'VCMP_GT'):
                    self.assertEqual(uops[op].writeback_banks, frozenset({'predicate'}))
                for op in ('VLDS', 'VSEL'):
                    self.assertEqual(uops[op].writeback_banks, frozenset({'vector'}))
                self.assertFalse(uops['VSTS'].writeback_banks)

    def test_engine_completes_vector_and_predicate_results_together(self):
        vf = parse_vector_body(
            'mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);'
            'for(int i=0;i<32;++i){vexp(y,x,mask);vcmp_gt(p,x,x,mask);'
            'mask=pset_b32(PAT_ALL);}vsel(y,y,x,p);vsts(y,out,0,NORM_B32,mask);')
        engine = engine_for(vf)
        engine.run(None)
        done = {row['inst_id']: row['cy'] for row in engine.trace.cyc_done_log}
        issues = Counter()
        writebacks = Counter()
        completions = defaultdict(set)
        for row in engine.trace.cyc_start_log:
            if row['op_class'] != 'COMPUTE':
                continue
            port = row['exu_port']
            issues[port, row['cy']] += 1
            banks = {engine.registers.physical_banks[p].name for p in row['preg_dst']}
            for bank in banks:
                writebacks[port, done[row['inst_id']], bank] += 1
            completions[port, done[row['inst_id']]].update(banks)
        self.assertTrue(any(banks == {'vector', 'predicate'} for banks in completions.values()))
        self.assertTrue(all(count == 1 for count in writebacks.values()))
        self.assertTrue(all(count == 1 for count in issues.values()))

    def test_multiple_destinations_use_one_slot_per_bank(self):
        db = ParamDB(base_dir=ROOT)
        values = {
            'predicate_like_vector': {'storage': 'Register'},
            'another_vector': {'storage': 'Register'},
            'V_like_predicate': {'storage': 'PredicateRegister'},
            'scalar': {'storage': 'Scalar'},
        }
        engine = component_engine(db.base_uarch, db, values=values)
        for identity, (destinations, expected) in enumerate((
            (['predicate_like_vector', 'another_vector'], {'vector'}),
            (['predicate_like_vector', 'V_like_predicate'], {'vector', 'predicate'}),
            (['scalar'], set()),
        )):
            with self.subTest(destinations=destinations):
                inst = DynamicInstruction(
                    type='inst', inst_id=identity, op='VADD', form='fp32',
                    dst=destinations,
                    dst_value_instances=[{'definition_id': f'{identity}:{d}',
                                          'iteration_path': []} for d in destinations],
                    dst_value_instance_keep=[False] * len(destinations))
                uop = engine.registers.rename(inst, db.resolve_inst('VADD', 'fp32'), 100)
                self.assertEqual(uop.writeback_banks, frozenset(expected))

    def test_port_selection_uses_completion_slots_at_the_candidate_cycle(self):
        for greedy in (False, True):
            with self.subTest(greedy=greedy):
                engine = self.engine(enable_exq_greedy_balance=greedy)
                self.start(engine, self.uop(engine, 0, 'VEXP'), 100, port=0)
                self.start(engine, self.uop(engine, 1, 'VEXP'), 101, port=1)
                candidate = self.uop(engine, 9, 'VADD')
                self.assertEqual(engine.exu.pick_port(candidate, 109, [False, False]), 1)

    def test_exq_prediction_checks_reserved_completions_with_or_without_a_tail(self):
        for pending in (False, True):
            with self.subTest(pending=pending):
                engine = self.engine()
                self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
                if pending:
                    tail = self.uop(engine, 1, 'VADD')
                    engine.exq.enqueue(tail, 0, 'ALU', 107, 107)
                candidate = self.uop(engine, 9, 'VADD')
                self.assertEqual(engine.exq.predict_issue_cycle(0, candidate, 109), 110)
                self.assertEqual(engine.exq.predict_issue_cycle(0, candidate, 109), 110)

    def test_exq_waits_for_completion_slot(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        candidate = self.uop(engine, 1, 'VADD')
        engine.exq.enqueue(candidate, 0, 'ALU', 109, 109)
        self.assertIsNone(engine.exq.pop_ready(0, 109))
        self.assertEqual(engine.exq.pop_ready(0, 110), ('ALU', candidate))

    def test_exq_and_port_selection_use_the_destination_bank(self):
        for greedy in (False, True):
            for pending in (False, True):
                with self.subTest(greedy=greedy, pending=pending):
                    engine = self.engine(enable_exq_greedy_balance=greedy)
                    self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
                    predicate = self.uop(engine, 1, 'VCMP_GT', banks=('predicate',))
                    if pending:
                        engine.exq.enqueue(self.uop(engine, 2, 'VADD'), 0, 'ALU', 107, 107)
                    self.assertEqual(engine.exq.predict_issue_cycle(0, predicate, 110), 110)
                    self.assertEqual(engine.exu.pick_port(predicate, 110, [False, True]), 0)
                    if pending:
                        self.assertIsNotNone(engine.exq.pop_ready(0, 107))
                    engine.exq.enqueue(predicate, 0, 'ALU', 110, 110)
                    self.assertEqual(engine.exq.pop_ready(0, 110), ('ALU', predicate))

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
