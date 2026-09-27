import unittest
import json
import tempfile
from pathlib import Path

from core.ooo import Uop
from core.ooo_mainline import OoOCoreMainline
from core.param_db import ParamDB
from core.physical_register_bank import PhysicalRegisterBank


ROOT = Path(__file__).resolve().parents[1]


class PhysicalRegisterBankTest(unittest.TestCase):
    def test_rejects_unknown_and_other_bank_ids_without_mutating_state(self):
        vector = PhysicalRegisterBank(68, name="vector", prefix="v")
        predicate = PhysicalRegisterBank(32, name="predicate", prefix="p")
        vector.allocate()
        predicate.allocate()
        for bank, invalid_ids in ((vector, ("p0", "v100", "")),
                                  (predicate, ("v0", "p32", "unknown"))):
            for physical_id in invalid_ids:
                with self.subTest(bank=bank.name, physical_id=physical_id):
                    before = tuple(bank.freelist)
                    for method in (bank.can_free, bank.try_free):
                        with self.assertRaisesRegex(ValueError, "does not belong"):
                            method(physical_id, 10)
                        self.assertEqual(tuple(bank.freelist), before)

    def test_allocation_release_and_exhaustion_preserve_capacity(self):
        bank = PhysicalRegisterBank(4, name="predicate", prefix="p")
        self.assertFalse(bank.try_free("p0", 0))
        for cycle in range(100):
            allocated = [bank.allocate() for _ in range(bank.capacity)]
            self.assertEqual(len(set(allocated)), bank.capacity)
            self.assertEqual(len(bank.freelist), 0)
            with self.assertRaisesRegex(RuntimeError, "exhausted"):
                bank.allocate()
            for index, slot in enumerate(reversed(allocated), 1):
                self.assertTrue(bank.try_free(slot, cycle))
                self.assertFalse(bank.try_free(slot, cycle))
                self.assertEqual(len(bank.freelist), index)
                self.assertLessEqual(len(bank.freelist), bank.capacity)
                self.assertEqual(len(set(bank.freelist)), len(bank.freelist))

    def test_separate_banks_can_use_identical_physical_numbers(self):
        vector = PhysicalRegisterBank(68, name="vector", prefix="p")
        predicate = PhysicalRegisterBank(32, name="predicate", prefix="p")
        for bank in (vector, predicate):
            slot = bank.allocate()
            bank.rat["same.definition"] = slot
            bank.generation[slot] = 1
        predicate.rat.clear()
        self.assertTrue(predicate.try_free("p0", 4))
        self.assertFalse(vector.try_free("p0", 4))
        self.assertEqual(len(vector.freelist), 67)
        self.assertEqual(len(predicate.freelist), 32)
        self.assertEqual(vector.rat["same.definition"], "p0")

    def test_free_requires_all_lifetime_guards_for_either_bank(self):
        for name in ("vector", "predicate"):
            with self.subTest(bank=name):
                bank = PhysicalRegisterBank(1, name=name, prefix="p")
                slot = bank.allocate()
                bank.rat["definition"] = slot
                bank.consumer_count[slot] = 1
                bank.pending.add(slot)
                bank.release_eligible_cycle[slot] = 14
                self.assertFalse(bank.try_free(slot, 14))
                bank.rat.clear()
                self.assertFalse(bank.try_free(slot, 14))
                bank.consumer_count[slot] = 0
                self.assertFalse(bank.try_free(slot, 14))
                bank.pending.clear()
                self.assertFalse(bank.try_free(slot, 13))
                self.assertTrue(bank.try_free(slot, 14))
                self.assertFalse(bank.try_free(slot, 14))

    def test_release_cleans_producer_but_keeps_generation(self):
        bank = PhysicalRegisterBank(1, name="predicate", prefix="p")
        slot = bank.allocate()
        bank.producer[slot] = object()
        bank.producer_uop[slot] = object()
        bank.producer_profile[slot] = object()
        bank.generation[slot] = 7
        bank.bypass_producer_done.add(slot)
        self.assertTrue(bank.try_free(slot, 20))
        self.assertNotIn(slot, bank.producer)
        self.assertNotIn(slot, bank.producer_uop)
        self.assertNotIn(slot, bank.producer_profile)
        self.assertNotIn(slot, bank.bypass_producer_done)
        self.assertEqual(bank.generation[slot], 7)

    def test_capacity_requires_positive_integer(self):
        for capacity in (True, 0, -1, 1.5, "32"):
            with self.subTest(capacity=capacity):
                with self.assertRaises(ValueError):
                    PhysicalRegisterBank(capacity, name="predicate", prefix="p")


