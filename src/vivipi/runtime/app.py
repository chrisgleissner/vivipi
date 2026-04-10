from __future__ import annotations

import time
from dataclasses import dataclass, replace

try:
    import threading
except ImportError:  # pragma: no cover - MicroPython fallback
    threading = None

try:
    import _thread
except ImportError:  # pragma: no cover - CPython fallback
    _thread = None

from vivipi.core.input import Button, InputController
from vivipi.core.models import CheckRuntime
from vivipi.core.render import render_frame
from vivipi.core.scheduler import due_checks, probe_backoff_remaining_s, probe_host_key, render_reason
from vivipi.core.shift import PixelShiftController
from vivipi.core.state import integrate_observations, page_count, record_diagnostic_events, set_page_index
from vivipi.core.logging import LogLevel, StructuredLogger, bound_text, log_field
from vivipi.core.models import (
    AppMode,
    AppState,
    CheckDefinition,
    CheckObservation,
    DiagnosticEvent,
    DisplayMode,
    ProbeSchedulingPolicy,
    Status,
    TransitionThresholds,
)
from vivipi.core.ring_buffer import RingBuffer
from vivipi.core.state import overview_checks
from vivipi.runtime.metrics import MetricsStore, elapsed_ms, start_timer
from vivipi.runtime.state import make_error_record


def _enum_text(value) -> str:
    return str(getattr(value, "value", value))


def _button_text(value) -> str:
    return str(getattr(value, "value", value))


def _sleep_ms(value_ms: int):
    if value_ms <= 0:
        return
    if hasattr(time, "sleep_ms"):
        time.sleep_ms(value_ms)
        return
    time.sleep(value_ms / 1000.0)


def _monotonic_now_s() -> float:
    if hasattr(time, "ticks_ms"):
        return float(time.ticks_ms()) / 1000.0
    return float(time.perf_counter())


def _allocate_lock():
    if threading is not None:
        return threading.Lock()
    if _thread is not None:
        return _thread.allocate_lock()
    return None


def _start_background_thread(target, args) -> bool:
    if threading is not None:
        try:
            worker = threading.Thread(target=target, args=args, daemon=True)
            worker.start()
            return True
        except Exception:
            return False
    if _thread is not None:
        try:
            _thread.start_new_thread(target, args)
            return True
        except Exception:
            return False
    return False


def _lock_context(lock):
    if lock is None:
        return False
    lock.acquire()
    return True


@dataclass(frozen=True)
class ButtonEvent:
    button: object
    held_ms: int


@dataclass(frozen=True)
class PendingCheckRun:
    definition: CheckDefinition
    requested_at_s: float
    manual: bool = False


@dataclass(frozen=True)
class CompletedCheckRun:
    definition: CheckDefinition
    observed_at_s: float
    duration_ms: float
    manual: bool = False
    result: object | None = None
    error: BaseException | None = None


