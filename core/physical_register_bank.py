from __future__ import annotations

from collections import deque
from typing import Any


class PhysicalRegisterBank:
    """Owned physical IDs and allocation; Core may share bank-qualified lifetime maps."""

    def __init__(self, capacity: int, *, name: str, prefix: str) -> None:
        if type(capacity) is not int or capacity <= 0:
            raise ValueError("Physical register capacity must be a positive integer")
        self.name = name
        self.capacity = capacity
        self.freelist = deque(f"{prefix}{i}" for i in range(capacity))
        self.physical_ids = frozenset(self.freelist)
        self._allocated: set[str] = set()
        self.rat: dict[Any, str] = {}
        self.producer: dict[str, Any] = {}
        self.producer_uop: dict[str, Any] = {}
        self.producer_profile: dict[str, Any] = {}
        self.pending: set[str] = set()
        self.consumer_count: dict[str, int] = {}
        self.generation: dict[str, int] = {}
        self.release_eligible_cycle: dict[str, int] = {}
        self.bypass_producer_done: set[str] = set()

    def allocate(self) -> str:
        if not self.freelist:
            raise RuntimeError(f"Physical register bank {self.name} is exhausted")
        physical_id = self.freelist[0]
        if physical_id not in self.physical_ids or physical_id in self._allocated:
            raise RuntimeError(f"Corrupt freelist in physical register bank {self.name}")
        self.freelist.popleft()
        self._allocated.add(physical_id)
        return physical_id

    def can_free(self, physical_id: str, cycle: int) -> bool:
        if physical_id not in self.physical_ids:
            raise ValueError(
                f"Physical register {physical_id!r} does not belong to bank {self.name}"
            )
        if physical_id not in self._allocated:
            return False
        if physical_id in self.rat.values():
            return False
        if self.consumer_count.get(physical_id, 0) > 0:
            return False
        if physical_id in self.pending:
            return False
        eligible = self.release_eligible_cycle.get(physical_id)
        return eligible is None or cycle >= eligible

    def try_free(self, physical_id: str, cycle: int) -> bool:
        if not self.can_free(physical_id, cycle):
            return False
        self.producer.pop(physical_id, None)
        self.producer_uop.pop(physical_id, None)
        self.producer_profile.pop(physical_id, None)
        self.pending.discard(physical_id)
        self.consumer_count.pop(physical_id, None)
        self.release_eligible_cycle.pop(physical_id, None)
        self.bypass_producer_done.discard(physical_id)
        # Keep generation across reuse so delayed consumers can detect stale refs.
        self._allocated.remove(physical_id)
        self.freelist.append(physical_id)
        return True
