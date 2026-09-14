"""Reproduce global timing measurements without any UB experiment API."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from api.cce_adapter import parse_cce_canonical_vf_info
from tools.membar_fixture_export import canonical_vf_info_to_dict
from api.simulator_costmodel import CoreVfCostModel


def events(folder, filename):
    text = (folder / filename).read_text()
    rows = json.loads(text) if filename == 'membar_history.json' else [
        json.loads(line) for line in text.splitlines() if line.strip()]
    return sorted((r['stream_seq'], r['cy'], r.get('op', ''),
                   r.get('event', ''), r.get('barrier', '')) for r in rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--native-runner', type=Path)
    args = parser.parse_args()
    summary = json.loads((ROOT/'docs/validation/membar_timing_summary.json').read_text())
    report = {'target': 'A5', 'cases': []}
    for case in summary['cases']:
        folder = args.out_dir / case['name']
        folder.mkdir(parents=True, exist_ok=True)
        source = ROOT/'tests/fixtures/membar_timing'/case['fixture']
        vf = parse_cce_canonical_vf_info(source)
        py_dir = folder/'python'
        result = CoreVfCostModel(base_dir=ROOT, out_dir=py_dir).run_canonical_vf_info(vf)
        row = {'name': case['name'], 'python_cycles': result['vf_end_cycle']}
        if row['python_cycles'] != case['global_cycles']:
            raise RuntimeError(f'{case["name"]}: global timing differs from recorded DV100 result')
        if args.native_runner:
            trace = folder/'canonical.json'
            trace.write_text(json.dumps(canonical_vf_info_to_dict(vf), indent=2)+'\n')
            cpp_dir = folder/'native'
            proc = subprocess.run([str(args.native_runner.resolve()), '--trace', str(trace),
                                   '--out-dir', str(cpp_dir)],
                                  capture_output=True, text=True, timeout=60, check=True)
            fields = dict(line.split('=', 1) for line in proc.stdout.splitlines() if '=' in line)
            row['native_cycles'] = int(fields['vfEndCycle'])
            if (row['native_cycles'] != row['python_cycles'] or
                    int(fields['cyclesExecuted']) != result['cycles_executed']):
                raise RuntimeError(f'{case["name"]}: Python/Native cycle mismatch')
            for name in ('start_by_cycle.json', 'done_by_cycle.json', 'membar_history.json'):
                if events(py_dir, name) != events(cpp_dir, name):
                    raise RuntimeError(f'{case["name"]}: Python/Native {name} mismatch')
            row['event_parity'] = True
        report['cases'].append(row)
        print(json.dumps(row), flush=True)
    (args.out_dir/'summary.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
