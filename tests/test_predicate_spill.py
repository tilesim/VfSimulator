"""Predicate spill contracts and scheduling shared by Python and Native."""
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.input_api import InputAPI
from api.common.validator import validate_canonical_vf_info
from api.json.adapter import CanonicalJsonVfInfoAdapter
from api.json.serialization import canonical_vf_info_to_dict
from core.params.database import ParamDB
from core.simulator import CoreVfCostModel

ROOT = Path(__file__).resolve().parents[1]
RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")


def parse(body):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "spill.cce"
        path.write_text(
            "void vf(__ubuf__ uint32_t *out) { __VEC_SCOPE__ {"
            "vector_bool p=pset_b32(PAT_ALL); vector_bool q;" + body + "}}"
        )
        return InputAPI.load_cce(path)


class PredicateSpillTests(unittest.TestCase):
    def compare(self, vf):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            result = CoreVfCostModel(base_dir=ROOT, out_dir=folder / "python").run_vf_info(vf)
            self.assertLess(result["vf_end_cycle"], 10000)
            rows = [json.loads(line) for line in (folder / "python/start_by_cycle.json").read_text().splitlines()]
            if RUNNER:
                source = folder / "input.json"
                source.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc = subprocess.run(
                    [RUNNER, "--trace", str(source), "--out-dir", str(folder / "native")],
                    capture_output=True, text=True, timeout=60,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr)
                fields = dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)
                self.assertEqual(int(fields["vfEndCycle"]), result["vf_end_cycle"])
                self.assertEqual(int(fields["cyclesExecuted"]), result["cycles_executed"])
                for name in ("start_by_cycle.json", "done_by_cycle.json"):
                    def events(backend):
                        return sorted((row["cy"], row["op"], row["inst_id"])
                                      for row in map(json.loads, (folder / backend / name).read_text().splitlines()))
                    self.assertEqual(events("python"), events("native"), name)
            return sorted(rows, key=lambda row: row["stream_seq"])

    def test_byte_offsets_and_json_roundtrip(self):
        vf = parse("psts(p,out,-1376,NORM);mem_bar(VST_VLD);plds(q,out,-1376,NORM);psts(q,out,-1344,NORM);")
        for instruction, offset in zip((vf.context[1], vf.context[3], vf.context[4]), (-1376, -1376, -1344)):
            memory = next(operand.memory_access for operand in (*instruction.inputs, *instruction.outputs) if operand.memory_access)
            self.assertEqual(memory.offset.constant, offset)
            self.assertEqual(memory.address_unit_bytes, 1)
            self.assertEqual(memory.span, 32)
        payload = canonical_vf_info_to_dict(vf)
        accepted = CanonicalJsonVfInfoAdapter.from_payload(payload)
        self.assertEqual(canonical_vf_info_to_dict(accepted), payload)
        rows = self.compare(accepted)
        load = next(row for row in rows if row["op"] == "PLDS")
        self.assertTrue(load["preg_dst"][0].startswith("pred"))
        self.assertEqual(len(load["preg_dst"]), 1)
        store = rows[-1]
        self.assertEqual(store["preg_src"], load["preg_dst"])
        self.assertGreaterEqual(store["cy"], load["cy"] + 8)

    def test_pointer_alias_offsets_count_elements(self):
        vf = parse("__ubuf__ uint32_t *slot=out+8;psts(p,slot,32,NORM);plds(q,slot,-32,NORM);")
        self.assertEqual(vf.context[1].outputs[0].memory_access.offset.constant, 64)
        self.assertEqual(vf.context[2].inputs[0].memory_access.offset.constant, 0)
        self.compare(vf)

    def test_required_byte_address_contract(self):
        vf = parse("plds(q,out,0,NORM);")
        load = vf.context[1]
        operand = load.inputs[0]
        for unit, code in ((None, "catalog_memory_address_unit_mismatch"),
                           (4, "catalog_memory_address_unit_mismatch"),
                           (3, "invalid_address_unit"), (True, "invalid_address_unit")):
            with self.subTest(unit=unit):
                memory = replace(operand.memory_access, address_unit_bytes=unit)
                bad = replace(vf, context=(vf.context[0], replace(load, inputs=(replace(operand, memory_access=memory),))))
                self.assertIn(code, [diagnostic.code for diagnostic in validate_canonical_vf_info(bad).errors])
                if RUNNER:
                    with tempfile.TemporaryDirectory() as directory:
                        source = Path(directory) / "bad.json"
                        source.write_text(json.dumps(canonical_vf_info_to_dict(bad)))
                        proc = subprocess.run([RUNNER, "--trace", str(source)], capture_output=True, text=True)
                        self.assertNotEqual(proc.returncode, 0)

    def test_loop_spills_recycle_predicate_registers(self):
        vf = parse("for(int i=0;i<80;++i){plds(q,out,-1376,NORM);psts(q,out,-1376,NORM);}")
        vf = InputAPI.from_canonical(replace(vf, uarch={"physical_predicate_registers": 2, "vreg_num": 1}))
        rows = self.compare(vf)
        self.assertEqual(sum(row["op"] == "PLDS" for row in rows), 80)
        self.assertEqual(sum(row["op"] == "PSTS" for row in rows), 80)

    def test_branch_timing_parameters(self):
        db = ParamDB(base_dir=str(ROOT))
        self.assertEqual(db.resolve_inst("PLDS", "b8", "bool").latency, 9)
        self.assertEqual(db.resolve_inst("PSTS", "b8", "bool").latency, 9)
        self.assertEqual(db.get_forwarding_cycles("PLDS", "PSTS", producer_form="b8", consumer_form="b8"), 8)
        for form in ("fp32", "b32"):
            self.assertEqual(db.get_forwarding_cycles("VMOV", "VAXPY", producer_form=form, consumer_form="fp32"), 2)


if __name__ == "__main__":
    unittest.main()
