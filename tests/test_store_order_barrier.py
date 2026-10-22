import copy
import unittest
from api.input_symbols import normalize_membar_type
from core.control_unit import ControlUnit
from core.membar_timing import TimedControlUnit
from tests.test_membar_timing import CONFIG


class StoreOrderingBarrierTests(unittest.TestCase):
    def test_alias_and_public_enum(self):
        self.assertEqual(normalize_membar_type('MEMBAR.VST_VST'),'VST_VST')

    def test_prior_store_completion_gates_only_future_stores(self):
        for unit in (ControlUnit(None),TimedControlUnit(None,'fp32',copy.deepcopy(CONFIG))):
            with self.subTest(unit=type(unit).__name__):
                unit.accept_membar({'barrier':'VST_VST','stream_seq':5})
                unit.update(lambda seq,cls:cls=='STORE',cycle=10)
                self.assertTrue(unit.blocks({'stream_seq':6,'op':'VSTS','form':'fp32'}))
                for op in ('VLDS','VADD'):
                    self.assertFalse(unit.blocks({'stream_seq':6,'op':op,'form':'fp32'}))
                self.assertFalse(unit.blocks({'stream_seq':4,'op':'VSTS','form':'fp32'}))
                unit.update(lambda *_:False,cycle=11)
                self.assertTrue(unit.empty())

    def test_undispatched_prior_store_cannot_release_fence(self):
        unit=TimedControlUnit(None,'fp32',copy.deepcopy(CONFIG))
        unit.accept_membar({'barrier':'VST_VST','stream_seq':5})
        unit.update(lambda *_:False,cycle=1,has_pending_dispatch=lambda _:True)
        self.assertFalse(unit.empty())
        unit.update(lambda *_:False,cycle=2,has_pending_dispatch=lambda _:False)
        self.assertTrue(unit.empty())

    def test_order_fence_does_not_borrow_other_direction_delays(self):
        unit=TimedControlUnit(None,'fp32',copy.deepcopy(CONFIG))
        unit.accept_membar({'barrier':'VST_VST','stream_seq':0})
        self.assertIsNone(unit.last_barrier)
        self.assertNotIn('VST_VST',unit.rules)
        self.assertEqual(unit.history,[])
