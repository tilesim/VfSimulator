import unittest
import re
import tempfile
from pathlib import Path

from api.cce.ir import AdapterMembar
from api.cce.parser import _parse_cce_adapter_program
from api.common.symbols import (
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
    specialize_opcode,
)


ROOT = Path(__file__).resolve().parents[1]


class CCEParserTest(unittest.TestCase):
    def test_input_symbols_normalize_public_aliases(self):
        self.assertEqual(normalize_dtype("float32"), "fp32")
        self.assertEqual(normalize_dtype("fp64"), "fp64")
        self.assertEqual(normalize_form("fp32_to_s32"), "f32_to_s32")
        self.assertEqual(normalize_form("f16_to_s32"), "f16_to_s32")
        self.assertEqual(normalize_opcode("vld"), "VLD")
        self.assertEqual(normalize_opcode("vpack"), "VPACK")
        self.assertEqual(normalize_opcode("vsstb"), "VSSTB")
        self.assertEqual(normalize_opcode("vunknown"), "VUNKNOWN")
        self.assertEqual(specialize_opcode("vcvt", "fp32_to_s32"), "VCVT_F32_TO_S32")
        self.assertEqual(specialize_opcode("vcvt", "f32_to_bf16"), "VCVT_F32_TO_BF16")
        self.assertEqual(specialize_opcode("vcvt", "f16_to_s32"), "VCVT_F16_TO_S32")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VLD_VST"), "VLD_VST")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VV_ALL"), "VV_ALL")

    def test_cce_adapter_parses_vpack_and_vsstb_cast_operands(self):
        source = """
        void softmax_vf(__ubuf__ half *nz_buffer_Ptr) {
          constexpr uint64_t kVsstbConfig = ((128 + 1) << 16) | 1u;
          __VEC_SCOPE__ {
            vector_f16 vreg_x_exp_even_f16;
            vector_bool preg_low_half = pset_b16(PAT_ALL);
            vpack((vector_u16 &)vreg_x_exp_even_f16, (vector_u32 &)vreg_x_exp_even_f16, LOWER);
            vsstb(vreg_x_exp_even_f16, ((__ubuf__ half *&)nz_buffer_Ptr), kVsstbConfig, preg_low_half, NO_UPDATE);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "softmax.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="softmax_vf")

            path.write_text(
                source.replace("NO_UPDATE", "POST_UPDATE"), encoding="utf-8"
            )
            post_update_info = _parse_cce_adapter_program(
                path, kernel_name="softmax_vf"
            )

            path.write_text(
                source.replace("| 1u", "| 2u").replace("NO_UPDATE", "POST_UPDATE"),
                encoding="utf-8",
            )
            two_block_info = _parse_cce_adapter_program(path, kernel_name="softmax_vf")

            path.write_text(
                source.replace("128 + 1", "255 + 1").replace(
                    "NO_UPDATE", "POST_UPDATE"
                ),
                encoding="utf-8",
            )
            different_stride_info = _parse_cce_adapter_program(
                path, kernel_name="softmax_vf"
            )

        insts = vf_info.context[1:]
        self.assertEqual(insts[0].name, "VPACK")
        self.assertEqual(insts[0].src, ["vreg_x_exp_even_f16"])
        self.assertEqual(insts[0].dst, ["vreg_x_exp_even_f16"])
        self.assertEqual(insts[0].form, "u32_to_u16")
        self.assertEqual(insts[1].name, "VSSTB")
        self.assertEqual(insts[1].src, ["vreg_x_exp_even_f16", "preg_low_half"])
        self.assertEqual(insts[1].dst, ["nz_buffer_Ptr"])
        self.assertEqual(insts[1].form, "fp16")
        self.assertEqual(vf_info.values["vreg_x_exp_even_f16"].dtype, "fp16")
        self.assertEqual(vf_info.values["nz_buffer_Ptr"].storage, "UB")
        no_update = vf_info.context[2].memory_accesses[0]
        one_block_update = post_update_info.context[2].memory_accesses[0]
        two_block_update = two_block_info.context[2].memory_accesses[0]
        different_stride_update = different_stride_info.context[2].memory_accesses[0]
        self.assertEqual(no_update.update_mode, "none")
        self.assertIsNone(no_update.post_update_delta_bytes)
        self.assertEqual(one_block_update.update_mode, "post_update")
        self.assertEqual(one_block_update.post_update_delta_bytes, 32)
        self.assertEqual(two_block_update.post_update_delta_bytes, 64)
        self.assertEqual(different_stride_update.post_update_delta_bytes, 32)

    def test_cce_adapter_parses_symbolic_division_loop_bound(self):
        source = """
        void loop_bound_vf(__ubuf__ float *a) {
          constexpr uint16_t kRows = 128;
          constexpr uint16_t kStart = 4;
          constexpr uint16_t kStepBase = 2;
          constexpr uint16_t kStep = kStepBase * 2;
          __VEC_SCOPE__ {
            for (uint16_t i = kStart; i < kRows / 2; i += kStep) {
              vector_f32 v0;
              vlds(v0, a, 0, NORM);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "loop_bound.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(
                path,
                kernel_name="loop_bound_vf",
            )

        loop = vf_info.context[0]
        self.assertEqual(loop.count, 15)
        self.assertEqual(loop.induction_start, 4)
        self.assertEqual(loop.induction_step, 4)

    def test_cce_adapter_parses_vmulscvt_conversion_form(self):
        source = """
        void vmulscvt_vf() {
          __VEC_SCOPE__ {
            vector_f32 expv;
            vector_f16 cvt;
            vmulscvt(cvt, expv, 1.0f, pset_b32(PAT_ALL), PART_EVEN);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "vmulscvt.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="vmulscvt_vf")

        self.assertEqual(vf_info.context[1].name, "VMULSCVT")
        self.assertEqual(vf_info.context[1].form, "f32_to_f16")

    def test_cce_adapter_accepts_binary_mode_operand(self):
        source = """
        void binary_mode_vf() {
          __VEC_SCOPE__ {
            vector_f32 dst, lhs, rhs;
            vector_bool pred = pset_b32(PAT_ALL);
            (void)lhs;
            vmax(dst, lhs, rhs, pred, MODE_ZEROING);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "binary_mode.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="binary_mode_vf")

        self.assertEqual(vf_info.context[1].name, "VMAX")
        self.assertEqual(vf_info.context[1].src, ["lhs", "rhs", "pred"])
        self.assertEqual(vf_info.context[1].dst, ["dst"])

    def test_cce_adapter_registers_first_vector_decl_inside_loop(self):
        source = """
        void loop_decl_vf(__ubuf__ float *src) {
          __VEC_SCOPE__ {
            vector_bool pred = pset_b32(PAT_ALL);
            for (uint16_t row = 0; row < 2; ++row) {
              vector_f32 x0;
              vector_f32 exp0;
              vld(x0, src, 0, NORM);
              vmuls(x0, x0, 0.125f, pred);
              vexpdif(exp0, x0, x0, pred, PART_ODD);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "loop_decl.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="loop_decl_vf")

        loop = vf_info.context[1]
        vmuls = next(
            inst for inst in loop.body if getattr(inst, "name", None) == "VMULS"
        )
        vexpdif = next(
            inst for inst in loop.body if getattr(inst, "name", None) == "VEXPDIF"
        )
        self.assertEqual(vmuls.src, ["x0", "0.125f", "pred"])
        self.assertEqual(vmuls.dst, ["x0"])
        self.assertEqual(vexpdif.src, ["x0", "x0", "pred"])
        self.assertEqual(vexpdif.dst, ["exp0"])

    def test_cce_catalog_binder_rejects_missing_or_misordered_operands(self):
        sources = {
            "missing_binary_source": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs;
                    vadd(dst, lhs);
                  }
                }
            """,
            "ub_used_as_register": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, rhs;
                    vadd(dst, a, rhs, pset_b32(PAT_ALL));
                  }
                }
            """,
            "predicate_used_as_scalar": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, src;
                    vadds(dst, src, pset_b32(PAT_ALL), 1.0f);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_catalog_rejects_invalid_forms(self):
        sources = {
            "vexpdif_fp16": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f16 dst, lhs, rhs;
                    vector_bool mask = pset_b16(PAT_ALL);
                    vexpdif(dst, lhs, rhs, mask, PART_EVEN);
                  }
                }
            """,
            "vcvt_same_dtype": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f16 dst, src;
                    vector_bool mask = pset_b16(PAT_ALL);
                    vcvt(dst, src, mask, ROUND_R, RS_DISABLE, PART_EVEN);
                  }
                }
            """,
            "vmulscvt_same_dtype": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, src;
                    vector_bool mask = pset_b32(PAT_ALL);
                    vmulscvt(dst, src, 1.0f, mask, PART_EVEN);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_form.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsupported semantic form"):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_catalog_rejects_extra_or_unresolved_arguments(self):
        sources = {
            "extra_compute_argument": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vector_bool mask = pset_b32(PAT_ALL);
                    vadd(dst, lhs, rhs, mask, TOTALLY_INVALID);
                  }
                }
            """,
            "invalid_load_config": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst;
                    vld(dst, a, nonsense, also_nonsense);
                  }
                }
            """,
            "unsupported_load_mode": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst;
                    vld(dst, a, 0, TOTALLY_INVALID);
                  }
                }
            """,
            "undeclared_predicate": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, potato);
                  }
                }
            """,
            "predicate_name_contains_pset": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, not_a_pset_b32_value);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_argument.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_predicate_uses_declared_bool_not_name_prefix(self):
        source = """
            void valid() {
              vector_bool lane_mask = pset_b32(PAT_ALL);
              __VEC_SCOPE__ {
                vector_f32 dst, lhs, rhs;
                vadd(dst, lhs, rhs, lane_mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "predicate.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].name, "VADD")

    def test_cce_predicate_obeys_lexical_scope_and_declaration_order(self):
        sources = {
            "use_before_declaration": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, late_mask);
                    vector_bool late_mask = pset_b32(PAT_ALL);
                  }
                }
            """,
            "loop_local_used_after_loop": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    for (int i = 0; i < 2; ++i) {
                      vector_bool local_mask = pset_b32(PAT_ALL);
                      vadd(dst, lhs, rhs, local_mask);
                    }
                    vadd(dst, lhs, rhs, local_mask);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_scope.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "must be a predicate"):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_load_store_and_vdup_call_variants(self):
        source = """
            void valid(__ubuf__ float *a, __ubuf__ float *b) {
              __VEC_SCOPE__ {
                vector_f32 value, broadcast;
                vector_bool mask = pset_b32(PAT_ALL);
                vlds(value, a, 64, NORM, POST_UPDATE);
                vsts(value, b, 64, NORM_B32, mask, POST_UPDATE);
                vdup(value, 0.0f, mask, MODE_ZEROING);
                vdup(broadcast, value, mask, POS_LOWEST, MODE_ZEROING);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "call_variants.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(
            [node.name for node in vf_info.context],
            ["PSET_B32", "VLDS", "VSTS", "VDUPS", "VDUP"],
        )

    def test_cce_vector_align_stores_use_state_attributes(self):
        source = """
            void valid(__ubuf__ float *a) {
              __VEC_SCOPE__ {
                vector_f32 value;
                vector_align u1;
                vstus(u1, 1, value, a, POST_UPDATE);
                vstas(u1, a, 0, POST_UPDATE);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "optional_stores.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(
            [(node.name, node.form) for node in vf_info.context],
            [("VSTUS", "fp32"), ("VSTAS", "fp32")],
        )
        vstus, vstas = vf_info.context
        self.assertEqual(vstus.src, ["value"])
        self.assertEqual(vstas.src, [])
        self.assertEqual(
            vstus.attributes["align_state_id"],
            vstas.attributes["align_state_id"],
        )
        self.assertEqual(vstus.attributes["align_state_operation"], "append")
        self.assertEqual(vstas.attributes["align_state_operation"], "consume")

    def test_cce_rejects_invalid_vdup_call_variant_combinations(self):
        sources = (
            "vdup(dst, src, mask, POS_LOWEST);",
            "vdup(dst, src, mask, MODE_ZEROING, MODE_ZEROING);",
        )
        for call in sources:
            with self.subTest(call=call), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_vdup.cce"
                path.write_text(
                    f"""
                    void bad() {{
                      __VEC_SCOPE__ {{
                        vector_f32 dst, src;
                        vector_bool mask = pset_b32(PAT_ALL);
                        {call}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "call variant"):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_offset_rejects_non_affine_products(self):
        for expression in ("i * j", "i * i"):
            with (
                self.subTest(expression=expression),
                tempfile.TemporaryDirectory() as tmpdir,
            ):
                path = Path(tmpdir) / "non_affine.cce"
                path.write_text(
                    f"""
                    void bad(__ubuf__ float *a) {{
                      __VEC_SCOPE__ {{
                        vector_f32 value;
                        for (int i = 0; i < 2; ++i) {{
                          for (int j = 0; j < 2; ++j) {{
                            vlds(value, a, {expression}, NORM);
                          }}
                        }}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "invalid offset expression"):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_rejects_integer_encoding_for_symbolic_config_operands(self):
        calls = (
            "vlds(x, mem, 0, 123);",
            "vexp(y, x, pred, 123);",
        )
        for call in calls:
            with self.subTest(call=call), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "integer_config.cce"
                path.write_text(
                    f"""
                    void bad(__ubuf__ float *mem) {{
                      __VEC_SCOPE__ {{
                        vector_f32 x, y;
                        vector_bool pred = pset_b32(PAT_ALL);
                        {call}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "unsupported configuration"):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_local_integer_affine_offsets(self):
        source = """
            void valid(__ubuf__ float *input) {
              const uint32_t base = 1;
              __VEC_SCOPE__ {
                vector_f32 value;
                for (int i = 0; i < 2; ++i) {
                  uint32_t stride = 64;
                  uint32_t off = stride * i + base;
                  vlds(value, input, off, NORM);
                }
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "local_affine.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].body[0].name, "VLDS")

    def test_cce_local_scalar_offsets_obey_scope_and_affine_rules(self):
        sources = {
            "use_before_declaration": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    vlds(value, input, off, NORM);
                    uint32_t off = 64;
                  }
                }
                """,
            ),
            "loop_local_used_outside": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    for (int i = 0; i < 2; ++i) {
                      uint32_t off = 64 * i;
                      vlds(value, input, off, NORM);
                    }
                    vlds(value, input, off, NORM);
                  }
                }
                """,
            ),
            "non_affine_initializer": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    for (int i = 0; i < 2; ++i) {
                      uint32_t off = i * i;
                      vlds(value, input, off, NORM);
                    }
                  }
                }
                """,
            ),
        }
        for name, (message, source) in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_local_scalar.cce"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_local_float_is_available_as_scalar_operand(self):
        source = """
            void valid() {
              __VEC_SCOPE__ {
                vector_f32 dst, src;
                vector_bool mask = pset_b32(PAT_ALL);
                float scale = 0.5f;
                vadds(dst, src, scale, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "local_float.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(vf_info.context[1].src, ["src", "scale", "mask"])

    def test_cce_local_scalar_initializer_is_validated_only_for_offsets(self):
        source = """
            void valid() {
              uint32_t tid = get_block_idx();
              __VEC_SCOPE__ {
                vector_f32 dst, src;
                vector_bool mask = pset_b32(PAT_ALL);
                vadds(dst, src, tid, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "delayed_scalar.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="valid")
        self.assertEqual(vf_info.context[1].src, ["src", "tid", "mask"])

        offset_source = """
            void bad(__ubuf__ float *input) {
              uint32_t tid = get_block_idx();
              __VEC_SCOPE__ {
                vector_f32 value;
                vlds(value, input, tid, NORM);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "invalid_delayed_offset.cce"
            path.write_text(offset_source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid offset expression"):
                _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_rejects_unmodeled_scope_statements_with_source_text(self):
        sources = {
            "assignment": "off = 64 * i;",
            "non_vector_call": "update_offset(off);",
        }
        for name, statement in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "unsupported_statement.cce"
                path.write_text(
                    f"""
                    void bad() {{
                      __VEC_SCOPE__ {{
                        uint32_t off = 0;
                        for (int i = 0; i < 2; ++i) {{
                          {statement}
                        }}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    re.escape(statement),
                ):
                    _parse_cce_adapter_program(path, kernel_name="bad")

    def test_cce_standalone_pset_requires_destination(self):
        source = """
            void valid() {
              __VEC_SCOPE__ {
                vector_f32 dst, lhs, rhs;
                vector_bool mask = pset_b32(PAT_ALL);
                pset_b32(PAT_ALL);
                vadd(dst, lhs, rhs, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pset_noop.cce"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be a predicate"):
                _parse_cce_adapter_program(path, kernel_name="valid")

    def test_cce_adapter_parses_mem_bar_call(self):
        source = """
        void barrier_vf(__ubuf__ float *a, __ubuf__ float *b) {
          __VEC_SCOPE__ {
            vector_f32 v0;
            vsts(v0, a, 0, NORM_B32, pset_b32(PAT_ALL));
            mem_bar(VST_VLD);
            vlds(v0, b, 0, NORM);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "barrier.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="barrier_vf")

        membars = [node for node in vf_info.context if isinstance(node, AdapterMembar)]
        self.assertEqual(len(membars), 1)
        self.assertEqual(membars[0].type, "VST_VLD")

    def test_cce_adapter_keeps_scalar_function_params_as_scalar(self):
        source = """
        void scalar_param_vf(__ubuf__ float *a, float epsilon) {
          __VEC_SCOPE__ {
            vector_bool p = pset_b32(PAT_ALL);
            vector_f32 v0;
            vlds(v0, a, 0, NORM);
            vadds(v0, v0, epsilon, p);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scalar_param.cce"
            path.write_text(source, encoding="utf-8")
            vf_info = _parse_cce_adapter_program(path, kernel_name="scalar_param_vf")

        self.assertEqual(vf_info.values["a"].storage, "UB")
        self.assertEqual(vf_info.values["epsilon"].storage, "Scalar")


if __name__ == "__main__":
    unittest.main()
