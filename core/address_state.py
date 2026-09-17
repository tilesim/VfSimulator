"""IDU pointer-update forwarding; independent of LSU execution and vector RAT."""


class AddressStateTracker:
    def __init__(self, update_latency: int):
        if type(update_latency) is not int or not 0 < update_latency < 2**63:
            raise ValueError("idu_post_update_ready_latency must be a positive int64")
        self.update_latency = update_latency
        self.updates: dict[str, dict] = {}

    def dependencies(self, accesses) -> list[dict]:
        states = {a["address_state_id"] for a in accesses
                  if a.get("address_state_id") is not None}
        return [dict(self.updates[s]) for s in sorted(states) if s in self.updates]

    def can_dispatch(self, accesses, cycle: int) -> bool:
        return all(cycle >= d["ready_cycle"] for d in self.dependencies(accesses))

    def notify_dispatch(self, inst_id: int, accesses, cycle: int) -> None:
        states = {a["address_state_id"] for a in accesses
                  if a.get("address_state_id") is not None
                  and a.get("update_mode") == "post_update"}
        if states and cycle > 2**63 - 1 - self.update_latency:
            raise ValueError("Address ready_cycle exceeds int64")
        for state in states:
            self.updates[state] = {
                "address_state_id": state,
                "producer_inst_id": inst_id,
                "producer_dispatch_cycle": cycle,
                "ready_cycle": cycle + self.update_latency,
            }
