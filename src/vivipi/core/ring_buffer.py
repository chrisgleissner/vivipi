from __future__ import annotations


class RingBuffer:
    def __init__(self, capacity: int):
        self.capacity = int(capacity)
        if self.capacity < 1:
            raise ValueError("capacity must be positive")
        self._items: list[object | None] = [None] * self.capacity
        self._count = 0
        self._next_index = 0

    def __len__(self) -> int:
        return self._count

    @property
    def is_full(self) -> bool:
        return self._count == self.capacity

    def append(self, item: object):
        self._items[self._next_index] = item
        self._next_index = (self._next_index + 1) % self.capacity
        if self._count < self.capacity:
            self._count += 1

    def clear(self):
        for index in range(self.capacity):
            self._items[index] = None
        self._count = 0
        self._next_index = 0

    def items(self, limit: int | None = None) -> tuple[object, ...]:
        if limit is not None and limit < 0:
            raise ValueError("limit must not be negative")
        if self._count == 0:
            return ()

        count = self._count
        if limit is not None:
            count = min(count, limit)

        start = (self._next_index - count) % self.capacity
        values: list[object] = []
        for offset in range(count):
            item = self._items[(start + offset) % self.capacity]
            if item is not None:
                values.append(item)
        return tuple(values)