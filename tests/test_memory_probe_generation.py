import struct
import unittest
from pathlib import Path
from runpy import run_path

make_case = run_path(str(
    Path(__file__).resolve().parents[1]
    / "skills/vfsim-instruction-benchmark/scripts/memory_probe.py"
))["make_case"]


class MemoryProbeGenerationTest(unittest.TestCase):
    def test_vldas_forwarding_probes_have_no_loop(self):
        for kind in ("vldus_straight", "vldus_no_update"):
            source, data, golden = make_case(kind, "fp32", 1, 1)
            self.assertNotIn("for (", source)
            self.assertEqual(golden, data[4:260])
            self.assertIn("vldas(state, ptr)", source)
            self.assertEqual("POST_UPDATE" in source, kind == "vldus_straight")
    def test_paired_load_golden(self):
        for kind, stride in (("dual_pairs", 256), ("norm_pairs", 128)):
            source, data, golden = make_case(kind, "fp32", 0, 8)
            values = struct.unpack(f"<{len(data)//4}f", data)
            actual = struct.unpack(f"<{len(golden)//4}f", golden)
            self.assertEqual(len(actual), stride * 8)
            self.assertLess(source.index("vlds(c"), source.index("vadds(a"))
            for i in range(8):
                block = values[i*stride:(i+1)*stride]
                if kind == "dual_pairs":
                    block = block[:128:2] + block[1:128:2] + block[128::2] + block[129::2]
                self.assertEqual(actual[i*stride:(i+1)*stride], tuple(x+1 for x in block))

    def test_double_result_golden(self):
        source, data, golden = make_case("vldsx2", "fp32", 8, 1)
        values = struct.unpack("<256f", data)[8:136]
        self.assertEqual(struct.unpack("<128f", golden), values[::2] + values[1::2])
        self.assertIn("vlds(even, odd", source)

    def test_element_offsets_and_post_update(self):
        for dtype, width, lanes in [("fp16", 2, 128), ("fp32", 4, 64), ("int32", 4, 64)]:
            source, data, golden = make_case("vldus", dtype, 1, 3)
            self.assertEqual(golden, data[width:width + 768])
            self.assertIn(f"ptr, {lanes}, POST_UPDATE", source)

    def test_pstu_packing_and_guards(self):
        for dtype, width, packed_bytes in [("fp32", 4, 8), ("fp16", 2, 16)]:
            _, _, golden = make_case("pstu", dtype, 8, 3)
            start = width * 8
            self.assertEqual(golden[:start], b"\xa5" * start)
            self.assertEqual(golden[start:start+packed_bytes*3],
                             (b"\xff"*4 + b"\x00"*(packed_bytes-4))*3)
            self.assertEqual(golden[start+packed_bytes*3:], b"\xa5"*(256-start-packed_bytes*3))

    def test_predicate_store_bitmap_and_guards(self):
        for suffix, stride in (("", 1), ("16", 2), ("32", 4)):
            source, data, golden = make_case("predicate_pset_store" + suffix, "fp32", 0, 1)
            bitmap = sum(1 << bit for bit in range(0, 256, stride)).to_bytes(32, "little")
            self.assertEqual(golden[:32], data[:32])
            self.assertEqual(golden[32:64], bitmap)
            self.assertEqual(golden[64:], data[64:256])
            self.assertIn("psts(p,saved,32,NORM)", source)

    def test_predicate_reload_uses_sparse_mask(self):
        _, data, golden = make_case("predicate_load_select", "fp32", 0, 1)
        values = struct.unpack("<256f", data)
        selected = struct.unpack("<64f", golden)
        self.assertEqual(selected, tuple(values[i] if i % 2 == 0 else values[64 + i] for i in range(64)))
        self.assertEqual(data[768:800], sum(1 << (4 * i) for i in range(0, 64, 2)).to_bytes(32, "little"))

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            make_case("vldus", "fp32", 100, 3)
        with self.assertRaises(ValueError):
            make_case("vldsx2", "fp32", 200, 1)


if __name__ == "__main__":
    unittest.main()
