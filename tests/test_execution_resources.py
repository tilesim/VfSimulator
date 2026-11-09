"""Exercise resource sharing, sparse timing exceptions and parameter provenance."""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from core.execution.instruction import Uop
from core.params.database import ParamDB
from core.params.loader import load_parameters
from core.params.resources import validate_resources
from tests.core_support import component_engine

ROOT = Path(__file__).resolve().parents[1]


class ExecutionResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = load_parameters(ROOT)

    def engine(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        return component_engine({**db.get_uarch(), 'exq_issue_inflight_cap_per_port': 0}, db)

    def uop(self, engine, identity, op, form='fp32'):
        profile = engine.db.resolve_inst(op, form)
        return Uop(identity, op, form, [], [], [], [], profile=profile,
                   writeback_banks=profile.writeback_banks)

    def start(self, engine, uop, cycle, port=0, queued=True):
        self.assertTrue(engine.exu.can_issue(uop, port, cycle, queued=queued))
        engine.exu.start(uop, port, uop.profile.fu_type, cycle, queued=queued)

    def test_shared_resource_survives_an_independent_instruction(self):
        for queued in (False, True):
            with self.subTest(queued=queued):
                engine = self.engine()
                self.start(engine, self.uop(engine, 0, 'VCI', 'int32'), 100, queued=queued)
                add = self.uop(engine, 2, 'VADD')
                self.assertFalse(engine.exu.can_issue(add, 0, 101, queued=queued))
                self.start(engine, self.uop(engine, 1, 'VEXP'), 101, queued=queued)
                self.assertEqual(engine.exu.next_issue_cycle(add, 0), 102)
                self.start(engine, add, 102, queued=queued)

    def test_idle_resource_and_other_port_can_accept(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VCI', 'int32'), 100)
        multiply = self.uop(engine, 1, 'VMUL')
        add = self.uop(engine, 2, 'VADD')
        self.assertTrue(engine.exu.can_issue(multiply, 0, 101, queued=True))
        self.assertTrue(engine.exu.can_issue(add, 1, 100, queued=True))
        self.assertFalse(engine.exu.can_issue(multiply, 0, 100, queued=True))

    def test_exq_selects_an_idle_resource_behind_a_blocked_group(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VCI', 'int32'), 100)
        add, multiply = self.uop(engine, 1, 'VADD'), self.uop(engine, 2, 'VMUL')
        engine.exq.enqueue(add, 0, 101, 102)
        engine.exq.enqueue(multiply, 0, 101, 101)
        self.assertEqual(engine.exq.pop_ready(0, 101), ('ALU', multiply))
        self.assertEqual(engine.exq.occupancy(0), 1)
        self.assertEqual(engine.exq.pop_ready(0, 102), ('ALU', add))

    def test_exq_prediction_uses_pending_other_opcode_resource_occupancy(self):
        engine = self.engine()
        engine.exq.enqueue(self.uop(engine, 0, 'VCI', 'int32'), 0, 100, 100)
        add = self.uop(engine, 1, 'VADD')
        multiply = self.uop(engine, 2, 'VMUL')
        self.assertEqual(engine.exq.predict_issue_cycle(0, add, 101), 102)
        self.assertEqual(engine.exq.predict_issue_cycle(0, multiply, 101), 101)

    def test_exception_uses_original_producer_after_interleaving(self):
        engine = self.engine()
        self.start(engine, self.uop(engine, 0, 'VEXP'), 100)
        self.start(engine, self.uop(engine, 1, 'VADD'), 101)
        divide = self.uop(engine, 2, 'VDIV')
        self.assertEqual(engine.exu.next_resource_issue_cycle(divide.profile, 0), 104)
        self.assertEqual(engine.exu.next_issue_cycle(divide, 0), 106)
        self.assertFalse(engine.exu.can_issue(divide, 0, 105, queued=True))
        self.assertTrue(engine.exu.can_issue(divide, 0, 106, queued=True))
        self.assertTrue(engine.exu.can_issue(divide, 1, 101, queued=True))

    def test_exception_deadline_survives_producer_completion(self):
        engine = self.engine()
        producer = self.uop(engine, 0, 'VEXP')
        original = engine.db.issue_overrides_for_profile
        with patch.object(engine.db, 'issue_overrides_for_profile', side_effect=lambda profile:
                          {'VDIV.fp32': 30} if profile.op == 'VEXP' else original(profile)):
            self.start(engine, producer, 100)
        engine.exu.finish(116)
        self.start(engine, self.uop(engine, 1, 'VADD'), 117)
        self.assertEqual(engine.exu.next_issue_cycle(self.uop(engine, 2, 'VDIV'), 0), 130)

    def test_exq_prediction_includes_pending_exceptions(self):
        engine = self.engine()
        engine.exq.enqueue(self.uop(engine, 0, 'VEXP'), 0, 100, 100)
        engine.exq.enqueue(self.uop(engine, 1, 'VADD'), 0, 101, 101)
        self.assertEqual(engine.exq.predict_issue_cycle(0, self.uop(engine, 2, 'VDIV'), 102), 106)

    def test_group_metadata_and_estimated_pairs(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        counts = Counter(params['execution_resource'] for node in self.parameters.isa['instructions'].values()
                         for params in node.get('forms', {}).values() if params.get('execution_resource'))
        self.assertEqual(counts, dict(ADD_DUAL=50, ADD_CONV=57, MUL=27, MOV=11, LNEXP=8))
        self.assertEqual(db.get_ii('VCI', 'VADD', prev_form='int32', cur_form='fp32'), 2)
        self.assertEqual(db.get_ii('VCI', 'VMUL', prev_form='int32', cur_form='fp32'), 1)
        self.assertEqual(db.get_ii('VMUL', 'VADD', 'fp32'), 2)  # completion bank collision
        self.assertEqual(db.get_forwarding_cycles('VADD', 'VMUL', 'fp32'), 3)
        self.assertEqual(db.get_forwarding_cycles('VADD', 'VEXP', 'fp32'), 4)
        self.assertEqual(db.get_forwarding_cycles('VEXP', 'VADD', 'fp32'), 13)
        self.assertEqual(db.get_forwarding_cycles('VMOV', 'VADD', 'fp32'), 2)
        self.assertEqual(db.get_forwarding_cycles('VMULL', 'VMULL', 'int32'), 5)
        self.assertEqual(db.get_warnings(), [])

    def test_ungrouped_forms_keep_missing_parameter_diagnostics(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        self.assertEqual(db.resolve_inst('VSELRV2', 'fp32').execution_resource, '')
        db.get_ii('VSELRV2', 'VADD', 'fp32')
        db.get_forwarding_cycles('VSELRV2', 'VADD', 'fp32')
        kinds = {row['kind'] for row in db.get_warnings()}
        self.assertIn('missing_ii_pair', kinds)
        self.assertIn('missing_forwarding_pair', kinds)

    def test_all_migrated_compute_store_parameters_keep_their_values(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        record = json.loads((ROOT / 'docs/data/compute_store_forwarding_20261109.json').read_text())
        self.assertEqual(len(record['pairs']), 104)
        for row in record['pairs']:
            p, pf = row['producer'].split('.', 1)
            c, cf = row['consumer'].split('.', 1)
            with self.subTest(producer=row['producer'], consumer=row['consumer']):
                self.assertEqual(db.get_forwarding_cycles(p, c, producer_form=pf, consumer_form=cf), row['value'])
                self.assertNotIn(row['consumer'], db._fwd_table.get(row['producer'], {}))

    def test_compute_store_rule_covers_ungrouped_predicate_and_memory_variants(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        for p, pf, c, cf, expected in (
            ('VADD', 'fp32', 'VSTS', 'fp32', 5),
            ('VEXP', 'fp16', 'VSTAS', 'fp16', 19),
            ('VCADD', 'fp32', 'VSSTB', 'fp32', 20),
            ('PLT', 'b32', 'PSTS', 'b8', 4),
            ('VCVT_BF16_TO_F32', 'bf16_to_f32', 'VSTUS', 'fp32', 5),
            ('VCI', 'int32', 'VSCATTER', 'int32', 5),
        ):
            with self.subTest(producer=p, consumer=c):
                self.assertEqual(db.get_forwarding_cycles(p, c, producer_form=pf, consumer_form=cf), expected)
        self.assertEqual(db.get_warnings(), [])

    def test_compute_store_rule_uses_current_latency_and_precedes_explicit_values(self):
        isa = deepcopy(self.parameters.isa)
        isa['instructions']['VADD']['forms']['fp32']['latency'] = 14
        overrides = deepcopy(self.parameters.timing_overrides)
        overrides['forwarding']['VADD.fp32']['VSTS.fp32'] = 97
        parameters = replace(self.parameters, isa=isa, timing_overrides=overrides,
            forwarding={'forwarding': {'VADD.fp32': {'VSTS.fp32': 99, '*': 98}}})
        db = ParamDB(base_dir=ROOT, parameters=parameters)
        for _ in range(3):
            self.assertEqual(db.get_forwarding_cycles('VADD', 'VSTS', 'fp32'), 12)
        self.assertEqual(db.get_warnings(), [])

    def test_compute_store_forwarding_clamps_short_latency_and_retains_other_paths(self):
        isa = deepcopy(self.parameters.isa)
        isa['instructions']['VADD']['forms']['fp32']['latency'] = 1
        db = ParamDB(base_dir=ROOT, parameters=replace(self.parameters, isa=isa))
        self.assertEqual(db.get_forwarding_cycles('VADD', 'VSTS', 'fp32'), 0)
        self.assertEqual(db.get_forwarding_cycles('VLDS', 'VSTS', 'fp32'), 8)
        self.assertEqual(db.get_forwarding_cycles('VMULL', 'VMULL', 'int32'), 5)

    def test_all_migrated_values_and_additional_measurements(self):
        db = ParamDB(base_dir=ROOT, parameters=self.parameters)
        record = json.loads((ROOT / 'docs/data/sub_exu_timing_migration_20261109.json').read_text())
        for row in record['original_pair_values'] + record['additional_measured_ii']:
            p, pf = row['previous'].split('.', 1)
            c, cf = row['current'].split('.', 1)
            with self.subTest(previous=row['previous'], current=row['current'], kind=row.get('kind', 'ii')):
                value = (db.get_forwarding_cycles(p, c, producer_form=pf, consumer_form=cf)
                         if row.get('kind') == 'forwarding' else db.get_ii(p, c, prev_form=pf, cur_form=cf))
                self.assertEqual(value, row['value'])

    def test_resource_configuration_rejects_invalid_values(self):
        mutations = (
            lambda r, o, i: r['groups'].append('ADD_DUAL'),
            lambda r, o, i: r['forwarding'].update(minimum=True),
            lambda r, o, i: r['forwarding']['pair_offsets'].update(UNKNOWN={'MUL': 3}),
            lambda r, o, i: i['instructions']['VADD']['forms']['fp32'].update(execution_resource='UNKNOWN'),
            lambda r, o, i: i['instructions']['VADD']['forms']['fp32'].update(execution_resource=True),
            lambda r, o, i: i['defaults'].update(execution_resource='UNKNOWN'),
            lambda r, o, i: i['instructions']['VLDS']['forms']['fp32'].update(execution_resource='MUL'),
            lambda r, o, i: o['ii'].update({'VADD.fp32': {'VADD.fp32': 1}}),
            lambda r, o, i: o['ii'].update({'VADD.fp32': {'VMUL.fp32': True}}),
            lambda r, o, i: o['forwarding'].update({'VADD.fp32': {'UNKNOWN.fp32': 2}}),
            lambda r, o, i: o['forwarding'].update({'UNKNOWN.fp32': {}}),
        )
        for index, mutate in enumerate(mutations):
            rules, overrides, isa = map(deepcopy, (self.parameters.resource_timing,
                                                   self.parameters.timing_overrides, self.parameters.isa))
            mutate(rules, overrides, isa)
            with self.subTest(case=index), self.assertRaises(ValueError):
                validate_resources(rules, overrides, isa)


if __name__ == '__main__':
    unittest.main()
