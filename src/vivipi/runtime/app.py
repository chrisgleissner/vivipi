from __future__ import annotations

from dataclasses import dataclass, replace

from vivipi.core import InputController, PixelShiftController, due_checks, integrate_observations, page_count, record_diagnostic_events, render_frame, render_reason, set_page_index
from vivipi.core.logging import LogLevel, StructuredLogger, log_field
from vivipi.core.models import AppMode, AppState, CheckDefinition, DiagnosticEvent, DisplayMode
from vivipi.core.ring_buffer import RingBuffer
from vivipi.core.state import overview_checks
from vivipi.runtime.metrics import MetricsStore, elapsed_ms, start_timer
from vivipi.runtime.state import make_error_record


@dataclass(frozen=True)
class ButtonEvent:
    button: object
    held_ms: int


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
        self.last_started_at: dict[str, float] = {}
        self.state = AppState(
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
        self.error_records = RingBuffer(16)
        self.base_log_level = LogLevel.INFO
        self.debug_mode = False
        self.config: dict[str, object] | None = None
        self.now_provider = None
        self.wifi_connector = None
        self.wifi_reconnector = None
        self.network_state_reader = None
        self.memory_snapshot_interval_s = 30.0
        self.last_memory_snapshot_s: float | None = None
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
                "status": "?",
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

        self.logger.info(
            "CORE",
            "boot",
            (
                log_field("checks", len(self.definitions)),
                log_field("mode", self.state.display_mode.value),
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
                    "check_type": definition.check_type.value,
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
                "status": check.status.value,
                "details": check.details,
                "latency_ms": check.latency_ms,
                "last_update_s": check.last_update_s,
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
            status = primary.status.value
            details = primary.details or details
            latency_ms = primary.latency_ms
        elif definition.check_type.value == "SERVICE":
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
            "last_error": None if status == "OK" else details,
        }
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
        state = self.state
        for event in button_events:
            state = self.input_controller.apply(state, event.button, held_ms=event.held_ms)
        self.state = state

    def _run_check(self, definition: CheckDefinition, now_s: float, manual: bool = False):
        started_at, timer_kind = start_timer()
        self.last_started_at[definition.identifier] = now_s
        try:
            result = self.executor(definition, now_s)
        except Exception as error:
            duration_ms = elapsed_ms(started_at, timer_kind)
            self.metrics.record_check(definition.identifier, duration_ms, None)
            self.registered_results[definition.identifier] = {
                "id": definition.identifier,
                "name": definition.name,
                "status": "FAIL",
                "details": "executor exception",
                "latency_ms": None,
                "last_update_s": now_s,
                "last_error": str(error) or type(error).__name__,
            }
            self._record_exception("check", error, observed_at_s=now_s, identifier=definition.identifier)
            return None

        duration_ms = elapsed_ms(started_at, timer_kind)
        self._record_result(definition, result, duration_ms, manual=manual)
        self.state = integrate_observations(
            self.state,
            result.observations,
            replace_source_identifier=result.source_identifier if result.replace_source else None,
        )
        if result.diagnostics:
            self.inject_diagnostics(result.diagnostics, activate=True)
        return result

    def _run_due_checks(self, now_s: float):
        # Hot path: keep metrics on every execution but only log state transitions.
        for scheduled in due_checks(self.definitions, self.last_started_at, now_s):
            self._run_check(scheduled.definition, now_s)

    def run_all_checks(self, now_s: float | None = None):
        observed_at_s = now_s if now_s is not None else (self.current_time_s() or 0.0)
        for definition in self.definitions:
            self._run_check(definition, observed_at_s, manual=True)
        self._capture_memory_snapshot("manual-run", now_s=observed_at_s)
        return self.get_registered_checks()

    def reset_runtime_state(self):
        self.last_started_at.clear()
        self.state = AppState(
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
        for definition in self.definitions:
            self.last_error_by_check[definition.identifier] = None
            self.registered_results[definition.identifier] = {
                "id": definition.identifier,
                "name": definition.name,
                "status": "?",
                "details": "pending",
                "latency_ms": None,
                "last_update_s": None,
                "last_error": None,
            }
        self.logger.info("CTRL", "reset", ())
        return self.snapshot()

    def reconnect_network(self):
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
            self.inject_diagnostics(diagnostics, activate=True)
        self._refresh_network_state(last_error=last_error, connect_duration_ms=duration_ms, reconnect=True)
        self._capture_memory_snapshot("reconnect", now_s=self.current_time_s())
        return self.get_network_state_snapshot()

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
        self._run_due_checks(now_s)
        self._apply_page_rotation(now_s)
        self._apply_shift(now_s)

        reason = render_reason(self.last_rendered_state, self.state)
        if reason != "none":
            if self.display_retry_at_s is None or now_s >= self.display_retry_at_s:
                try:
                    self.display.draw_frame(render_frame(self.state, now_s=now_s))
                except Exception as error:
                    self._record_display_failure(error, now_s)
                else:
                    self._reset_display_failure_state()
                    self.last_rendered_state = self.state

        self.metrics.record_cycle(elapsed_ms(cycle_started, cycle_timer_kind))
        self._maybe_capture_memory_snapshot(now_s)
        self._assert_debug_invariants()
        return reason