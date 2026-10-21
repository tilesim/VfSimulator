from dataclasses import replace
import unittest

from tools.audit_a5_instruction_coverage import declarations, variant_form, matched_forms
from api.common.instruction_catalog import DEFAULT_INSTRUCTION_CATALOG


class InstructionCoverageAuditTest(unittest.TestCase):
    def test_macros_expanded_signatures_merge_with_source_locations(self):
        signature = ('[aicore] static __attribute__((overloadable, cce_builtin_api, always_inline)) '
                     'void pxor(vector_bool &dst, vector_bool a, vector_bool b, vector_bool mask) '
                     '{ const char *diagnostic = "literal { brace"; dst=__builtin_cce_pxor_z(a,b,mask); }\n')
        text = '# 8 "/compiler/include/__clang_cce_vector_intrinsics.h"\n' + signature
        text += '# 20 "/compiler/include/npu_arch_3101/__clang_cce_vector_intrinsics.h"\n' + signature
        rows = declarations(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sources"], [
            {"header": "__clang_cce_vector_intrinsics.h", "line": 8},
            {"header": "npu_arch_3101/__clang_cce_vector_intrinsics.h", "line": 20}])
        self.assertEqual(rows[0]["builtins"], ["__builtin_cce_pxor_z"])

    def test_conversion_alignment_and_bit_form_correspondence(self):
        def entry(api, arguments, returns="void"):
            return {"api": api, "arguments": arguments, "returns": returns}
        self.assertEqual(variant_form(entry("vcvt", "vector_f16 &d,vector_f32 s,vector_bool p")), "f32_to_f16")
        self.assertEqual(variant_form(entry("vldas", "vector_align &d,__attribute__((cce_unif_buff)) half *p")), "fp16")
        self.assertEqual(variant_form(entry("movvp", "vector_bool &d,vector_u16 s,int16_t part")), "uint16")
        self.assertEqual(variant_form(entry("vcadd", "vector_s32 &d,vector_s16 s,vector_bool p")), "int16")
        self.assertEqual(matched_forms("vor", ["uint8", "uint16", "uint32"],
                                      replace(DEFAULT_INSTRUCTION_CATALOG.lookup("VOR"),
                                              form_rule="fixed", fixed_form="b32",
                                              forms=frozenset({"b32"}))), ["uint32"])
        self.assertEqual(matched_forms("plt_b32", ["b32"],
                                      DEFAULT_INSTRUCTION_CATALOG.lookup("PLT")), [])


if __name__ == "__main__":
    unittest.main()
