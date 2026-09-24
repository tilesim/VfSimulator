import struct
import unittest
from cce_code.predicate_select_test.memory_probe import make_case


class MemoryProbeGenerationTest(unittest.TestCase):
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

    def test_out_of_bounds_rejected(self):
        with self.assertRaises(ValueError):
            make_case("vldus", "fp32", 100, 3)
        with self.assertRaises(ValueError):
            make_case("vldsx2", "fp32", 200, 1)


if __name__ == "__main__":
    unittest.main()
