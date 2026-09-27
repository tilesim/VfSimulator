import copy
import json
from pathlib import Path
import unittest

from api.frontend import CanonicalJsonVfInfoAdapter, VfInfoValidationError
from core.param_db import ParamDB
from tests import test_pstu as shared


FIXTURE = Path(__file__).parent / "fixtures/canonical_vf_info/v2_external_predicate_forms.json"


class ExternalPredicateFormsTest(unittest.TestCase):
    def test_json_contract_and_uint_equivalence(self):
        runner = shared.PredicateStoreTest()
        for move_bits in (16, 32):
            for store_bits in (16, 32):
                payload = json.loads(FIXTURE.read_text())
                payload["values"]["bits"]["dtype"] = f"uint{move_bits}"
                for key in ("stored", "flushed"):
                    payload["values"][key]["dtype"] = f"uint{store_bits}"
                payload["context"][0]["form"] = f"b{move_bits}"
                for inst in payload["context"][1:]:
                    inst["form"] = f"b{store_bits}"
                memory = payload["context"][1]["outputs"][0]["memory_access"]
                memory["span"] = 8 if store_bits == 16 else 2
                memory["post_update_delta_bytes"]["constant"] = 16 if store_bits == 16 else 8
                with self.subTest(move_bits=move_bits, store_bits=store_bits):
                    external = CanonicalJsonVfInfoAdapter.from_payload(payload)
                    rows = runner.run_case(external)
                    internal_payload = copy.deepcopy(payload)
                    for inst in internal_payload["context"]:
                        inst["form"] = inst["form"].replace("b", "uint")
                    internal = CanonicalJsonVfInfoAdapter.from_payload(internal_payload)
                    reference = runner.run_case(internal)
                    key = lambda rs: [(r["op"], r["cy"]) for r in rs]
                    self.assertEqual(key(rows), key(reference))
                    self.assertEqual(rows[2]["cy"], rows[1]["cy"] + 1)
                    self.assertEqual(rows[1]["preg_src"], rows[0]["preg_dst"])

    def test_external_forms_preserve_address_contract(self):
        payload = json.loads(FIXTURE.read_text())
        payload["context"][1]["outputs"][0]["memory_access"]["post_update_delta_bytes"]["constant"] = 0
        with self.assertRaises(VfInfoValidationError):
            CanonicalJsonVfInfoAdapter.from_payload(payload)

    def test_explicit_timing_and_mixed_flush_forms(self):
        db = ParamDB(base_dir=str(shared.ROOT))
        for bits in (16, 32):
            for form in (f"b{bits}", f"uint{bits}"):
                self.assertEqual(db.resolve_inst("PSTU", form, "fp32").latency, 8)
                self.assertEqual(db.resolve_inst("MOVVP", form, "fp32").latency, 16)
                for flush_form in (f"b{bits}", f"uint{bits}", f"fp{bits}"):
                    self.assertEqual(db.get_forwarding_cycles(
                        "PSTU", "VSTAS", producer_form=form, consumer_form=flush_form), 1)
