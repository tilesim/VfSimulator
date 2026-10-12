import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

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
