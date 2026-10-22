import unittest
from pathlib import Path

from core.execution.instruction import Uop
from core.params.database import ParamDB
from tests.core_support import component_engine

ROOT = Path(__file__).resolve().parents[1]


class OoODispatchTest(unittest.TestCase):

    def _uop(self, inst_id, op, form='fp32', state='ready', *, db):
        return Uop(inst_id=inst_id, op=op, form=form, src=[], dst=[f'v{inst_id}'], preg_src=[], preg_dst=[f'p{inst_id}'], state=state, profile=db.resolve_inst(op, form, 'fp32'))


    def _engine(self):
        db = ParamDB(base_dir=str(ROOT))
        uarch = dict(db.get_uarch())
        uarch.update({'enable_isu_queue_model': True, 'shq_exq_dispatch_policy': 'fu_round_robin_exu0_reserve', 'exu0_reserve_lookahead': 8, 'exu0_reserve_min_count': 2, 'enforce_same_cycle_src_hazard': False, 'exq_depth': 2})
        return component_engine(uarch, db, fallback_dtype='fp32')




    def test_fu_round_robin_keeps_exu0_only_on_exq0(self):
        core = self._engine()
        core.shq.SHQ.append(self._uop(0, 'VPACK', form='b32', db=core.db))
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 1)
        self.assertEqual(len(core.exq.exq_wait[0]['ALU']), 1)
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].op, 'VPACK')
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].exu_port, 0)
        self.assertEqual(len(core.exq.exq_wait[1]['ALU']), 0)


    def test_fu_round_robin_fifo_allows_alu_to_pass_blocked_exu0_only_alu(self):
        core = self._engine()
        core.exq.exq_wait[0]['ALU'].append(self._uop(100, 'VADDS', db=core.db))
        core.exq.exq_wait[0]['SFU'].append(self._uop(101, 'VEXP', db=core.db))
        core.shq.SHQ.extend([self._uop(0, 'VPACK', form='b32', db=core.db), self._uop(1, 'VADDS', db=core.db)])
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 1)
        self.assertEqual([u.op for u in core.shq.SHQ], ['VPACK'])
        self.assertEqual(len(core.exq.exq_wait[1]['ALU']), 1)
        self.assertEqual(core.exq.exq_wait[1]['ALU'][0].op, 'VADDS')
        self.assertEqual(core.exq.exq_wait[1]['ALU'][0].exu_port, 1)


    def test_fu_round_robin_fifo_allows_sfu_to_pass_blocked_alu_group(self):
        core = self._engine()
        core.ooo.enforce_same_cycle_src_hazard = True
        blocked_alu = self._uop(0, 'VADDS', db=core.db)
        blocked_alu.preg_src = ['p_shared']
        second_alu = self._uop(1, 'VADDS', db=core.db)
        sfu = self._uop(2, 'VEXP', db=core.db)
        core.shq.SHQ.extend([blocked_alu, second_alu, sfu])
        issued = core.ooo.dispatch(0, {'p_shared'})
        self.assertEqual(issued, 1)
        self.assertEqual([u.op for u in core.shq.SHQ], ['VADDS', 'VADDS'])
        self.assertEqual(len(core.exq.exq_wait[0]['SFU']), 1)
        self.assertEqual(core.exq.exq_wait[0]['SFU'][0].op, 'VEXP')


    def test_exu0_reserve_lookahead_min_count_sends_flexible_alu_to_exq1(self):
        core = self._engine()
        core.shq.SHQ.extend([self._uop(0, 'VADDS', db=core.db), self._uop(1, 'VPACK', form='b32', db=core.db), self._uop(2, 'VPACK', form='b32', db=core.db)])
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 2)
        self.assertEqual(len(core.exq.exq_wait[0]['ALU']), 1)
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].op, 'VPACK')
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].exu_port, 0)
        self.assertEqual(len(core.exq.exq_wait[1]['ALU']), 1)
        self.assertEqual(core.exq.exq_wait[1]['ALU'][0].op, 'VADDS')
        self.assertEqual(core.exq.exq_wait[1]['ALU'][0].exu_port, 1)


    def test_exu0_reserve_ignores_single_exu0_only_in_window(self):
        core = self._engine()
        core.shq.SHQ.extend([self._uop(0, 'VADDS', db=core.db), self._uop(1, 'VPACK', form='b32', db=core.db)])
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 1)
        self.assertEqual([u.op for u in core.shq.SHQ], ['VPACK'])
        self.assertEqual(len(core.exq.exq_wait[0]['ALU']), 1)
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].op, 'VADDS')
        self.assertEqual(core.exq.exq_wait[0]['ALU'][0].exu_port, 0)
        self.assertEqual(len(core.exq.exq_wait[1]['ALU']), 0)


    def test_default_exu0_reserve_min_count_reserves_for_single_exu0_only(self):
        db = ParamDB(base_dir=str(ROOT))
        uarch = dict(db.get_uarch())
        self.assertEqual(uarch['exu0_reserve_min_count'], 1)
        core = component_engine(uarch, db, fallback_dtype='fp32')
        core.shq.SHQ.extend([self._uop(0, 'VADDS', db=core.db), self._uop(1, 'VPACK', form='b32', db=core.db)])
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 2)
        self.assertEqual([u.op for u in core.exq.exq_wait[0]['ALU']], ['VPACK'])
        self.assertEqual([u.op for u in core.exq.exq_wait[1]['ALU']], ['VADDS'])


    def test_exu0_reserve_balances_queue_gap_instead_of_forcing_exq1(self):
        db = ParamDB(base_dir=str(ROOT))
        uarch = dict(db.get_uarch())
        core = component_engine(uarch, db, fallback_dtype='fp32')
        core.exq.exq_wait[1]['ALU'].append(self._uop(100, 'VADDS', db=core.db))
        core.shq.SHQ.extend([self._uop(0, 'VADDS', db=core.db), self._uop(1, 'VPACK', form='b32', db=core.db)])
        issued = core.ooo.dispatch(0, set())
        self.assertEqual(issued, 1)
        self.assertEqual([u.op for u in core.exq.exq_wait[0]['ALU']], ['VADDS'])
        self.assertEqual([u.op for u in core.exq.exq_wait[1]['ALU']], ['VADDS'])
        self.assertEqual([u.op for u in core.shq.SHQ], ['VPACK'])



if __name__ == '__main__':
    unittest.main()
