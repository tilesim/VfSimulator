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
        vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/v1_valid_loop.json')
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
        vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/v1_valid_loop.json')
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

    def test_missing_isa_uses_catalog_instruction_class(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "isa.json"
            path.write_text(json.dumps({"schema_version": 2, "defaults": {}, "instructions": {}}))
            db = ParamDB(base_dir=ROOT, isa_path=path)
            for opcode, form, expected in (
                ("PSTU", "uint32", "STORE"),
                ("VLDAS", "fp32", "LOAD"),
                ("VMOV", "fp32", "COMPUTE"),
                ("VLD_FUTURE", "fp32", "LOAD"),
            ):
                with self.subTest(opcode=opcode):
                    self.assertEqual(db.resolve_inst(opcode, form).op_class, expected)
                    warning = next(item for item in db.get_warnings() if item.get("op") == opcode)
                    self.assertEqual(warning["used_defaults"]["op_class"], expected)

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

class ParameterReuseTest(unittest.TestCase):
    def test_model_reads_parameters_once_for_repeated_runs(self):
        from core.params.loader import read_json
        vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/v1_valid_loop.json')
        with patch('core.params.loader.read_json', wraps=read_json) as reads:
            model = CoreVfCostModel(base_dir=ROOT, out_dir=None)
            self.assertEqual(reads.call_count, 4)
            baseline = model.run_vf_info(vf)
            overridden = InputAPI.from_canonical(replace(vf, uarch={'issue_ports': 1, 'vreg_num': 1}))
            model.run_vf_info(overridden)
            self.assertEqual(model.run_vf_info(vf), baseline)
            self.assertEqual(reads.call_count, 4)

    def test_file_updates_require_a_new_model(self):
        vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/v1_valid_loop.json')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'configs').mkdir()
            for name in ('isa.json', 'uarch.json', 'forwarding.json', 'InitiationInterval.json'):
                (root / 'configs' / name).write_text((ROOT / 'configs' / name).read_text())
            model = CoreVfCostModel(base_dir=root, out_dir=None)
            baseline = model.run_vf_info(vf)
            isa_path = root / 'configs/isa.json'
            isa = json.loads(isa_path.read_text())
            isa['defaults']['vf_drain_cost'] += 17
            isa_path.write_text(json.dumps(isa))
            self.assertEqual(model.run_vf_info(vf), baseline)
            fresh = CoreVfCostModel(base_dir=root, out_dir=None)
            self.assertEqual(fresh.run_vf_info(vf)['vf_end_cycle'], baseline['vf_end_cycle'] + 17)
            # All four files may disappear after construction; the snapshot is sufficient.
            for path in (root / 'configs').iterdir():
                path.unlink()
            self.assertEqual(model.run_vf_info(vf), baseline)

    def test_repeated_runs_keep_warnings_independent(self):
        warning_vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/timing/cli_writes_instruction_fallback_warnings_without_vreg_warning.json')
        clean_vf = InputAPI.load_json(ROOT / 'VFtest/canonical/fixtures/v1_valid_loop.json')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = CoreVfCostModel(base_dir=ROOT, out_dir=root / 'first')
            model.run_vf_info(warning_vf)
            first = json.loads((root / 'first/model_warnings.json').read_text())
            self.assertTrue(first['instruction_fallback_warnings'])
            model.out_dir = root / 'second'
            model.run_vf_info(warning_vf)
            self.assertEqual(json.loads((root / 'second/model_warnings.json').read_text()), first)
            model.out_dir = root / 'clean'
            model.run_vf_info(clean_vf)
            self.assertFalse((root / 'clean/model_warnings.json').exists())

    def test_shared_parameters_keep_profiles_and_warnings_local(self):
        from core.params.loader import load_parameters
        parameters = load_parameters(ROOT)
        first = ParamDB(parameters=parameters)
        second = ParamDB(parameters=parameters)
        producer = first.resolve_inst('VADD', 'fp32')
        consumer = first.resolve_inst('VADD', 'fp32')
        first.get_forwarding_for_profiles(producer, consumer)
        first.record_warning('run_local')
        self.assertFalse(second.get_warnings())
        with self.assertRaisesRegex(ValueError, 'different ParamDB'):
            second.get_forwarding_for_profiles(producer, consumer)

    def test_forwarding_cache_preserves_zero_compatible_fallback_and_alias(self):
        from core.params.loader import load_parameters
        parameters = load_parameters(ROOT)
        parameters = replace(parameters, forwarding={'defaults': 3, 'forwarding': {
            'VADD.fp32': {'VMUL.fp32': 0},
            'VMUL.fp32': {'*': 7},
            'VLDS.fp32': {'VADD.fp32': 9},
        }})
        for producer, consumer, pform, cform, expected in (
            ('VADD', 'VMUL', 'fp32', 'fp32', 0),
            ('VADD', 'VMUL', 'b32', 'b32', 0),
            ('VMUL', 'VADD', 'fp32', 'fp32', 7),
            ('VLDUS', 'VADD', 'fp32', 'fp32', 9),
            ('UNMODELED', 'VADD', 'fp32', 'fp32', 6),
        ):
            with self.subTest(pair=(producer, consumer, pform, cform)):
                db = ParamDB(parameters=parameters)
                with patch.object(db, '_compute_forwarding_cycles', wraps=db._compute_forwarding_cycles) as compute:
                    query = lambda: db.get_forwarding_cycles(producer, consumer, producer_form=pform, consumer_form=cform)
                    self.assertEqual(query(), expected)
                    calls, warnings = compute.call_count, db.get_warnings()
                    for _ in range(5):
                        self.assertEqual(query(), expected)
                    self.assertEqual(compute.call_count, calls)
                    self.assertEqual(db.get_warnings(), warnings)


if __name__ == '__main__':
    unittest.main()
