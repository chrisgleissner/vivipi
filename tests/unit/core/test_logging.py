import pytest

from vivipi.core.logging import LogLevel, StructuredLogger, _enum_name, bound_text, format_log_line, log_field, parse_log_level
from vivipi.core.ring_buffer import RingBuffer


def test_format_log_line_uses_fixed_prefix_and_bounded_fields():
    line = format_log_line(
        LogLevel.INFO,
        "networking",
        "connected to access point",
        fields=(log_field("ssid", "Office Wifi With A Very Long Name"), log_field("ip", "192.0.2.10")),
        line_limit=64,
    )

    assert line.startswith("[vivipi] [INFO][NETWORKI] connected to access point")
    assert "ssid=Office…" in line
    assert len(line) <= 64


def test_structured_logger_honors_level_gating_and_ring_buffer_capacity():
    logger = StructuredLogger(level="WARN", buffer=RingBuffer(2))

    assert logger.info("CORE", "boot") is None

    logger.warn("CORE", "warn", (log_field("id", "router"),))
    logger.error("CORE", "error", (log_field("id", "router"),))
    logger.error("CORE", "boom", ())

    assert logger.dump() == (
        "[vivipi] [ERROR][CORE] error id=router",
        "[vivipi] [ERROR][CORE] boom",
    )

    logger.set_level(LogLevel.DEBUG)

    assert logger.debug("CORE", "trace") == "[vivipi] [DEBUG][CORE] trace"


def test_logging_helpers_cover_parse_validation_sink_and_clear_paths():
    assert parse_log_level(20) == LogLevel.INFO
    assert parse_log_level("error") == LogLevel.ERROR
    assert bound_text("Alpha Beta", 1) == "A"

    with pytest.raises(ValueError, match="positive"):
        bound_text("Alpha", 0)

    emitted = []
    logger = StructuredLogger(level=LogLevel.DEBUG, buffer=RingBuffer(4), sink=emitted.append)

    line = format_log_line(
        LogLevel.INFO,
        "",
        "",
        fields=("", log_field("detail", "value"), log_field("extra", "ignored")),
        field_limit=1,
    )

    assert line == "[vivipi] [INFO][CORE] event"
    assert logger.emit(LogLevel.INFO, "CORE", "boot") == "[vivipi] [INFO][CORE] boot"
    assert emitted == ["[vivipi] [INFO][CORE] boot"]

    logger.clear()

    assert logger.dump() == ()


def test_format_log_line_uses_enum_name_fallback_when_micropython_exposes_property_descriptor():
    class FakeEnumLike:
        name = "<property>"
        _name_ = "INFO"

        def __str__(self):
            return "ignored"

    assert _enum_name(FakeEnumLike()) == "INFO"
    assert _enum_name(20) == "INFO"
    assert _enum_name(LogLevel.INFO) == "INFO"
    assert format_log_line(LogLevel.INFO, "core", "boot") == "[vivipi] [INFO][CORE] boot"