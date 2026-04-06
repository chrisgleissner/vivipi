import pytest

from vivipi.core.ring_buffer import RingBuffer


def test_ring_buffer_wraps_and_returns_items_in_logical_order():
    buffer = RingBuffer(3)

    buffer.append("one")
    buffer.append("two")
    buffer.append("three")
    buffer.append("four")

    assert buffer.is_full is True
    assert buffer.items() == ("two", "three", "four")
    assert buffer.items(limit=2) == ("three", "four")


def test_ring_buffer_clear_and_limit_validation():
    with pytest.raises(ValueError, match="positive"):
        RingBuffer(0)

    buffer = RingBuffer(2)
    buffer.append("one")
    buffer.clear()

    assert len(buffer) == 0
    assert buffer.items() == ()

    with pytest.raises(ValueError, match="negative"):
        buffer.items(limit=-1)