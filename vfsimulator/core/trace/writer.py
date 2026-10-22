import json
from dataclasses import dataclass
from pathlib import Path
from vfsimulator.core.trace.perfetto import dump_perfetto_trace

@dataclass(frozen=True)
class SimulationResult:
    cycles_executed: int
    vf_end_cycle: int
    results_dir: str | None = None
    trace_path: str | None = None

    def to_dict(self):
        return dict(
            cycles_executed=self.cycles_executed,
            vf_end_cycle=self.vf_end_cycle,
            results_dir=self.results_dir,
            trace_path=self.trace_path
        )

def dump_model_warnings(results_dir, *, instruction_warnings=None):
    warnings = list(instruction_warnings or ())
    if not warnings:
        return False
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    (root / 'model_warnings.json').write_text(json.dumps(
        {'has_warning': True, 'instruction_fallback_warnings': warnings},
        indent=2,
        ensure_ascii=False
    ))
    return True

def _write_rows(path, rows):
    with path.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

def dump_logs(engine, results_dir):
    root = Path(results_dir)
    root.mkdir(parents=True, exist_ok=True)
    engine.trace.dump_history(str(root / 'sim_history.json'))
    engine.trace.dump_simple_logs(str(root / 'start_by_cycle.json'), str(root / 'done_by_cycle.json'))
    if hasattr(engine.control, 'history'):
        (root / 'membar_history.json').write_text(json.dumps(engine.control.history, indent=2))
    path = root / 'trace.json'
    dump_perfetto_trace(
        path,
        engine.trace.cyc_start_log,
        engine.trace.cyc_done_log,
        issue_ports=engine.exu.issue_ports
    )
    _write_rows(root / 'idu_to_ooo.json', engine.idu.dispatch_log)
    _write_rows(root / 'idu_address_blocked.json', engine.idu.address_block_log)
    _write_rows(root / 'idu_resource_blocked.json', engine.idu.resource_block_log)
    loops = sorted(
        engine.loops.vloop_trace,
        key=lambda row: (row['top_block_id'], row['start_cycle'], row['loop_id'], tuple(row['iter']))
    )
    _write_rows(root / 'vloop_trace.json', loops)
    dump_model_warnings(root, instruction_warnings=engine.db.get_warnings())
    return str(path)