class VectorReleaseContractTest(unittest.TestCase):
    def setUp(self):
        db = ParamDB(base_dir=str(ROOT))
        uarch = dict(db.get_uarch())
        uarch["consumer_release_start_offset"] = 4
        self.core = OoOCoreMainline(uarch, db)
        self.slot = self.core.vector_bank.allocate()
        self.core.preg_generation[self.slot] = 1

    def consumer(self, inst_id, start, copies=1):
        uop = Uop(
            inst_id=inst_id, op="VADD", form="fp32", src=["value"] * copies,
            dst=[], preg_src=[self.slot] * copies, preg_dst=[], preg_old=[],
            start_cycle=start,
        )
        uop.preg_src_gen = [1] * copies
        self.core.preg_lifecycle.schedule_src_release_from_start(uop)
        return uop

    def release_at(self, cycle):
        self.core.cycle = cycle
        self.core.preg_lifecycle.run_src_release_events(cycle)

    def test_last_use_frees_without_a_later_overwrite_at_start_plus_four(self):
        self.core.preg_consumer_count[self.slot] = 1
        self.consumer(1, 10)
        self.release_at(13)
        self.assertNotIn(self.slot, self.core.freelist)
        self.release_at(14)
        self.assertIn(self.slot, self.core.freelist)

    def test_all_consumers_must_release_even_if_last_in_source_starts_first(self):
        self.core.preg_consumer_count[self.slot] = 2
        self.consumer(2, 10)
        self.consumer(1, 20)
        self.release_at(14)
        self.assertNotIn(self.slot, self.core.freelist)
        self.release_at(24)
        self.assertIn(self.slot, self.core.freelist)

    def test_repeated_source_and_repeated_scheduling_do_not_double_free(self):
        self.core.preg_consumer_count[self.slot] = 2
        uop = self.consumer(1, 10, copies=2)
        self.core.preg_lifecycle.schedule_src_release_from_start(uop)
        self.assertEqual(len(self.core.src_release_events[14]), 2)
        self.release_at(14)
        self.release_at(14)
        self.assertEqual(list(self.core.freelist).count(self.slot), 1)

    def test_pending_producer_prevents_early_release(self):
        self.core.preg_consumer_count[self.slot] = 1
        self.core.preg_pending.add(self.slot)
        self.consumer(1, 10)
        self.release_at(14)
        self.assertNotIn(self.slot, self.core.freelist)
        self.core.preg_pending.remove(self.slot)
        self.assertTrue(self.core.preg_lifecycle.try_free_preg(self.slot))

    def test_stale_release_cannot_free_reused_slot(self):
        self.core.preg_consumer_count[self.slot] = 1
        self.consumer(1, 10)
        self.core.preg_generation[self.slot] = 2
        with self.assertRaisesRegex(AssertionError, "stale start-release"):
            self.release_at(14)
        self.assertNotIn(self.slot, self.core.freelist)
        self.assertEqual(self.core.preg_consumer_count[self.slot], 1)


class ReleaseConfigurationTest(unittest.TestCase):
    def test_param_db_rejects_removed_field_even_when_empty_or_null(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "uarch.json"
            for value in ({}, {"VADD": 1}, None, 0):
                with self.subTest(value=value):
                    path.write_text(json.dumps({
                        "consumer_release_start_offset": 4,
                        "consumer_release_start_offset_by_op": value,
                    }))
                    with self.assertRaisesRegex(
                        ValueError,
                        "consumer_release_start_offset_by_op was removed; use global consumer_release_start_offset",
                    ):
                        ParamDB(base_dir=str(ROOT), uarch_path=str(path))
            path.write_text(json.dumps({"consumer_release_start_offset": 4}))
            db = ParamDB(base_dir=str(ROOT), uarch_path=str(path))
            self.assertEqual(db.get_uarch()["consumer_release_start_offset"], 4)


if __name__ == "__main__":
    unittest.main()
