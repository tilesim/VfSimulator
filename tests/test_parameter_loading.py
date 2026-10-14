import json
import os
import tempfile
import unittest
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path
from unittest.mock import patch

from api.common.uarch_validation import UARCH_FIELD_SPECS, validate_uarch_overrides
from api.input_api import InputAPI
from core.simulator import CoreVfCostModel
from core.params.database import ParamDB
from core.params.loader import resolve_path
from core.params.types import InstructionProfile, UarchParameters
from core.params.uarch import resolve_uarch

ROOT = Path(__file__).resolve().parents[1]


class ParameterLoadingTest(unittest.TestCase):
    def test_explicit_environment_and_default_paths_have_one_priority_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            explicit, env, default = (root / name for name in ('explicit.json', 'env.json', 'default.json'))
            for path in (explicit, env, default):
                path.write_text('{}')
            with patch.dict('os.environ', {'VFSIM_TEST_PARAM_PATH': str(env)}):
                self.assertEqual(resolve_path(root, explicit=explicit, env_key='VFSIM_TEST_PARAM_PATH', candidates=('default.json',)), explicit)
                self.assertEqual(resolve_path(root, explicit=None, env_key='VFSIM_TEST_PARAM_PATH', candidates=('default.json',)), env)
            with patch.dict('os.environ', {}, clear=True):
                self.assertEqual(resolve_path(root, explicit=None, env_key='VFSIM_TEST_PARAM_PATH', candidates=('default.json',)), default)
                self.assertIsNone(resolve_path(root, explicit=None, env_key='VFSIM_TEST_PARAM_PATH', candidates=('missing.json',), required=False))
            with self.assertRaises(FileNotFoundError):
                resolve_path(root, explicit=root/'missing.json', env_key='VFSIM_TEST_PARAM_PATH', candidates=(), required=False)

    def test_malformed_optional_json_is_an_error_instead_of_a_timing_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'invalid.json'
            for content in ('{"unfinished":', '[]'):
                path.write_text(content)
                for field in ('forwarding_path', 'ii_path'):
                    with self.subTest(content=content, field=field), self.assertRaisesRegex(ValueError, 'invalid.json'):
                        ParamDB(base_dir=ROOT, **{field: str(path)})

    def test_invalid_pair_cells_are_rejected_at_load_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pairs.json'
            for value in (True, '6', 1.5, -1):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'forwarding': {'VADD.fp32': {'VMUL.fp32': value}}}))
                    with self.assertRaisesRegex(ValueError, 'forwarding'):
                        ParamDB(base_dir=ROOT, forwarding_path=str(path))
            path.write_text(json.dumps({'InitiationInterval': {'VADD.fp32': {'VMUL.fp32': 0}}}))
            with self.assertRaisesRegex(ValueError, 'II'):
                ParamDB(base_dir=ROOT, ii_path=str(path))

    def test_typed_uarch_merges_overrides_without_mutating_the_base(self):
        base = {'shq_depth': 58, 'issue_ports': 2}
        cfg = resolve_uarch(base, {'shq_depth': 32})
        self.assertIsInstance(cfg, UarchParameters)
        self.assertEqual(cfg.shq_depth, 32)
        self.assertEqual(base['shq_depth'], 58)
        self.assertEqual(cfg.consumer_release_start_offset, 4)
        with self.assertRaises(FrozenInstanceError):
            cfg.shq_depth = 16
        for value in ('32', True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'shq_depth'):
                resolve_uarch({'shq_depth': value})

    def test_unknown_fields_are_rejected_in_overrides_and_config_files(self):
        for name, value in (
            ('shq_dept', 32), ('unrecognized_setting', False), ('extra_latency', 0),
        ):
            with self.subTest(field=name):
                diagnostics = validate_uarch_overrides({name: value}).errors
                self.assertEqual([d.code for d in diagnostics], ['unknown_uarch_field'])
                self.assertEqual(diagnostics[0].context['path'], f'uarch.{name}')
                with self.assertRaisesRegex(ValueError, name):
                    resolve_uarch({}, {name: value})
                with tempfile.TemporaryDirectory() as tmp:
                    path = Path(tmp) / 'uarch.json'
                    path.write_text(json.dumps({name: value}))
                    with self.assertRaisesRegex(ValueError, name):
                        ParamDB(base_dir=ROOT, uarch_path=path)

    def test_model_accepts_supported_exu_ports_and_rejects_out_of_range(self):
        vf = InputAPI.load_json(ROOT / 'tests/fixtures/canonical_vf_info/v1_valid_loop.json')
        for ports in (1, 2):
            with self.subTest(ports=ports):
                accepted = InputAPI.from_canonical(replace(vf, uarch={'issue_ports': ports}))
                result = CoreVfCostModel(base_dir=ROOT, out_dir=None).run_vf_info(accepted)
                self.assertGreater(result['vf_end_cycle'], 0)
        for ports in (-1, 0, 3, 4):
            with self.subTest(ports=ports):
                accepted = InputAPI.from_canonical(replace(vf, uarch={'issue_ports': ports}))
                with self.assertRaisesRegex(ValueError, 'one or two EXU ports'):
                    CoreVfCostModel(base_dir=ROOT, out_dir=None).run_vf_info(accepted)

    def test_config_file_port_count_is_checked_at_model_assembly(self):
        vf = InputAPI.load_json(ROOT / 'tests/fixtures/canonical_vf_info/v1_valid_loop.json')
        config = json.loads((ROOT / 'configs/uarch.json').read_text())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'uarch.json'
            path.write_text(json.dumps({**config, 'issue_ports': 3}))
            with patch.dict(os.environ, {'UARCH_JSON_PATH': str(path)}):
                with self.assertRaisesRegex(ValueError, 'one or two EXU ports'):
                    CoreVfCostModel(base_dir=ROOT, out_dir=None).run_vf_info(vf)

    def test_unknown_config_fields_are_rejected_for_any_value_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'uarch.json'
            for value in ({}, {'VADD': 1}, None, 0, False):
                with self.subTest(value=value):
                    path.write_text(json.dumps({'unrecognized_setting': value}))
                    with self.assertRaisesRegex(ValueError, 'Unknown uarch field: unrecognized_setting'):
                        ParamDB(base_dir=ROOT, uarch_path=path)

    def test_instruction_form_takes_precedence_over_fallback_dtype(self):
        db = ParamDB(base_dir=ROOT)
        for op, form in (('VADD', 'fp32'), ('VADD', 'fp16'),
                         ('VCVT_F32_TO_F16', 'f32_to_f16')):
            with self.subTest(op=op, form=form):
                profile = db.resolve_inst(op, form, dtype='fp16')
                self.assertEqual(profile.requested_form, form)
                self.assertEqual(profile.latency, db.get_inst_form(op, form)['latency'])
        self.assertEqual(db.resolve_inst('VADD', dtype='fp16').requested_form, 'fp16')

    def test_override_contract_covers_active_scalar_runtime_fields(self):
        names = {f.name for f in fields(UarchParameters)} - {'membar_timing'}
        self.assertEqual(set(UARCH_FIELD_SPECS), names)
        overrides = {
            'enable_exq_greedy_balance': True,
            'shq_exq_static_rr': True,
            'theoretical_limit_mode': True,
            'theoretical_limit_shq_depth': 128,
            'theoretical_limit_exq_issue_inflight_cap_per_port': 2,
        }
        self.assertFalse(validate_uarch_overrides(overrides).errors)
        cfg = resolve_uarch({}, overrides)
        self.assertTrue(cfg.enable_exq_greedy_balance)
        self.assertTrue(cfg.shq_exq_static_rr)
        self.assertEqual((cfg.shq_depth, cfg.exq_issue_inflight_cap_per_port), (128, 2))
        for name in ('enable_exq_greedy_balance', 'theoretical_limit_shq_depth'):
            self.assertEqual(
                [d.code for d in validate_uarch_overrides({name: 'invalid'}).errors],
                ['uarch_field_type_mismatch'],
            )

    def test_profiles_resolve_ports_once_and_keep_cache_ownership(self):
        db = ParamDB(base_dir=ROOT)
        for op, form, ports in (('VADD','fp32',(0,1)), ('VPACK','b32',(0,))):
            profile = db.resolve_inst(op, form, form)
            self.assertIsInstance(profile, InstructionProfile)
            self.assertEqual(profile.allowed_exu_ports, ports)
        producer = db.resolve_inst('VLD_FUTURE', 'fp32', instruction_class='COMPUTE')
        self.assertEqual(producer.op_class, 'COMPUTE')
        self.assertIs(producer, db.resolve_inst('VLD_FUTURE', 'fp32', instruction_class='COMPUTE'))
        consumer = db.resolve_inst('VADD', 'fp32')
        self.assertGreaterEqual(db.get_forwarding_for_profiles(producer, consumer), 0)
        self.assertEqual(db.get_ii_for_profiles(None, consumer), 1)
        other = ParamDB(base_dir=ROOT)
        with self.assertRaisesRegex(ValueError, 'different ParamDB'):
            db.get_ii_for_profiles(None, other.resolve_inst('VADD', 'fp32'))


if __name__ == '__main__':
    unittest.main()
