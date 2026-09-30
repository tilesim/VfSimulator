import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import unittest

from api.cce_adapter import parse_cce_canonical_vf_info
from api.frontend import canonical_vf_info_to_dict, validate_canonical_vf_info
from api.frontend.schema import CanonicalOperand, CanonicalValue, OperandRole, StorageKind
from api.simulator_costmodel import CoreVfCostModel


def packed_program():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "packed.cce"
        path.write_text("void vf(__ubuf__ half *input, __ubuf__ half *out) {"
                        "__VEC_SCOPE__ {vector_f16 x; vector_bool mask;"
                        "mask=pset_b32(PAT_ALL);"
                        "vlds(x,input,64,UNPK_B16);vsts(x,out,0,PK_B32,mask);}}")
        vf = parse_cce_canonical_vf_info(path)
    # NPUIR PLT has a predicate result AND an updated scalar count result.
    first = vf.context[0]
    count = CanonicalValue("count.0", "count", StorageKind.SCALAR, "uint32")
    remaining = replace(count, definition_id="count.1", producer_node_id=first.instruction_id)
    plt = replace(first, opcode="PLT", form="b32",
                  inputs=(CanonicalOperand(count.definition_id, OperandRole.SCALAR),),
                  outputs=first.outputs + (CanonicalOperand(remaining.definition_id, OperandRole.DESTINATION),),
                  attributes={"active_elements": 37})
    values = dict(vf.values)
    values[count.definition_id] = count
    values[remaining.definition_id] = remaining
    for name, value in values.items():
        if value.storage == StorageKind.REGISTER:
            values[name] = replace(value, shape=(64,))
    return replace(vf, values=values, context=(plt, *vf.context[1:]))


class PackedMemoryModeTest(unittest.TestCase):
    def test_explicit_semantics_and_native_parity(self):
        vf = packed_program()
        self.assertTrue(validate_canonical_vf_info(vf).ok)
        plt, load, store = vf.context
        self.assertEqual(load.attributes["catalog_mode"], "UNPK_B16")
        self.assertEqual(store.attributes["catalog_mode"], "PK_B32")
        self.assertEqual(load.inputs[0].memory_access.span, 64)
        self.assertEqual(load.inputs[0].memory_access.offset.constant, 64)
        self.assertEqual(store.outputs[0].memory_access.span, 64)
        self.assertEqual(store.inputs[-1].value_id, plt.outputs[0].value_id)
        with tempfile.TemporaryDirectory() as tmp:
            result = CoreVfCostModel(out_dir=tmp).run_vf_info(vf)
            rows = [json.loads(line) for line in (Path(tmp)/"start_by_cycle.json").read_text().splitlines()]
            by_op = {row["op"]: row for row in rows}
            self.assertEqual(len(by_op["PLT"]["preg_dst"]), 1)
            self.assertTrue(by_op["PLT"]["preg_dst"][0].startswith("pred"))
            self.assertIn(by_op["PLT"]["preg_dst"][0], by_op["VSTS"]["preg_src"])
            self.assertLess(result["vf_end_cycle"], 200)
            runner = os.environ.get("VFSIM_NATIVE_RUNNER")
            if runner:
                path = Path(tmp)/"vf.json"
                path.write_text(json.dumps(canonical_vf_info_to_dict(vf)))
                proc = subprocess.run([runner, "--trace", str(path), "--out-dir", tmp], capture_output=True, text=True)
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                self.assertIn(f"vfEndCycle={result['vf_end_cycle']}", proc.stdout)
                native = [json.loads(line) for line in (Path(tmp)/"start_by_cycle.json").read_text().splitlines()]
                key = lambda events: sorted((r["inst_id"], r["cy"]) for r in events)
                self.assertEqual(key(rows), key(native))

    def test_bad_span_dtype_and_predicate_are_rejected(self):
        vf = packed_program()
        for index, direction in ((1, "inputs"), (2, "outputs")):
            node = vf.context[index]
            operands = getattr(node, direction)
            bad = replace(operands[0], memory_access=replace(operands[0].memory_access, span=128))
            context = list(vf.context)
            context[index] = replace(node, **{direction: (bad, *operands[1:])})
            codes = {d.code for d in validate_canonical_vf_info(replace(vf, context=tuple(context))).errors}
            self.assertIn("catalog_memory_span_mismatch", codes)
        values = dict(vf.values)
        reg = vf.context[1].outputs[0].value_id
        values[reg] = replace(values[reg], dtype="fp32")
        self.assertFalse(validate_canonical_vf_info(replace(vf, values=values)).ok)
        plt = vf.context[0]
        context = (replace(plt, outputs=plt.outputs[:1]), *vf.context[1:])
        self.assertFalse(validate_canonical_vf_info(replace(vf, context=context)).ok)
        for dtype in ("fp32", "bool"):
            values = dict(vf.values)
            values["count.0"] = replace(values["count.0"], dtype=dtype)
            self.assertFalse(validate_canonical_vf_info(replace(vf, values=values)).ok)

    def test_checked_in_canonical_payload(self):
        from api.frontend.serialization import canonical_vf_info_from_dict
        fixture = Path(__file__).parent/"fixtures/canonical_vf_info/v2_plt_packed_fp16.json"
        vf = canonical_vf_info_from_dict(json.loads(fixture.read_text()))
        self.assertTrue(validate_canonical_vf_info(vf).ok)

    def test_payload_bytes_do_not_shrink_transaction_budget(self):
        from api.frontend.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG as catalog
        for opcode, mode in (("VLDS", "UNPK_B16"), ("VSTS", "PK_B32")):
            spec = catalog.lookup(opcode).memory_modes[mode]
            self.assertEqual(spec.memory_span * 2, 128)
            self.assertEqual(spec.ub_transfer_bytes, 256)


if __name__ == "__main__":
    unittest.main()
