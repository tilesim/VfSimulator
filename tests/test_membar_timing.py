import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api.frontend.builder import VfInfoBuilder
from api.simulator_costmodel import CoreVfCostModel
from core.membar_timing import TimedControlUnit


ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT/'configs/uarch.json').read_text())['membar_timing']


class MembarTimingTests(unittest.TestCase):
    soc = 'dav-3510'
    config = CONFIG

    def setUp(self):
        self.unit = TimedControlUnit(None, 'fp32', copy.deepcopy(self.config))

    def barrier(self, seq, kind='VLD_VST'):
        self.unit.accept_membar({'stream_seq': seq, 'barrier': kind, 'pc': 7})
        return self.unit.last_barrier

    def inst(self, seq, op):
        return {'stream_seq': seq, 'op': op, 'form': 'fp32'}

    def tick(self, cy, pending=False):
        self.unit.update(lambda seq, cls: pending, cycle=cy)

    def test_vld_vst_retire_and_store_gate_are_distinct(self):
        b = self.barrier(0)
        for cy in range(24):
            self.tick(cy)
            self.assertEqual(self.unit.blocks(self.inst(1, 'VSTS')), cy < 23)
            self.assertFalse(self.unit.blocks(self.inst(1, 'VADD')))
        self.assertEqual((b.issue_cycle, b.release_cycle, b.retire_cycle), (1, 19, 21))
        self.assertEqual([r['event'] for r in self.unit.history], ['issue','sync_release','retire'])

    def test_vst_vld_load_can_start_before_retirement(self):
        b = self.barrier(0, 'VST_VLD')
        for cy in range(10): self.tick(cy)
        self.assertEqual((b.issue_cycle,b.release_cycle,b.retire_cycle), (1,8,10))
        self.assertFalse(self.unit.blocks(self.inst(1,'VLDS')))
        self.assertFalse(b.retired)
        self.assertFalse(self.unit.empty())
        self.tick(10)
        self.assertTrue(self.unit.empty())

    def test_repeated_empty_barriers_do_not_collapse(self):
        barriers = [self.barrier(i) for i in range(8)]
        for cy in range(180): self.tick(cy)
        self.assertEqual([b.issue_cycle for b in barriers], [1+21*i for i in range(8)])
        self.assertEqual([b.retire_cycle for b in barriers], [21+21*i for i in range(8)])

    def test_repeated_vst_vld_spacing(self):
        barriers = [self.barrier(i, 'VST_VLD') for i in range(8)]
        for cy in range(100): self.tick(cy)
        self.assertEqual([b.issue_cycle for b in barriers], [1+11*i for i in range(8)])

    def test_segment_waits_for_all_stores_start_not_done(self):
        self.barrier(0)
        for seq in (1,2): self.unit.observe_instruction(self.inst(seq,'VSTS'))
        b = self.barrier(3)
        # Complete later stream_seq first: dynamic age alone is insufficient.
        for cy in range(31):
            if cy == 23: self.unit.notify_lsu_start(2,cy)
            if cy == 26: self.unit.notify_lsu_start(1,cy)
            self.tick(cy)
            if cy < 30: self.assertIsNone(b.issue_cycle)
        self.assertEqual(b.issue_cycle,30)

    def test_wait_direction_completion_delays_release_and_retire(self):
        b = self.barrier(0)
        for cy in range(51): self.tick(cy, pending=cy < 50)
        self.assertEqual((b.issue_cycle,b.release_cycle,b.retire_cycle),(1,50,52))
        self.assertTrue(self.unit.blocks(self.inst(1,'VSTS')))
        for cy in range(51,55): self.tick(cy)
        self.assertFalse(self.unit.blocks(self.inst(1,'VSTS')))

    def test_mixed_directions_do_not_wait_for_future_group(self):
        self.barrier(0)
        self.unit.observe_instruction(self.inst(1,'VSTS'))
        b = self.barrier(2,'VST_VLD')
        self.unit.observe_instruction(self.inst(3,'VLDS'))
        for cy in range(28):
            if cy == 23: self.unit.notify_lsu_start(1,cy)
            self.tick(cy)
        self.assertEqual(b.issue_cycle,27)
        self.assertIn(3,self.unit.pending_starts)

    def test_undispatched_predecessor_prevents_early_barrier_issue(self):
        b = self.barrier(1)
        for cy in range(8):
            self.unit.update(lambda *_: False, cycle=cy, has_pending_dispatch=lambda _: cy<7)
        self.assertEqual(b.issue_cycle,7)

    def test_segment_storage_is_bounded_without_barriers(self):
        for seq in range(1000):
            self.unit.observe_instruction(self.inst(seq,'VLDS'))
            self.unit.notify_lsu_start(seq,seq)
        self.assertEqual(len(self.unit.segment),1)
        self.assertFalse(self.unit.pending_starts)

    def test_invalid_timing_config_is_rejected(self):
        config = copy.deepcopy(self.config)
        config['directions']['VLD_VST']['retire_latency'] = -1
        with self.assertRaises(ValueError): TimedControlUnit(None,'fp32',config)

    def test_trailing_barrier_counts_toward_vf_end(self):
        builder = VfInfoBuilder()
        builder.add_membar('tail',barrier='VST_VLD')
        with tempfile.TemporaryDirectory() as tmp:
            result = CoreVfCostModel(base_dir=ROOT,out_dir=tmp).run_vf_info(builder.build())
            history = json.loads((Path(tmp)/'membar_history.json').read_text())
        retire = next(e['cy'] for e in history if e['event']=='retire')
        self.assertGreaterEqual(result['cycles_executed'],retire)
        self.assertGreater(result['vf_end_cycle'],retire)


class MembarConfigurationTests(unittest.TestCase):
    def test_present_invalid_timing_is_rejected_at_public_entry(self):
        for value in ({}, None, [], False, 0, '', {'admission_delay': 1}):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                uarch = json.loads((ROOT/'configs/uarch.json').read_text())
                uarch['membar_timing'] = value
                path = Path(tmp)/'uarch.json'
                path.write_text(json.dumps(uarch))
                with patch.dict('os.environ', {'UARCH_JSON_PATH': str(path)}):
                    with self.assertRaisesRegex(ValueError, 'membar_timing'):
                        CoreVfCostModel(base_dir=ROOT, out_dir=tmp).run_vf_info(
                            VfInfoBuilder().build())

    def test_missing_timing_keeps_compatibility_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            uarch = json.loads((ROOT/'configs/uarch.json').read_text())
            del uarch['membar_timing']
            path = Path(tmp)/'uarch.json'
            path.write_text(json.dumps(uarch))
            builder = VfInfoBuilder()
            builder.add_membar('tail', barrier='VST_VLD')
            with patch.dict('os.environ', {'UARCH_JSON_PATH': str(path)}):
                CoreVfCostModel(base_dir=ROOT, out_dir=tmp).run_vf_info(builder.build())
            self.assertFalse((Path(tmp)/'membar_history.json').exists())


if __name__ == '__main__':
    unittest.main()
