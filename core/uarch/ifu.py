from collections import deque

class IFU:
    """Consume a prepared Canonical stream; no parsing, naming inference, or expansion."""

    def __init__(self, instructions):
        self.stream = deque(instructions)

    def done(self):
        return not self.stream

    def next_inst(self):
        return self.stream.popleft() if self.stream else None

    def take(self, count: int):
        return [self.next_inst() for _ in range(min(count, len(self.stream)))]
