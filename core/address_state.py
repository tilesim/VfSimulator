"""Ordered pointer-version binding, independent of UB overlap and vector RAT."""
from dataclasses import dataclass, field


@dataclass
class AddressEvent:
    inst_id: int
    start_cycle: int | None = None


@dataclass
class AddressBinding:
    state_id: str
    event: AddressEvent
    dependencies: list[tuple[AddressEvent, int]] = field(default_factory=list)

    def can_issue(self, cycle: int) -> bool:
        return all(event.start_cycle is not None and
                   cycle >= event.start_cycle + delay
                   for event, delay in self.dependencies)


class AddressStateTracker:
    def __init__(self, update_latency: int):
        if type(update_latency) is not int or not 0 < update_latency < 2**63:
            raise ValueError("lsu_post_update_ready_latency must be a positive int64")
        self.update_latency = update_latency
        self.updates: dict[str, AddressEvent] = {}
        self.readers: dict[str, list[AddressEvent]] = {}

    def bind(self, inst_id: int, accesses) -> list[AddressBinding]:
        modes = {}
        for access in accesses:
            state = access.get("address_state_id")
            if state is not None:
                modes[state] = modes.get(state, False) or access.get("update_mode") == "post_update"
        bindings = []
        for state, updates in modes.items():
            event = AddressEvent(inst_id)
            binding = AddressBinding(state, event)
            previous = self.updates.get(state)
            if previous is not None:
                binding.dependencies.append((previous, self.update_latency))
            readers = [r for r in self.readers.get(state, []) if r.start_cycle is None]
            if updates:
                binding.dependencies.extend((r, 0) for r in readers)
                self.updates[state] = event
                self.readers[state] = []
            else:
                readers.append(event)
                self.readers[state] = readers
            bindings.append(binding)
        return bindings

    def notify_start(self, bindings: list[AddressBinding], cycle: int) -> None:
        for binding in bindings:
            binding.event.start_cycle = cycle
            self.readers[binding.state_id] = [
                r for r in self.readers.get(binding.state_id, []) if r.start_cycle is None
            ]
