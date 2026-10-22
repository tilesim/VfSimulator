import json
from pathlib import Path

from core.param_db import ParamDB


def test_fp16_sqrt_log_latency_comes_from_verified_single_instruction_samples():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root/'tests/fixtures/elementwise_fp16/transcendental_latency.json').read_text())
    db = ParamDB(base_dir=str(root))
    for sample in evidence['samples']:
        assert sample['golden']['passed'] and sample['dynamic_counts_match']
        for op,histogram in sample['latency_histograms'].items():
            assert sum(histogram.values()) == 1
            latency = int(next(iter(histogram)))
            profile = db.resolve_inst(op.removeprefix('RV_'),'fp16','fp16')
            assert profile.latency == latency
            assert profile.fu_type == 'SFU'
