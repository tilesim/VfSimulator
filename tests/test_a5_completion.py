"""Semantic and scheduling checks for the CANN 3101 completion work."""

import json
from pathlib import Path
import tempfile
import unittest

from api.input_api import InputAPI
from api.common.schema import CanonicalInstruction
from core.params.database import ParamDB
from core.simulator import CoreVfCostModel


ROOT = Path(__file__).resolve().parents[1]


class A5CompletionTests(unittest.TestCase):
    def parse(self, body, parameters=""):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.cce"
            path.write_text(f"void vf({parameters}){{__VEC_SCOPE__{{{body}}}}}")
            return InputAPI.load_cce(path)

    def test_broadcast_register_and_scalar_have_distinct_timing(self):
        vf = self.parse(
            "vector_f32 a,b;vector_bool p=pset_b32(PAT_ALL);"
            "vdup(a,b,p,POS_LOWEST,MODE_ZEROING);"
            "vdup(b,s,p,MODE_ZEROING);",
            "float s",
        )
        self.assertEqual(
            [node.opcode for node in vf.context], ["PSET_B32", "VDUP", "VDUPS"]
        )
        db = ParamDB(base_dir=ROOT)
        self.assertEqual(db.resolve_inst("VDUP", "fp32").latency, 11)
        self.assertEqual(db.resolve_inst("VDUPS", "fp32").latency, 6)
        self.assertEqual(
            db.get_forwarding_cycles(
                "VDUP", "VDUP", "fp32", producer_form="fp32", consumer_form="fp32"
            ),
            8,
        )

    def test_zeroing_then_masked_copy_preserves_old_destination(self):
        vf = self.parse(
            "vector_f32 a,b;vector_bool p=pset_b32(PAT_ALL);"
            "vbr(a,2.0f);vadd(a,a,b,p,MODE_MERGING);"
        )
        broadcast, compute, merge = vf.context[1:]
        self.assertEqual((compute.opcode, merge.opcode), ("VADD", "VMOV_MERGE"))
        self.assertEqual(merge.inputs[0].value_id, broadcast.outputs[0].value_id)
        self.assertEqual(merge.inputs[1].value_id, compute.outputs[0].value_id)

    def test_bf16_scalar_merge_reads_header_local(self):
        vf = self.parse(
            "vector_bf16 a;vector_bool p=pset_b16(PAT_ALL);"
            "vbr(a,s);vdup(a,s,p,MODE_MERGING);",
            "bfloat16_t s",
        )
        old = vf.context[1].outputs[0].value_id
        merge = vf.context[2]
        self.assertNotEqual(merge.inputs[0].value_id, old)
        self.assertIsNone(vf.values[merge.inputs[0].value_id].producer_node_id)

    def test_vag_nested_strides_follow_iteration_ordinals(self):
        vf = InputAPI.load_cce(ROOT / "VFtest/cce/contracts/vector_a5_address.cce")
        load, store = vf.context[1].body[0].body
        for node, operands in ((load, load.inputs), (store, store.outputs)):
            memory = next(
                value.memory_access for value in operands if value.memory_access
            )
            self.assertEqual(memory.address_unit_bytes, 1)
            self.assertEqual(memory.span, 256)
            self.assertEqual(memory.offset.constant, 0)
            self.assertEqual(
                sorted(term.coefficient for term in memory.offset.terms), [256, 768]
            )

    def test_paired_load_and_store_have_512_byte_layout(self):
        vf = self.parse(
            "vector_2xvl_u64 a;vector_bool p=pset_b32(PAT_ALL);"
            "vlds(a,input,3);vsts(a,output,5,p);",
            "__ubuf__ uint64_t *input,__ubuf__ uint64_t *output",
        )
        load = vf.context[1]
        self.assertEqual(load.opcode, "VLDS_PAIR")
        self.assertEqual(len(load.outputs), 2)
        self.assertEqual(load.inputs[0].memory_access.span, 512)
        self.assertEqual(load.inputs[0].memory_access.offset.constant, 24)
        stores = [node for node in vf.context if node.opcode == "VSTS_BYTES"]
        self.assertEqual(
            [node.outputs[0].memory_access.offset.constant for node in stores],
            [40, 296],
        )

    def test_paired_indexed_accesses_use_two_word_indices(self):
        vf = self.parse(
            "vector_2xvl_u64 a;vector_u32 idx;vector_bool p=pset_b32(PAT_ALL);"
            "vgather2(a,input+2,idx,p);",
            "__ubuf__ uint64_t *input",
        )
        multiply, add, low, high = vf.context[1:]
        self.assertEqual((multiply.opcode, add.opcode), ("VMULS", "VADDS"))
        self.assertEqual(low.inputs[1].value_id, multiply.outputs[0].value_id)
        self.assertEqual(high.inputs[1].value_id, add.outputs[0].value_id)
        for node in (low, high):
            self.assertEqual(node.opcode, "VGATHER2_BYTES")
            self.assertEqual(node.inputs[0].memory_access.offset.constant, 16)
            self.assertEqual(node.inputs[0].memory_access.address_unit_bytes, 1)

    def test_paired_software_loop_keeps_live_carried_halves(self):
        vf = InputAPI.load_cce(ROOT / "VFtest/cce/contracts/vector_a5_software.cce")
        loop = next(node for node in vf.context if hasattr(node, "body"))
        self.assertGreaterEqual(len(loop.carried_values), 2)
        operations = [
            node.opcode for node in loop.body if isinstance(node, CanonicalInstruction)
        ]
        self.assertIn("VADDC", operations)
        self.assertIn("VADDCS", operations)
        self.assertTrue(
            all(not value.dtype.startswith("2xvl_") for value in vf.values.values())
        )

    def test_invalid_pair_component_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "component index"):
            self.parse("vector_2xvl_u64 a;vector_u32 b;vmov(b,a.val[2]);")

    def test_native_width_unaligned_store_keeps_cann_discarded_pointer(self):
        vf = self.parse(
            "vector_f32 a;vstore(output,a,31,ADDRESS_UNALIGNED);",
            "__ubuf__ float *output",
        )
        append, flush = vf.context
        first, second = (node.outputs[0].memory_access for node in vf.context)
        self.assertNotEqual(first.address_state_id, second.address_state_id)
        self.assertEqual(first.post_update_delta_bytes.constant, 124)
        self.assertTrue(flush.attributes["cann_pointer_result_discarded"])

    def test_block_store_range_includes_stride(self):
        vf = self.parse(
            "vector_u16 a;vector_bool p=pset_b16(PAT_ALL);"
            "vsstb(a,output,((2<<16)|1),p);",
            "__ubuf__ uint16_t *output",
        )
        access = vf.context[1].outputs[0].memory_access
        self.assertEqual(access.offset.constant, 16)
        self.assertEqual(access.span, 240)

    def test_predicate_immediate_blocks_and_scalar_bytes(self):
        vf = self.parse(
            "vector_bool p;pldi(p,input,2,NORM);pldi(p,input,4,US);"
            "pldi(p,input,2,DS);plds(p,input,64,NORM);"
            "psti(p,output,4,PK,POST_UPDATE);psts(p,output,64,NORM,POST_UPDATE);",
            "__ubuf__ uint32_t *input,__ubuf__ uint32_t *output",
        )
        for node in vf.context[:4]:
            access = node.inputs[0].memory_access
            self.assertEqual(access.offset.constant, 64)
            self.assertEqual(access.address_unit_bytes, 1)
        for node in vf.context[4:]:
            access = node.outputs[0].memory_access
            self.assertEqual(access.post_update_delta_bytes.constant, 64)
        self.assertEqual(
            [node.inputs[0].memory_access.span for node in vf.context[:4]],
            [32, 16, 64, 32],
        )

    def test_unmasked_block_gather_has_no_predicate_input(self):
        vf = self.parse(
            "vector_u16 r;vector_u32 index;vgatherb(r,input,index);",
            "__ubuf__ uint16_t *input",
        )
        instruction = vf.context[0]
        self.assertEqual(instruction.opcode, "VGATHERB_UNMASKED")
        self.assertEqual(len(instruction.inputs), 2)

    def test_s4_no_offset_load_uses_signed_byte_view(self):
        vf = self.parse("vector_s4x2 r;vld(r,input,NORM);", "__ubuf__ int4x2_t *input")
        self.assertEqual((vf.context[0].opcode, vf.context[0].form), ("VLDS", "int8"))

    def test_immediate16_has_two_parts_and_only_structural_assembly_delay(self):
        vf = self.parse("vector_s16 a;vdupi_s16(a,-1234);")
        low, high = vf.context
        self.assertEqual((low.opcode, high.opcode), ("VDUPI", "VDUPI_HI"))
        self.assertEqual(high.inputs[0].value_id, low.outputs[0].value_id)
        db = ParamDB(base_dir=ROOT)
        self.assertEqual(
            db.get_forwarding_cycles(
                "VDUPI",
                "VDUPI_HI",
                "int16",
                producer_form="int16",
                consumer_form="int16",
            ),
            0,
        )

    def test_indexed_load_waits_for_index_producer(self):
        vf = self.parse(
            "vector_u32 index;vector_f32 result;vector_bool p=pset_b32(PAT_ALL);"
            "vci((vector_s32 &)index,0,INC_ORDER);"
            "vgather2(result,input,index,p);",
            "__ubuf__ float *input",
        )
        with tempfile.TemporaryDirectory() as folder:
            CoreVfCostModel(base_dir=ROOT, out_dir=folder).run_vf_info(vf)
            events = [
                json.loads(line)
                for line in (Path(folder) / "start_by_cycle.json")
                .read_text()
                .splitlines()
            ]
        starts = {event["op"]: event["cy"] for event in events}
        db = ParamDB(base_dir=ROOT)
        gap = db.get_forwarding_cycles(
            "VCI", "VGATHER2", "fp32", producer_form="int32", consumer_form="fp32"
        )
        self.assertGreaterEqual(starts["VGATHER2"], starts["VCI"] + gap)

    def test_state_publication_is_separate_from_vector_data_latency(self):
        db = ParamDB(base_dir=ROOT)
        producer = db.resolve_inst("VLDUS", "fp32")
        self.assertEqual(producer.latency, 9)
        self.assertEqual(producer.align_state_latency, 1)
        for field, value in [
            ("align_state_latency", -1),
            ("lsu_issue_interval", 0),
            ("align_state_latency", True),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                data = db.get_inst_form("VLDS", "fp32")
                db._insts["INVALID_STATE"] = {
                    "op_class": "LOAD",
                    "forms": {"fp32": {**data, field: value}},
                }
                db.resolve_inst("INVALID_STATE", "fp32")

    def test_distribution_mode_changes_done_latency_and_cache_identity(self):
        db = ParamDB(base_dir=ROOT)
        normal = db.resolve_inst("VSTS", "uint8")
        first = db.resolve_inst("VSTS", "uint8", memory_mode="ONEPT_B8")
        self.assertEqual((normal.latency, first.latency), (9, 13))
        self.assertIs(first, db.resolve_inst("VSTS", "uint8", memory_mode="ONEPT_B8"))
        self.assertIsNot(first, normal)
        vf = self.parse(
            "vector_u8 a;vector_bool p=pset_b8(PAT_ALL);"
            "vlds(a,input,0,NORM);vsts(a,output,0,ONEPT_B8,p);",
            "__ubuf__ uint8_t *input,__ubuf__ uint8_t *output",
        )
        with tempfile.TemporaryDirectory() as folder:
            CoreVfCostModel(base_dir=ROOT, out_dir=folder).run_vf_info(vf)
            start = [
                json.loads(line)
                for line in (Path(folder) / "start_by_cycle.json")
                .read_text()
                .splitlines()
            ]
            done = [
                json.loads(line)
                for line in (Path(folder) / "done_by_cycle.json")
                .read_text()
                .splitlines()
            ]
        begin = next(event["cy"] for event in start if event["op"] == "VSTS")
        end = next(event["cy"] for event in done if event["op"] == "VSTS")
        self.assertEqual(end - begin, 13)

    def test_load_distribution_uses_byte_spans_and_element_offsets(self):
        vf = self.parse(
            "vector_u32 a;vlds(a,input,3,BRC_B8);", "__ubuf__ uint32_t *input"
        )
        access = vf.context[0].inputs[0].memory_access
        self.assertEqual(
            (access.address_unit_bytes, access.span, access.offset.constant), (1, 1, 12)
        )


if __name__ == "__main__":
    unittest.main()
