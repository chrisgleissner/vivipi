from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

from vivipi.core.logging import LogLevel, format_log_line, log_field
from vivipi.core.models import CheckDefinition, CheckType


DEFAULT_SYSLOG_PORT = 514
DEFAULT_RETRY_INTERVAL_S = 5.0


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ValueError("service.syslog.enabled must be a boolean")


def _extract_host_port(value: object) -> tuple[str | None, int | None]:
    if value is None:
        return (None, None)
    text = str(value).strip()
    if not text:
        return (None, None)
    if "://" in text:
        parsed = urlparse(text)
        return (parsed.hostname, parsed.port)
    if text.count(":") == 1 and "]" not in text:
        host, port_text = text.rsplit(":", 1)
        if port_text.isdigit():
            return (host.strip() or None, int(port_text))
    return (text, None)


def _resolve_host_alias(host: str | None, host_aliases: object) -> str | None:
    if host is None:
        return None
    normalized = host.strip()
    if not normalized:
        return None
    if not isinstance(host_aliases, dict):
        return normalized
    alias = host_aliases.get(normalized)
    if alias is None:
        return normalized
    resolved = str(alias).strip()
    return resolved or normalized


def _default_syslog_host(config: dict[str, object], definitions: tuple[CheckDefinition, ...] = ()) -> tuple[str | None, int | None]:
    service = config.get("service") if isinstance(config.get("service"), dict) else {}
    wifi = config.get("wifi") if isinstance(config.get("wifi"), dict) else {}
    host_aliases = wifi.get("host_aliases") if isinstance(wifi, dict) else None

    host, port = _extract_host_port(service.get("base_url") if isinstance(service, dict) else None)
    if host:
        return (_resolve_host_alias(host, host_aliases), port)

    for definition in definitions:
        check_type = getattr(definition, "check_type", None)
        target = getattr(definition, "target", None)
        if check_type not in {CheckType.SERVICE, CheckType.HTTP} or target is None:
            continue
        resolved_host, resolved_port = _extract_host_port(target)
        if resolved_host:
            return (_resolve_host_alias(resolved_host, host_aliases), resolved_port)
    return (None, None)


def resolve_syslog_config(config: dict[str, object], definitions: tuple[CheckDefinition, ...] = ()) -> dict[str, object]:
    service = config.get("service") if isinstance(config.get("service"), dict) else {}
    wifi = config.get("wifi") if isinstance(config.get("wifi"), dict) else {}
    host_aliases = wifi.get("host_aliases") if isinstance(wifi, dict) else None
    raw_syslog = service.get("syslog") if isinstance(service, dict) and isinstance(service.get("syslog"), dict) else {}

    derived_host, derived_port = _default_syslog_host(config, definitions)
    explicit_host, explicit_host_port = _extract_host_port(raw_syslog.get("host") if isinstance(raw_syslog, dict) else None)
    host = _resolve_host_alias(explicit_host, host_aliases) or derived_host
    port = int(raw_syslog.get("port", explicit_host_port or DEFAULT_SYSLOG_PORT))
    enabled_default = host is not None
    enabled = _coerce_bool(raw_syslog.get("enabled") if isinstance(raw_syslog, dict) else None, enabled_default)
    retry_interval_s = float(raw_syslog.get("retry_interval_s", DEFAULT_RETRY_INTERVAL_S)) if isinstance(raw_syslog, dict) else DEFAULT_RETRY_INTERVAL_S
    if port < 1 or port > 65535:
        raise ValueError("service.syslog.port must be between 1 and 65535")
    if retry_interval_s < 0:
        raise ValueError("service.syslog.retry_interval_s must not be negative")
    return {
        "enabled": enabled and host is not None,
        "host": host,
        "port": port,
        "retry_interval_s": retry_interval_s,
    }


class UdpSyslogSink:
    def __init__(
        self,
        host: str,
        port: int = DEFAULT_SYSLOG_PORT,
        retry_interval_s: float = DEFAULT_RETRY_INTERVAL_S,
        socket_module=socket,
        now_provider=None,
    ):
        self.host = str(host).strip()
        self.port = int(port)
        self.retry_interval_s = max(0.0, float(retry_interval_s))
        self.socket_module = socket_module
        self.now_provider = now_provider or time.time
        self._socket = None
        self._address = None
        self._warned = False
        self._next_retry_at_s = 0.0

    def _now_s(self) -> float:
        return float(self.now_provider())

    def _close(self):
        sock = self._socket
        self._socket = None
        self._address = None
        if sock is None:
            return
        try:
            sock.close()
        except Exception:
            return

    def _ensure_socket(self):
        if self._socket is not None and self._address is not None:
            return
        info = self.socket_module.getaddrinfo(self.host, self.port, 0, self.socket_module.SOCK_DGRAM)[0]
        family, _socktype, proto, _canonname, sockaddr = info
        # MicroPython may report a stream socktype even for SOCK_DGRAM lookups.
        sock = self.socket_module.socket(family, self.socket_module.SOCK_DGRAM, proto)
        if hasattr(sock, "setblocking"):
            sock.setblocking(False)
        elif hasattr(sock, "settimeout"):
            sock.settimeout(0)
        self._socket = sock
        self._address = sockaddr

    def _warning_line(self, error: BaseException) -> str:
        return format_log_line(
            LogLevel.WARN,
            "SYSLOG",
            "unavailable",
            (
                log_field("host", self.host),
                log_field("port", self.port),
                log_field("error", str(error) or type(error).__name__),
            ),
        )

    def emit(self, line: str) -> str | None:
        now_s = self._now_s()
        if now_s < self._next_retry_at_s:
            return None
        try:
            self._ensure_socket()
            assert self._socket is not None
            assert self._address is not None
            self._socket.sendto(str(line).encode("utf-8", "replace"), self._address)
            return None
        except Exception as error:
            self._close()
            self._next_retry_at_s = now_s + self.retry_interval_s
            if self._warned:
                return None
            self._warned = True
            return self._warning_line(error)


def build_syslog_sink(config: dict[str, object], definitions: tuple[CheckDefinition, ...] = (), socket_module=socket, now_provider=None):
    settings = resolve_syslog_config(config, definitions)
    if not settings.get("enabled"):
        return None
    host = settings.get("host")
    if not isinstance(host, str) or not host.strip():
        return None
    return UdpSyslogSink(
        host=host,
        port=int(settings["port"]),
        retry_interval_s=float(settings["retry_interval_s"]),
        socket_module=socket_module,
        now_provider=now_provider,
    )