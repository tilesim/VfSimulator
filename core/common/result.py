from dataclasses import dataclass

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
