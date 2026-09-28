import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from dataclasses import replace

from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG, OperandDirection
from api.frontend.schema import OperandRole
from api.simulator_costmodel import CoreVfCostModel
from tests.test_predicate_registers import parse, records


RUNNER = os.environ.get("VFSIM_NATIVE_RUNNER")
PREFIX = "mask=pset_b32(PAT_ALL);vlds(x,a,0,NORM);vlds(y,a,64,NORM);"
AXPY = "vaxpy(y,x,2.0f,mask,MODE_ZEROING);"
STORE = "vsts(y,out,0,NORM_B32,mask);"


class ReadWriteOperandTest(unittest.TestCase):
    def test_catalog_projects_old_destination_as_source(self):
        spec = DEFAULT_INSTRUCTION_CATALOG.lookup("VAXPY")
        self.assertEqual(spec.operands[0].direction, OperandDirection.READ_WRITE)
        self.assertEqual(spec.input_operands[0].role, OperandRole.SOURCE)
        self.assertEqual(spec.output_operands[0].role, OperandRole.DESTINATION)
        self.assertEqual(len(spec.input_operands), 4)
        self.assertEqual(len(spec.output_operands), 1)

    def test_old_value_is_read_before_new_definition(self):
        vf = parse(PREFIX + AXPY + AXPY + STORE)
        load, first, second, store = vf.context[2:]
        self.assertEqual(first.inputs[0].value_id, load.outputs[0].value_id)
        self.assertEqual(second.inputs[0].value_id, first.outputs[0].value_id)
        self.assertNotEqual(first.inputs[0].value_id, first.outputs[0].value_id)
        self.assertEqual(store.inputs[0].value_id, second.outputs[0].value_id)
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        bad = replace(first, inputs=first.inputs[1:])
        invalid = replace(vf, context=(*vf.context[:3], bad, *vf.context[4:]))
        self.assertFalse(validate_canonical_vf_info(invalid).ok)

    def test_same_register_in_both_input_positions(self):
        vf = parse(PREFIX + "vaxpy(y,y,2.0f,mask);" + STORE)
        inst = vf.context[3]
        self.assertEqual(inst.inputs[0].value_id, inst.inputs[1].value_id)
        self.assertNotEqual(inst.inputs[0].value_id, inst.outputs[0].value_id)

    def test_merging_remains_explicitly_unsupported(self):
        with self.assertRaisesRegex(ValueError, "MODE_MERGING"):
            parse(PREFIX + AXPY.replace("MODE_ZEROING", "MODE_MERGING"))

    def test_loop_dependency_and_native_parity(self):
        for count, unroll in ((0, 1), (1, 1), (8, 1), (8, 4)):
            with self.subTest(count=count, unroll=unroll):
                vf = parse(PREFIX + f"\n#pragma unroll({unroll})\n"
                           f"for(int i=0;i<{count};++i){{{AXPY}}}" + STORE)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    result = CoreVfCostModel(out_dir=root / "python").run_vf_info(vf)
                    starts = records(root / "python/start_by_cycle.json")
                    chain = sorted((r for r in starts if r["op"] == "VAXPY"),
                                   key=lambda r: r["inst_id"])
                    self.assertEqual(len(chain), count)
                    for prev, cur in zip(chain, chain[1:]):
                        self.assertEqual(cur["preg_src"][0], prev["preg_dst"][0])
                        self.assertGreaterEqual(cur["cy"] - prev["cy"], 5)
                    if chain:
                        store = next(r for r in starts if r["op"] == "VSTS")
                        self.assertEqual(store["preg_src"][0], chain[-1]["preg_dst"][0])
                        self.assertGreaterEqual(store["cy"] - chain[-1]["cy"], 7)
                    if RUNNER:
                        path = root / "input.json"
                        path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                        proc = subprocess.run([RUNNER, "--trace", str(path),
                                               "--out-dir", str(root / "native"),
                                               "--max-cycles", "10000"],
                                              capture_output=True, text=True, timeout=30)
                        self.assertEqual(proc.returncode, 0, proc.stderr)
                        self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                        key = lambda rows: sorted((r["inst_id"], r["op"], r["cy"]) for r in rows)
                        self.assertEqual(key(starts), key(records(root / "native/start_by_cycle.json")))