class RuntimeApp:
    def __init__(
        self,
        definitions: tuple[CheckDefinition, ...],
        executor,
        display,
        button_reader=None,
        input_controller: InputController | None = None,
        shift_controller: PixelShiftController | None = None,
        page_interval_s: int = 15,
        page_size: int = 8,
        row_width: int = 16,
        display_mode: DisplayMode = DisplayMode.STANDARD,
        overview_columns: int = 1,
        column_separator: str = " ",
        transition_thresholds: TransitionThresholds | None = None,
        probe_scheduling: ProbeSchedulingPolicy | None = None,
        sleep_ms=_sleep_ms,
        probe_time_provider=_monotonic_now_s,
        version: str = "",
        build_time: str = "",
    ):
        if page_interval_s < 0:
            raise ValueError("page_interval_s must not be negative")
        self.definitions = tuple(definitions)
        self.executor = executor
        self.display = display
        self.button_reader = button_reader
        self.input_controller = input_controller or InputController()
        self.shift_controller = shift_controller or PixelShiftController()
        self.page_interval_s = page_interval_s
        self.transition_thresholds = transition_thresholds or TransitionThresholds()
        self.probe_scheduling = probe_scheduling or ProbeSchedulingPolicy()
        self.sleep_ms = sleep_ms
        self.probe_time_provider = probe_time_provider
        self.last_started_at: dict[str, float] = {}
        self.last_completed_at_by_host: dict[str, float] = {}
        self.background_lock = _allocate_lock()
        self.background_workers_enabled = self.background_lock is not None and (
            threading is not None or _thread is not None
        )
        self.pending_checks_by_worker: dict[str, list[PendingCheckRun]] = {}
        self.active_workers: set[str] = set()
        self.inflight_check_ids: set[str] = set()
        self.completed_checks: list[CompletedCheckRun] = []
        self.state = AppState(
            checks=tuple(
                CheckRuntime(
                    identifier=definition.identifier,
                    name=definition.name,
                )
                for definition in self.definitions
            ),
            page_size=page_size,
            row_width=row_width,
            display_mode=display_mode,
            overview_columns=overview_columns,
            column_separator=column_separator,
            version=version,
            build_time=build_time,
        )
        self.last_rendered_state: AppState | None = None
        self.logger = StructuredLogger()
        self.metrics = MetricsStore(tuple(definition.identifier for definition in self.definitions))
        self.error_records = RingBuffer(capacity=16)
        self.base_log_level = LogLevel.INFO
        self.debug_mode = False
        self.config: dict[str, object] | None = None
        self.now_provider = None
        self.wifi_connector = None
        self.wifi_reconnector = None
        self.network_state_reader = None
        self.memory_snapshot_interval_s = 30.0
        self.last_memory_snapshot_s: float | None = None
        self.network_reconnect_interval_s = 15.0
        self.last_network_reconnect_attempt_s: float | None = None
        self.network_state = {
            "ssid": "",
            "connected": False,
            "active": False,
            "ip_address": None,
            "last_error": "",
            "last_connect_duration_ms": None,
            "reconnect_count": 0,
        }
        self.last_error_by_check = {definition.identifier: None for definition in self.definitions}
        self.registered_results = {
            definition.identifier: {
                "id": definition.identifier,
                "name": definition.name,
                "status": _enum_text(Status.UNKNOWN),
                "details": "pending",
                "latency_ms": None,
                "last_update_s": None,
                "last_error": None,
            }
            for definition in self.definitions
        }
        self._last_result_signatures: dict[str, tuple[str, str]] = {}
        self._check_log_counts = {definition.identifier: 0 for definition in self.definitions}
        self.summary_log_interval = 10
        self.display_failure_count = 0
        self.display_retry_at_s: float | None = None
        self.feedback_message = ""
        self.feedback_until_s: float | None = None
        self.feedback_duration_s = 1.5
        self.last_cycle_ms: float | None = None
        self.last_render_at_s: float | None = None
        self.last_render_reason = "bootstrap"
        self.last_rendered_feedback = ""
        self.last_rendered_debug_mode = False
        self.last_success_at = {definition.identifier: None for definition in self.definitions}
        self.pending_status_updates: dict[str, dict[str, object]] = {}
        self.network_operation_inflight = False
        self.network_operation_result: dict[str, object] | None = None

    def render_once(self, now_s: float) -> str:
        boot_logo_until_s = getattr(self, "boot_logo_until_s", None)
        if self.last_rendered_state is None and boot_logo_until_s is not None and now_s < float(boot_logo_until_s):
            return "boot-logo"
        reason = render_reason(self.last_rendered_state, self.state)
        feedback_text = self._feedback_text(now_s) or ""
        if (
            reason == "none"
            and self.last_rendered_debug_mode == self.debug_mode
            and self.last_rendered_feedback == feedback_text
        ):
            return reason
        if reason == "none":
            reason = "overlay"
        if self.display_retry_at_s is not None and now_s < self.display_retry_at_s:
            return reason
        try:
            frame = self._decorate_frame(render_frame(self.state, now_s=now_s), now_s)
            self.display.draw_frame(frame)
        except Exception as error:
            self._record_display_failure(error, now_s)
        else:
            self._reset_display_failure_state()
            self.last_rendered_state = self.state
            self.last_render_at_s = now_s
            self.last_render_reason = reason
            self.last_rendered_debug_mode = self.debug_mode
            self.last_rendered_feedback = feedback_text
            self._log_display_propagation(now_s, reason)
        return reason

    def configure_observability(
        self,
        *,
        config: dict[str, object] | None = None,
        now_provider=None,
        wifi_connector=None,
        wifi_reconnector=None,
        network_state_reader=None,
        memory_snapshot_interval_s: float = 30.0,
    ):
        self.config = config
        self.now_provider = now_provider
        self.wifi_connector = wifi_connector
        self.wifi_reconnector = wifi_reconnector
        self.network_state_reader = network_state_reader
        self.memory_snapshot_interval_s = max(1.0, float(memory_snapshot_interval_s))

        if isinstance(config, dict):
            wifi = config.get("wifi", {}) if isinstance(config.get("wifi"), dict) else {}
            self.network_state["ssid"] = str(wifi.get("ssid", "")).strip()
            self.network_reconnect_interval_s = max(5.0, float(wifi.get("reconnect_interval_s", 15)))

        self.logger.info(
            "CORE",
            "boot",
            (
                log_field("checks", len(self.definitions)),
                log_field("mode", str(self.state.display_mode)),
            ),
        )

    def current_time_s(self) -> float | None:
        if self.now_provider is None:
            return None
        return float(self.now_provider())

    def set_log_level(self, level: LogLevel | str | int) -> LogLevel:
        self.base_log_level = LogLevel(level) if isinstance(level, int) else LogLevel[str(level).strip().upper()]
        if not self.debug_mode:
            self.logger.set_level(self.base_log_level)
        return self.logger.level

    def set_debug_mode(self, enabled: bool = True) -> bool:
        self.debug_mode = bool(enabled)
        self.logger.set_level(LogLevel.DEBUG if self.debug_mode else self.base_log_level)
        self.logger.info("CORE", "debug-mode", (log_field("enabled", self.debug_mode),))
        return self.debug_mode

    def toggle_debug_mode(self) -> bool:
        return self.set_debug_mode(not self.debug_mode)

    def get_logs(self, limit: int | None = None) -> tuple[str, ...]:
        return self.logger.dump(limit=limit)

    def get_errors(self, limit: int | None = None) -> tuple[dict[str, object], ...]:
        return tuple(self.error_records.items(limit=limit))

    def get_registered_checks(self) -> tuple[dict[str, object], ...]:
        items = []
        for definition in self.definitions:
            current = dict(self.registered_results[definition.identifier])
            current.update(
                {
                    "check_type": _enum_text(definition.check_type),
                    "target": definition.target,
                    "interval_s": definition.interval_s,
                    "timeout_s": definition.timeout_s,
                }
            )
            items.append(current)
        return tuple(items)

    def get_checks_snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "id": check.identifier,
                "name": check.name,
                "status": _enum_text(check.status),
                "details": check.details,
                "latency_ms": check.latency_ms,
                "last_update_s": check.last_update_s,
                "last_success_s": self.last_success_at.get(check.identifier),
                "last_error": self.last_error_by_check.get(check.identifier),
                "source_identifier": check.source_identifier,
            }
            for check in self.state.checks
        )

    def get_failures_snapshot(self) -> tuple[dict[str, object], ...]:
        return tuple(check for check in self.get_checks_snapshot() if check["status"] != "OK")

    def get_metrics_snapshot(self) -> dict[str, object]:
        return self.metrics.snapshot()

    def get_network_state_snapshot(self) -> dict[str, object]:
        return dict(self.network_state)

    def snapshot(self) -> dict[str, object]:
        return {
            "registered_checks": self.get_registered_checks(),
            "checks": self.get_checks_snapshot(),
            "failures": self.get_failures_snapshot(),
            "diagnostics": self.state.diagnostics,
            "metrics": self.get_metrics_snapshot(),
            "network": self.get_network_state_snapshot(),
            "errors": self.get_errors(),
            "logs": self.get_logs(),
            "debug_mode": self.debug_mode,
        }

    def _refresh_network_state(
        self,
        *,
        last_error: str = "",
        connect_duration_ms: float | None = None,
        reconnect: bool = False,
    ):
        snapshot = {}
        if self.network_state_reader is not None:
            snapshot = dict(self.network_state_reader(self.config or {}))

        self.network_state.update(snapshot)
        if last_error:
            self.network_state["last_error"] = last_error
        elif connect_duration_ms is not None:
            self.network_state["last_error"] = ""

        if connect_duration_ms is not None:
            self.network_state["last_connect_duration_ms"] = round(connect_duration_ms, 3)
            self.metrics.record_network(connect_duration_ms)
        if reconnect:
            self.network_state["reconnect_count"] = int(self.network_state.get("reconnect_count", 0)) + 1

        if self.network_state.get("connected"):
            self.logger.info(
                "NET",
                "connected",
                (
                    log_field("ssid", self.network_state.get("ssid", "")),
                    log_field("ip", self.network_state.get("ip_address", "-")),
                ),
            )
        elif connect_duration_ms is not None or last_error:
            self.logger.warn(
                "NET",
                "connect-failed",
                (
                    log_field("ssid", self.network_state.get("ssid", "")),
                    log_field("error", self.network_state.get("last_error", "unknown")),
                ),
            )

    def _record_exception(self, scope: str, exception: BaseException, observed_at_s: float | None = None, identifier: str | None = None):
        record = make_error_record(scope, exception, observed_at_s=observed_at_s, identifier=identifier)
        self.error_records.append(record)
        if identifier is not None:
            self.last_error_by_check[identifier] = record["message"]
        self.logger.error(
            "ERR",
            "exception",
            (
                log_field("scope", scope),
                log_field("id", identifier or "-"),
                log_field("type", record["type"]),
                log_field("msg", record["message"]),
            ),
        )
        return record

    def _record_result(self, definition: CheckDefinition, result, duration_ms: float, manual: bool = False):
        latency_ms = None
        status = "?"
        details = "pending"

        primary = None
        for observation in result.observations:
            if observation.identifier == definition.identifier:
                primary = observation
                break
        if primary is None and result.observations:
            primary = result.observations[0]

        if primary is not None:
            status = _enum_text(primary.status)
            details = primary.details or details
            latency_ms = primary.latency_ms
        elif _enum_text(definition.check_type) == "SERVICE":
            status = "OK"
            details = f"loaded {len(result.observations)} checks"

        self.metrics.record_check(definition.identifier, duration_ms, latency_ms)
        self.registered_results[definition.identifier] = {
            "id": definition.identifier,
            "name": definition.name,
            "status": status,
            "details": details,
            "latency_ms": latency_ms,
            "last_update_s": result.observations[0].observed_at_s if result.observations else None,
            "last_success_s": self.last_success_at.get(definition.identifier),
            "last_error": None if status == "OK" else details,
        }
        if status == "OK" and result.observations and result.observations[0].observed_at_s is not None:
            self.last_success_at[definition.identifier] = result.observations[0].observed_at_s
            self.registered_results[definition.identifier]["last_success_s"] = result.observations[0].observed_at_s
        self.last_error_by_check[definition.identifier] = None if status == "OK" else details

        run_count = self._check_log_counts.get(definition.identifier, 0) + 1
        self._check_log_counts[definition.identifier] = run_count
        signature = (status, details)
        emit_summary = (
            manual
            or status != "OK"
            or self.debug_mode
            or self._last_result_signatures.get(definition.identifier) != signature
            or run_count == 1
            or run_count % self.summary_log_interval == 0
        )
        summary_fields = [
            log_field("id", definition.identifier),
            log_field("status", status),
            log_field("dur_ms", f"{duration_ms:.1f}"),
        ]
        if latency_ms is not None:
            summary_fields.append(log_field("lat_ms", f"{float(latency_ms):.1f}"))
        if emit_summary:
            self.logger.info("CHECK", "run", tuple(summary_fields))

        if status != "OK":
            if status == "DEG":
                level = LogLevel.WARN
            else:
                level = LogLevel.ERROR if status == "FAIL" else LogLevel.WARN
            self.logger.emit(
                level,
                "CHECK",
                "failure",
                (
                    log_field("id", definition.identifier),
                    log_field("status", status),
                    log_field("detail", details),
                    log_field("manual", manual),
                ),
            )
        elif manual or self._last_result_signatures.get(definition.identifier) != signature:
            self.logger.debug(
                "CHECK",
                "steady",
                (
                    log_field("id", definition.identifier),
                    log_field("status", status),
                ),
            )
        self._last_result_signatures[definition.identifier] = signature

    def _capture_memory_snapshot(self, label: str, now_s: float | None = None):
        from vivipi.runtime.debug import capture_memory_snapshot

        snapshot = capture_memory_snapshot(label=label, observed_at_s=now_s)
        self.metrics.record_memory(snapshot)
        self.last_memory_snapshot_s = now_s
        return snapshot

    def _maybe_capture_memory_snapshot(self, now_s: float):
        interval_s = 5.0 if self.debug_mode else self.memory_snapshot_interval_s
        if self.last_memory_snapshot_s is None or (now_s - self.last_memory_snapshot_s) >= interval_s:
            self._capture_memory_snapshot("periodic", now_s=now_s)

    def _start_network_operation(self, reconnect: bool, now_s: float):
        if self.network_operation_inflight:
            return
        connector = self.wifi_reconnector if reconnect else self.wifi_connector
        if connector is None or self.config is None:
            return

        self.network_operation_inflight = True
        self.last_network_reconnect_attempt_s = now_s
        self.logger.info("NET", "connect-start", (log_field("reconnect", reconnect),))

        def worker():
            started_at, timer_kind = start_timer()
            try:
                diagnostics = connector(self.config)
                payload = {
                    "ok": True,
                    "diagnostics": tuple(diagnostics),
                    "duration_ms": elapsed_ms(started_at, timer_kind),
                    "reconnect": reconnect,
                }
            except Exception as error:
                payload = {
                    "ok": False,
                    "error": error,
                    "duration_ms": elapsed_ms(started_at, timer_kind),
                    "reconnect": reconnect,
                }

            if self._background_enabled():
                lock_acquired = _lock_context(self.background_lock)
                try:
                    self.network_operation_result = payload
                    self.network_operation_inflight = False
                finally:
                    if lock_acquired:
                        self.background_lock.release()
                return

            self.network_operation_result = payload
            self.network_operation_inflight = False

        if self._background_enabled() and _start_background_thread(worker, ()):
            return

        worker()

    def _drain_network_operation(self):
        if self._background_enabled():
            lock_acquired = _lock_context(self.background_lock)
            try:
                payload = self.network_operation_result
                self.network_operation_result = None
            finally:
                if lock_acquired:
                    self.background_lock.release()
        else:
            payload = self.network_operation_result
            self.network_operation_result = None

        if payload is None:
            return

        if not payload.get("ok"):
            error = payload.get("error")
            if isinstance(error, BaseException):
                self._record_exception("network", error, observed_at_s=self.current_time_s())
                self._refresh_network_state(
                    last_error=str(error) or type(error).__name__,
                    connect_duration_ms=float(payload.get("duration_ms", 0.0)),
                    reconnect=bool(payload.get("reconnect")),
                )
            return

        diagnostics = tuple(payload.get("diagnostics", ()))
        duration_ms = float(payload.get("duration_ms", 0.0))
        last_error = ""
        if diagnostics:
            last_error = "; ".join(getattr(item, "message", "") for item in diagnostics if getattr(item, "message", ""))
            self.inject_diagnostics(diagnostics, activate=False)
        self._refresh_network_state(
            last_error=last_error,
            connect_duration_ms=duration_ms,
            reconnect=bool(payload.get("reconnect")),
        )
        self._capture_memory_snapshot("reconnect", now_s=self.current_time_s())

    def _maybe_reconnect_network(self, now_s: float):
        if self.config is None or (self.wifi_reconnector is None and self.wifi_connector is None):
            return

        self._drain_network_operation()

        if self.network_state_reader is not None:
            self.network_state.update(dict(self.network_state_reader(self.config or {})))

        if self.network_state.get("connected"):
            return

        if self.network_operation_inflight:
            return

        if self.last_network_reconnect_attempt_s is not None:
            if (now_s - self.last_network_reconnect_attempt_s) < self.network_reconnect_interval_s:
                return

        self._start_network_operation(reconnect=True, now_s=now_s)
        if not self.network_operation_inflight:
            self._drain_network_operation()

    def _assert_debug_invariants(self):
        if not self.debug_mode:
            return
        assert self.state.page_size >= 1
        assert self.state.row_width >= 1
        assert 1 <= self.state.overview_columns <= 4

    def _display_retry_delay_s(self) -> float:
        if self.display_failure_count < 1:
            return 0.0
        return min(30.0, float(2 ** (self.display_failure_count - 1)))

    def _record_display_failure(self, error: BaseException, now_s: float):
        self.display_failure_count += 1
        retry_delay_s = self._display_retry_delay_s()
        self.display_retry_at_s = now_s + retry_delay_s
        self._record_exception("display", error, observed_at_s=now_s)
        self.inject_diagnostics((DiagnosticEvent(code="DISP", message=f"retry {int(retry_delay_s)}s"),), activate=True)
        self.logger.error(
            "DISP",
            "draw-failed",
            (
                log_field("retry_s", f"{retry_delay_s:.0f}"),
                log_field("count", self.display_failure_count),
            ),
        )

    def _reset_display_failure_state(self):
        if self.display_failure_count:
            self.logger.warn("DISP", "recovered", (log_field("count", self.display_failure_count),))
        self.display_failure_count = 0
        self.display_retry_at_s = None

    def inject_diagnostics(self, events: tuple[object, ...], activate: bool = True):
        if events:
            for event in events:
                self.logger.warn(
                    "CORE",
                    "diagnostic",
                    (
                        log_field("code", getattr(event, "code", "DIAG")),
                        log_field("detail", getattr(event, "message", "")),
                    ),
                )
        self.state = record_diagnostic_events(self.state, events, activate=activate)

    def _apply_button_events(self, button_events: tuple[ButtonEvent, ...]):
        now_s = self.current_time_s() or self._probe_now_s()
        for event in button_events:
            if event.button == Button.A:
                enabled = self.toggle_debug_mode()
                self._set_feedback("DBG ON" if enabled else "DBG OFF", now_s)
                self.logger.info(
                    "BTN",
                    "action",
                    (
                        log_field("button", _button_text(event.button)),
                        log_field("action", "debug"),
                        log_field("enabled", enabled),
                    ),
                )
                continue
            if event.button == Button.B:
                self.request_refresh(now_s)
                self._set_feedback("REFRESH", now_s)
                self.logger.info(
                    "BTN",
                    "action",
                    (
                        log_field("button", _button_text(event.button)),
                        log_field("action", "refresh"),
                    ),
                )

    def _worker_key(self, definition: CheckDefinition) -> str:
        host_key = probe_host_key(definition) or definition.identifier
        if self.probe_scheduling.allow_concurrent_same_host:
            return f"{host_key}:{definition.identifier}"
        return host_key

    def _background_enabled(self) -> bool:
        return self.background_workers_enabled

    def _copy_last_started_at(self) -> dict[str, float]:
        if not self._background_enabled():
            return dict(self.last_started_at)
        lock_acquired = _lock_context(self.background_lock)
        try:
            return dict(self.last_started_at)
        finally:
            if lock_acquired:
                self.background_lock.release()

    def _pop_completed_checks(self) -> tuple[CompletedCheckRun, ...]:
        if not self._background_enabled():
            return ()
        lock_acquired = _lock_context(self.background_lock)
        try:
            if not self.completed_checks:
                return ()
            items = tuple(self.completed_checks)
            self.completed_checks.clear()
            return items
        finally:
            if lock_acquired:
                self.background_lock.release()

    def _probe_now_s(self) -> float:
        return float(self.probe_time_provider())

    def _wait_for_probe_slot(self, definition: CheckDefinition):
        if self._background_enabled():
            lock_acquired = _lock_context(self.background_lock)
            try:
                completed_at = dict(self.last_completed_at_by_host)
            finally:
                if lock_acquired:
                    self.background_lock.release()
        else:
            completed_at = self.last_completed_at_by_host

        remaining_s = probe_backoff_remaining_s(definition, completed_at, self._probe_now_s(), self.probe_scheduling)
        if remaining_s <= 0:
            return
        remaining_ms = max(1, int((remaining_s * 1000.0) + 0.999))
        self.sleep_ms(remaining_ms)

    def _mark_probe_complete(self, definition: CheckDefinition):
        host_key = probe_host_key(definition)
        if host_key is None:
            return
        if self._background_enabled():
            lock_acquired = _lock_context(self.background_lock)
            try:
                self.last_completed_at_by_host[host_key] = self._probe_now_s()
            finally:
                if lock_acquired:
                    self.background_lock.release()
            return
        self.last_completed_at_by_host[host_key] = self._probe_now_s()

    def _execute_check_once(self, definition: CheckDefinition, now_s: float, manual: bool = False) -> CompletedCheckRun:
        self._wait_for_probe_slot(definition)
        started_now_s = now_s if manual else (self.current_time_s() or now_s)
        started_at, timer_kind = start_timer()
        if self._background_enabled():
            lock_acquired = _lock_context(self.background_lock)
            try:
                self.last_started_at[definition.identifier] = started_now_s
            finally:
                if lock_acquired:
                    self.background_lock.release()
        else:
            self.last_started_at[definition.identifier] = started_now_s
        try:
            result = self.executor(definition, started_now_s)
        except Exception as error:
            duration_ms = elapsed_ms(started_at, timer_kind)
            self._mark_probe_complete(definition)
            return CompletedCheckRun(
                definition=definition,
                observed_at_s=started_now_s,
                duration_ms=duration_ms,
                manual=manual,
                error=error,
            )

        duration_ms = elapsed_ms(started_at, timer_kind)
        self._mark_probe_complete(definition)
        return CompletedCheckRun(
            definition=definition,
            observed_at_s=started_now_s,
            duration_ms=duration_ms,
            manual=manual,
            result=result,
        )

    def _apply_completed_check(self, completed: CompletedCheckRun):
        definition = completed.definition
        previous_status = self.registered_results.get(definition.identifier, {}).get("status")
        if completed.error is not None:
            self.metrics.record_check(definition.identifier, completed.duration_ms, None)
            self.registered_results[definition.identifier] = {
                "id": definition.identifier,
                "name": definition.name,
                "status": "FAIL",
                "details": "executor exception",
                "latency_ms": None,
                "last_update_s": completed.observed_at_s,
                "last_success_s": self.last_success_at.get(definition.identifier),
                "last_error": str(completed.error) or type(completed.error).__name__,
            }
            self.state = integrate_observations(
                self.state,
                (
                    CheckObservation(
                        identifier=definition.identifier,
                        name=definition.name,
                        status=Status.FAIL,
                        details="executor exception",
                        observed_at_s=completed.observed_at_s,
                        source_identifier=definition.identifier,
                    ),
                ),
                thresholds=self.transition_thresholds,
                replace_source_identifier=definition.identifier if _enum_text(definition.check_type) == "SERVICE" else None,
            )
            self._record_exception("check", completed.error, observed_at_s=completed.observed_at_s, identifier=definition.identifier)
            self._track_status_transition(definition.identifier, previous_status, "FAIL", completed.observed_at_s)
            return None

        result = completed.result
        self._record_result(definition, result, completed.duration_ms, manual=completed.manual)
        self.state = integrate_observations(
            self.state,
            result.observations,
            thresholds=self.transition_thresholds,
            replace_source_identifier=result.source_identifier if result.replace_source else None,
        )
        if result.diagnostics:
            self.inject_diagnostics(result.diagnostics, activate=False)
        self._track_status_transition(
            definition.identifier,
            previous_status,
            self.registered_results.get(definition.identifier, {}).get("status"),
            completed.observed_at_s,
        )
        return result

    def _run_check(self, definition: CheckDefinition, now_s: float, manual: bool = False):
        return self._apply_completed_check(self._execute_check_once(definition, now_s, manual=manual))

    def _background_worker(self, worker_key: str):
        while True:
            lock_acquired = _lock_context(self.background_lock)
            try:
                queue = self.pending_checks_by_worker.get(worker_key)
                if not queue:
                    self.pending_checks_by_worker.pop(worker_key, None)
                    self.active_workers.discard(worker_key)
                    return
                pending = queue.pop(0)
                if not queue:
                    self.pending_checks_by_worker.pop(worker_key, None)
            finally:
                if lock_acquired:
                    self.background_lock.release()

            completed = self._execute_check_once(
                pending.definition,
                pending.requested_at_s,
                manual=pending.manual,
            )

            lock_acquired = _lock_context(self.background_lock)
            try:
                self.completed_checks.append(completed)
                self.inflight_check_ids.discard(pending.definition.identifier)
            finally:
                if lock_acquired:
                    self.background_lock.release()

    def _queue_check(self, definition: CheckDefinition, now_s: float, manual: bool = False):
        if not self._background_enabled():
            self._run_check(definition, now_s, manual=manual)
            return

        worker_key = self._worker_key(definition)
        start_worker = False
        lock_acquired = _lock_context(self.background_lock)
        try:
            if definition.identifier in self.inflight_check_ids:
                return
            self.inflight_check_ids.add(definition.identifier)
            self.pending_checks_by_worker.setdefault(worker_key, []).append(
                PendingCheckRun(definition=definition, requested_at_s=now_s, manual=manual)
            )
            if worker_key not in self.active_workers:
                self.active_workers.add(worker_key)
                start_worker = True
        finally:
            if lock_acquired:
                self.background_lock.release()

        if start_worker and not _start_background_thread(self._background_worker, (worker_key,)):
            lock_acquired = _lock_context(self.background_lock)
            try:
                self.active_workers.discard(worker_key)
                self.inflight_check_ids.discard(definition.identifier)
                queue = self.pending_checks_by_worker.get(worker_key, [])
                self.pending_checks_by_worker[worker_key] = [item for item in queue if item.definition.identifier != definition.identifier]
                if not self.pending_checks_by_worker.get(worker_key):
                    self.pending_checks_by_worker.pop(worker_key, None)
            finally:
                if lock_acquired:
                    self.background_lock.release()
            self._run_check(definition, now_s, manual=manual)

    def _drain_completed_checks(self):
        for completed in self._pop_completed_checks():
            self._apply_completed_check(completed)

    def _run_due_checks(self, now_s: float):
        # Hot path: keep metrics on every execution but only log state transitions.
        for scheduled in due_checks(self.definitions, self._copy_last_started_at(), now_s):
            self._queue_check(scheduled.definition, now_s)

    def prime_due_checks(self, now_s: float):
        self._run_due_checks(now_s)

    def run_all_checks(self, now_s: float | None = None):
        observed_at_s = now_s if now_s is not None else (self.current_time_s() or 0.0)
        for definition in self.definitions:
            self._run_check(definition, observed_at_s, manual=True)
        self._capture_memory_snapshot("manual-run", now_s=observed_at_s)
        return self.get_registered_checks()

    def request_refresh(self, now_s: float | None = None):
        requested_at_s = now_s if now_s is not None else (self.current_time_s() or 0.0)
        for definition in self.definitions:
            self._queue_check(definition, requested_at_s, manual=True)
        return requested_at_s

    def reset_runtime_state(self):
        self.last_started_at.clear()
        self.last_completed_at_by_host.clear()
        if self._background_enabled():
            lock_acquired = _lock_context(self.background_lock)
            try:
                self.pending_checks_by_worker.clear()
                self.active_workers.clear()
                self.inflight_check_ids.clear()
                self.completed_checks.clear()
            finally:
                if lock_acquired:
                    self.background_lock.release()
        self.state = AppState(
            checks=tuple(
                CheckRuntime(
                    identifier=definition.identifier,
                    name=definition.name,
                )
                for definition in self.definitions
            ),
            page_size=self.state.page_size,
            row_width=self.state.row_width,
            display_mode=self.state.display_mode,
            overview_columns=self.state.overview_columns,
            column_separator=self.state.column_separator,
            version=self.state.version,
            build_time=self.state.build_time,
        )
        self.last_rendered_state = None
        self.metrics.reset()
        self.error_records.clear()
        self._last_result_signatures.clear()
        self._check_log_counts = {definition.identifier: 0 for definition in self.definitions}
        self.display_failure_count = 0
        self.display_retry_at_s = None
        self.feedback_message = ""
        self.feedback_until_s = None
        self.last_rendered_feedback = ""
        self.last_rendered_debug_mode = False
        self.pending_status_updates.clear()
        self.network_operation_inflight = False
        self.network_operation_result = None
        for definition in self.definitions:
            self.last_error_by_check[definition.identifier] = None
            self.last_success_at[definition.identifier] = None
            self.registered_results[definition.identifier] = {
                "id": definition.identifier,
                "name": definition.name,
                "status": _enum_text(Status.UNKNOWN),
                "details": "pending",
                "latency_ms": None,
                "last_update_s": None,
                "last_success_s": None,
                "last_error": None,
            }
        self.logger.info("CTRL", "reset", ())
        return self.snapshot()

    def reconnect_network(self, activate_diagnostics: bool = True):
        connector = self.wifi_reconnector or self.wifi_connector
        if connector is None or self.config is None:
            raise RuntimeError("wifi reconnect is not configured")

        started_at, timer_kind = start_timer()
        try:
            diagnostics = connector(self.config)
        except Exception as error:
            self._record_exception("network", error, observed_at_s=self.current_time_s())
            raise

        duration_ms = elapsed_ms(started_at, timer_kind)
        last_error = ""
        if diagnostics:
            last_error = "; ".join(getattr(item, "message", "") for item in diagnostics if getattr(item, "message", ""))
            self.inject_diagnostics(diagnostics, activate=activate_diagnostics)
        self._refresh_network_state(last_error=last_error, connect_duration_ms=duration_ms, reconnect=True)
        self._capture_memory_snapshot("reconnect", now_s=self.current_time_s())
        return self.get_network_state_snapshot()

    def connect_network(self, activate_diagnostics: bool = False):
        if self.wifi_connector is None or self.config is None:
            raise RuntimeError("wifi connect is not configured")

        started_at, timer_kind = start_timer()
        try:
            diagnostics = self.wifi_connector(self.config)
        except Exception as error:
            self._record_exception("network", error, observed_at_s=self.current_time_s())
            raise

        duration_ms = elapsed_ms(started_at, timer_kind)
        last_error = ""
        if diagnostics:
            last_error = "; ".join(getattr(item, "message", "") for item in diagnostics if getattr(item, "message", ""))
            self.inject_diagnostics(diagnostics, activate=activate_diagnostics)
        self._refresh_network_state(last_error=last_error, connect_duration_ms=duration_ms, reconnect=False)
        self._capture_memory_snapshot("connect", now_s=self.current_time_s())
        return self.get_network_state_snapshot()

    def _set_feedback(self, message: str, now_s: float, duration_s: float | None = None):
        self.feedback_message = bound_text(message, self.state.row_width)
        feedback_duration_s = self.feedback_duration_s if duration_s is None else max(0.0, float(duration_s))
        self.feedback_until_s = now_s + feedback_duration_s

    def _feedback_text(self, now_s: float) -> str | None:
        if self.feedback_until_s is None or now_s > self.feedback_until_s or not self.feedback_message:
            return None
        return self.feedback_message

    def _pad_row(self, value: str) -> str:
        text = bound_text(value, self.state.row_width)
        return text + (" " * max(0, self.state.row_width - len(text)))

    def _last_update_age_s(self, now_s: float) -> int | None:
        updates = [check.last_update_s for check in self.state.checks if check.last_update_s is not None]
        if not updates:
            return None
        return max(0, int(now_s - max(updates)))

    def _overlay_rows(self, now_s: float) -> tuple[str, ...]:
        update_age_s = self._last_update_age_s(now_s)
        update_text = "--" if update_age_s is None else str(update_age_s)
        loop_text = "--" if self.last_cycle_ms is None else str(int(round(self.last_cycle_ms)))
        network_text = "UP" if self.network_state.get("connected") else "DN"
        return (
            self._pad_row(f"UPD:{update_text}s LP:{loop_text}"),
            self._pad_row(f"NET:{network_text} RF:{len(self.pending_status_updates)}"),
        )

    def _decorate_frame(self, frame, now_s: float):
        rows = list(frame.rows)
        if self.debug_mode and rows:
            overlay_rows = self._overlay_rows(now_s)
            overlay_count = min(len(rows), len(overlay_rows))
            start_index = len(rows) - overlay_count
            for index in range(overlay_count):
                rows[start_index + index] = overlay_rows[index]
        feedback = self._feedback_text(now_s)
        if feedback and rows:
            rows[-1] = self._pad_row(feedback)
        return replace(frame, rows=tuple(rows))

    def _track_status_transition(self, identifier: str, previous_status: object, current_status: object, observed_at_s: float | None):
        previous = "?" if previous_status is None else str(previous_status)
        current = "?" if current_status is None else str(current_status)
        if previous == current or observed_at_s is None:
            return
        self.pending_status_updates[identifier] = {
            "status": current,
            "observed_at_s": observed_at_s,
        }
        self.logger.warn(
            "STATE",
            "transition",
            (
                log_field("id", identifier),
                log_field("from", previous),
                log_field("to", current),
            ),
        )

    def _log_display_propagation(self, now_s: float, reason: str):
        if reason == "none" or not self.pending_status_updates:
            return
        for identifier, update in tuple(self.pending_status_updates.items()):
            observed_at_s = update.get("observed_at_s")
            if observed_at_s is None:
                self.pending_status_updates.pop(identifier, None)
                continue
            propagation_ms = max(0.0, (now_s - float(observed_at_s)) * 1000.0)
            self.logger.info(
                "DISP",
                "propagation",
                (
                    log_field("id", identifier),
                    log_field("status", update.get("status", "?")),
                    log_field("delay_ms", f"{propagation_ms:.1f}"),
                ),
            )
            self.pending_status_updates.pop(identifier, None)

    def _apply_shift(self, now_s: float):
        offset = self.shift_controller.offset_for_elapsed(now_s)
        if offset != self.state.shift_offset:
            self.state = replace(self.state, shift_offset=offset)

    def _apply_page_rotation(self, now_s: float):
        normalized = set_page_index(self.state, self.state.page_index)
        if normalized != self.state:
            self.state = normalized

        if self.state.mode != AppMode.OVERVIEW:
            return

        total_pages = page_count(overview_checks(self.state), self.state.page_size * self.state.overview_columns)
        if total_pages <= 1 or self.page_interval_s == 0:
            return

        next_page = int(now_s // self.page_interval_s) % total_pages
        if next_page != self.state.page_index:
            self.state = set_page_index(self.state, next_page, select_visible=True)

    def tick(self, now_s: float, button_events: tuple[ButtonEvent, ...] | None = None) -> str:
        cycle_started, cycle_timer_kind = start_timer()
        events = button_events
        if events is None:
            if self.button_reader is None:
                events = ()
            else:
                events = tuple(self.button_reader.poll())

        self._apply_button_events(events)
        self._drain_completed_checks()
        self._maybe_reconnect_network(now_s)
        self._run_due_checks(now_s)
        self._drain_completed_checks()
        self._apply_page_rotation(now_s)
        self._apply_shift(now_s)

        reason = self.render_once(now_s)

        self.last_cycle_ms = elapsed_ms(cycle_started, cycle_timer_kind)
        self.metrics.record_cycle(self.last_cycle_ms)
        self._maybe_capture_memory_snapshot(now_s)
        self._assert_debug_invariants()
        return reason
