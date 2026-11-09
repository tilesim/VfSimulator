"""Receive timing for one resource inside a physical EXU."""
from dataclasses import dataclass


@dataclass(slots=True)
class SubEXU:
    ready_cycle: int = -10**9

    def start(self, cycle: int, self_ii: int) -> None:
        self.ready_cycle = max(self.ready_cycle, cycle+self_ii)
