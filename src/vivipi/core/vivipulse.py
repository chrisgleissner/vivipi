from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import threading
import time
from typing import Callable

from vivipi.core.execution import CheckExecutionResult
from vivipi.core.models import CheckDefinition, CheckType, ProbeSchedulingPolicy, Status
from vivipi.core.scheduler import due_checks, probe_backoff_remaining_s, probe_host_key


TRANSPORT_FAILURE_CLASSES = frozenset({"timeout", "dns", "refused", "network", "reset", "io"})
DEFAULT_EXECUTION_PATH = "vivipi.core.execution.execute_check"
CHECK_ORDER_VALUES = frozenset({"identifier", "network-light-first", "network-heavy-first"})


def _isoformat_utc(value_s: float) -> str:
    return datetime.fromtimestamp(value_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _exception_text(error: BaseException | None) -> str | None:
    if error is None:
        return None
    message = " ".join(str(error).split()).strip()
    return message or type(error).__name__


def _diagnostic_messages(result: CheckExecutionResult) -> tuple[str, ...]:
    return tuple(
        f"{diagnostic.code}:{diagnostic.message}"
        for diagnostic in result.diagnostics
    )


def _source_observation(definition: CheckDefinition, result: CheckExecutionResult):
    for observation in result.observations:
        if observation.identifier == definition.identifier:
            return observation
        if observation.source_identifier == definition.identifier:
            return observation
    if len(result.observations) == 1:
        return result.observations[0]
    return None


def _failure_class_from_detail(status: str, detail: str, exception_detail: str | None) -> str:
    if exception_detail is not None:
        return "unexpected_exception"
    normalized = detail.strip().lower()
    if status == Status.OK.value:
        return "success"
    for category in TRANSPORT_FAILURE_CLASSES:
        if normalized == category or normalized.startswith(f"{category}:"):
            return category
    if normalized == "executor error":
        return "unexpected_exception"
    return "protocol"


def _service_summary(result: CheckExecutionResult) -> str:
    counts = defaultdict(int)
    for observation in result.observations:
        counts[getattr(observation.status, "value", observation.status)] += 1
    return (
        f"service checks={len(result.observations)} "
        f"ok={counts[Status.OK.value]} deg={counts[Status.DEG.value]} "
        f"fail={counts[Status.FAIL.value]} unknown={counts[Status.UNKNOWN.value]}"
    )


def _is_service_payload_result(definition: CheckDefinition, result: CheckExecutionResult) -> bool:
    if not result.replace_source or result.diagnostics:
        return False
    return bool(result.observations) and all(
        observation.source_identifier == definition.identifier
        for observation in result.observations
    )


def _direct_summary(detail: str, diagnostics: tuple[str, ...], exception_detail: str | None) -> str:
    if detail:
        return detail
    if diagnostics:
        return diagnostics[0]
    if exception_detail is not None:
        return exception_detail
    return "no detail"


def definition_to_runtime_item(definition: CheckDefinition) -> dict[str, object]:
    return {
        "id": definition.identifier,
        "name": definition.name,
        "type": definition.check_type.value,
        "target": definition.target,
        "interval_s": definition.interval_s,
        "timeout_s": definition.timeout_s,
        "method": definition.method,
        "username": definition.username,
        "password": definition.password,
        "service_prefix": definition.service_prefix,
    }


def definitions_to_runtime_config(
    definitions: tuple[CheckDefinition, ...],
    profile: ProbeSchedulingPolicy | None = None,
) -> dict[str, object]:
    policy = profile or ProbeSchedulingPolicy()
    return {
        "checks": [definition_to_runtime_item(definition) for definition in definitions],
        "probe_schedule": {
            "allow_concurrent_hosts": policy.allow_concurrent_hosts,
            "allow_concurrent_same_host": policy.allow_concurrent_same_host,
            "same_host_backoff_ms": policy.same_host_backoff_ms,
        },
    }


@dataclass(frozen=True)
class VivipulseProfile:
    allow_concurrent_hosts: bool = False
    allow_concurrent_same_host: bool = False
    same_host_backoff_ms: int = 250
    pass_spacing_s: float = 0.0
    same_host_spacing_ms: int = 0
    check_order: str = "network-light-first"
    interval_scale_by_check_id: dict[str, float] = field(default_factory=dict)
    disabled_check_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self):
        if self.same_host_backoff_ms < 0:
            raise ValueError("same_host_backoff_ms must not be negative")
        if self.pass_spacing_s < 0:
            raise ValueError("pass_spacing_s must not be negative")
        if self.same_host_spacing_ms < 0:
            raise ValueError("same_host_spacing_ms must not be negative")
        if self.check_order not in CHECK_ORDER_VALUES:
            raise ValueError(f"check_order must be one of: {', '.join(sorted(CHECK_ORDER_VALUES))}")
        for identifier, scale in self.interval_scale_by_check_id.items():
            if not str(identifier).strip():
                raise ValueError("interval_scale_by_check_id keys must be non-empty strings")
            if float(scale) < 1.0:
                raise ValueError("interval_scale_by_check_id values must be at least 1.0")

    def probe_policy(self) -> ProbeSchedulingPolicy:
        return ProbeSchedulingPolicy(
            allow_concurrent_hosts=self.allow_concurrent_hosts,
            allow_concurrent_same_host=self.allow_concurrent_same_host,
            same_host_backoff_ms=self.same_host_backoff_ms,
        )


@dataclass(frozen=True)
class FirmwareResearchHints:
    repo_path: str
    recommended_same_host_backoff_ms: int = 250
    recommended_allow_concurrent_same_host: bool = False
    recommended_check_order: str = "network-light-first"
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class TraceEvent:
    wall_time: str
    monotonic_s: float
    sequence: int
    target_sequence: int
    mode: str
    pass_index: int
    check_id: str
    check_name: str
    check_type: str
    target: str
    probe_host_key: str | None
    timeout_s: int
    same_host_backoff_ms: int
    called_function_path: str
    latency_ms: float | None
    observation_status: str
    failure_class: str
    response_summary: str
    raw_detail: str
    diagnostics: tuple[str, ...] = field(default_factory=tuple)
    exception_detail: str | None = None
    sleep_before_ms: int = 0

    @property
    def is_transport_failure(self) -> bool:
        return self.failure_class in TRANSPORT_FAILURE_CLASSES

    def to_dict(self) -> dict[str, object]:
        return {
            "wall_time": self.wall_time,
            "monotonic_s": round(self.monotonic_s, 6),
            "sequence": self.sequence,
            "target_sequence": self.target_sequence,
            "mode": self.mode,
            "pass_index": self.pass_index,
            "check_id": self.check_id,
            "check_name": self.check_name,
            "check_type": self.check_type,
            "target": self.target,
            "probe_host_key": self.probe_host_key,
            "timeout_s": self.timeout_s,
            "same_host_backoff_ms": self.same_host_backoff_ms,
            "called_function_path": self.called_function_path,
            "latency_ms": round(self.latency_ms, 3) if self.latency_ms is not None else None,
            "observation_status": self.observation_status,
            "failure_class": self.failure_class,
            "response_summary": self.response_summary,
            "raw_detail": self.raw_detail,
            "diagnostics": list(self.diagnostics),
            "exception_detail": self.exception_detail,
            "sleep_before_ms": self.sleep_before_ms,
        }


@dataclass(frozen=True)
class FailureBoundary:
    target: str
    probe_host_key: str | None
    last_success: TraceEvent | None
    first_failure: TraceEvent
    preceding_context: tuple[TraceEvent, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RunOutcome:
    mode: str
    profile: VivipulseProfile
    started_at: str
    completed_at: str
    trace_events: tuple[TraceEvent, ...]
    failure_boundaries: tuple[FailureBoundary, ...]
    selected_definition_ids: tuple[str, ...]
    blocked_host_keys: tuple[str, ...]
    aborted: bool = False
    aborted_reason: str | None = None
    recovery_count: int = 0
    total_sleep_s: float = 0.0

    @property
    def transport_failure_count(self) -> int:
        return sum(1 for event in self.trace_events if event.is_transport_failure)

    @property
    def unexpected_exception_count(self) -> int:
        return sum(1 for event in self.trace_events if event.failure_class == "unexpected_exception")

    @property
    def success_count(self) -> int:
        return sum(1 for event in self.trace_events if event.failure_class == "success")


@dataclass(frozen=True)
class SearchExperiment:
    label: str
    profile: VivipulseProfile
    outcome: RunOutcome


@dataclass(frozen=True)
class SearchOutcome:
    baseline: SearchExperiment
    experiments: tuple[SearchExperiment, ...]
    selected: SearchExperiment


@dataclass(frozen=True)
class PlanView:
    selected_definition_ids: tuple[str, ...]
    same_host_groups: tuple[tuple[str | None, tuple[str, ...]], ...]
    pass_order: tuple[str, ...]
    probe_schedule: ProbeSchedulingPolicy


def select_definitions(
    definitions: tuple[CheckDefinition, ...],
    target: str | None = None,
    check_ids: tuple[str, ...] = (),
) -> tuple[CheckDefinition, ...]:
    selected = definitions
    if target:
        selected = tuple(definition for definition in selected if definition.target == target)
    if check_ids:
        wanted = frozenset(check_ids)
        selected = tuple(definition for definition in selected if definition.identifier in wanted)
    return selected


def apply_profile(
    definitions: tuple[CheckDefinition, ...],
    profile: VivipulseProfile,
) -> tuple[CheckDefinition, ...]:
    selected: list[CheckDefinition] = []
    disabled = frozenset(profile.disabled_check_ids)
    for definition in definitions:
        if definition.identifier in disabled:
            continue
        scale = float(profile.interval_scale_by_check_id.get(definition.identifier, 1.0))
        if scale == 1.0:
            selected.append(definition)
            continue
        interval_s = max(1, int(round(definition.interval_s * scale)))
        timeout_limit = max(1, int(interval_s * 0.8))
        timeout_s = min(definition.timeout_s, timeout_limit)
        selected.append(replace(definition, interval_s=interval_s, timeout_s=timeout_s))
    return tuple(selected)


def _network_weight(definition: CheckDefinition, reverse: bool = False) -> tuple[int, str]:
    order = {
        CheckType.PING: 0,
        CheckType.HTTP: 1,
        CheckType.SERVICE: 2,
        CheckType.TELNET: 3,
        CheckType.FTP: 4,
    }
    value = order[definition.check_type]
    if reverse:
        value *= -1
    return (value, definition.identifier)


def ordered_definitions_for_pass(
    definitions: tuple[CheckDefinition, ...],
    profile: VivipulseProfile,
) -> tuple[CheckDefinition, ...]:
    base_order = [scheduled.definition for scheduled in due_checks(definitions, {}, 0.0)]
    if profile.check_order == "identifier":
        return tuple(base_order)

    grouped: dict[str | None, list[CheckDefinition]] = defaultdict(list)
    host_order: list[str | None] = []
    seen_hosts: set[str | None] = set()
    for definition in base_order:
        host_key = probe_host_key(definition)
        if host_key not in seen_hosts:
            host_order.append(host_key)
            seen_hosts.add(host_key)
        grouped[host_key].append(definition)

    ordered: list[CheckDefinition] = []
    reverse = profile.check_order == "network-heavy-first"
    for host_key in host_order:
        group = list(grouped[host_key])
        group.sort(key=lambda definition: _network_weight(definition, reverse=reverse))
        ordered.extend(group)
    return tuple(ordered)


def build_plan_view(
    definitions: tuple[CheckDefinition, ...],
    profile: VivipulseProfile,
) -> PlanView:
    selected = apply_profile(definitions, profile)
    groups: dict[str | None, list[str]] = defaultdict(list)
    group_order: list[str | None] = []
    for definition in selected:
        host_key = probe_host_key(definition)
        if host_key not in groups:
            group_order.append(host_key)
        groups[host_key].append(definition.identifier)
    return PlanView(
        selected_definition_ids=tuple(definition.identifier for definition in selected),
        same_host_groups=tuple((host_key, tuple(groups[host_key])) for host_key in group_order),
        pass_order=tuple(definition.identifier for definition in ordered_definitions_for_pass(selected, profile)),
        probe_schedule=profile.probe_policy(),
    )


class HostProbeRunner:
    def __init__(
        self,
        definitions: tuple[CheckDefinition, ...],
        executor,
        mode: str,
        profile: VivipulseProfile,
        *,
        trace_sink: Callable[[TraceEvent], None] | None = None,
        recovery_callback: Callable[[FailureBoundary], bool] | None = None,
        wall_time_provider: Callable[[], float] | None = None,
        monotonic_time_provider: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
        stop_on_failure: bool = False,
        interactive_recovery: bool = False,
        resume_after_recovery: bool = False,
    ):
        self.definitions = tuple(definitions)
        self.executor = executor
        self.mode = mode
        self.profile = profile
        self.trace_sink = trace_sink
        self.recovery_callback = recovery_callback
        self.wall_time_provider = wall_time_provider or time.time
        self.monotonic_time_provider = monotonic_time_provider or time.perf_counter
        self.sleep = sleep or time.sleep
        self.stop_on_failure = stop_on_failure
        self.interactive_recovery = interactive_recovery
        self.resume_after_recovery = resume_after_recovery
        self.execution_path = (
            f"{self.executor.__module__}.{self.executor.__qualname__} -> {DEFAULT_EXECUTION_PATH}"
        )
        self.state_lock = threading.Lock()

        self.last_started_at: dict[str, float] = {}
        self.last_completed_at_by_host: dict[str, float] = {}
        self.last_success_by_host: dict[str, TraceEvent] = {}
        self.per_target_sequence: dict[str, int] = defaultdict(int)
        self.completed_runs_by_check: dict[str, int] = defaultdict(int)
        self.trace_events: list[TraceEvent] = []
        self.failure_boundaries: list[FailureBoundary] = []
        self.boundary_recorded_for_host: set[str | None] = set()
        self.blocked_host_keys: set[str] = set()
        self.recent_events: deque[TraceEvent] = deque(maxlen=6)
        self.aborted = False
        self.aborted_reason: str | None = None
        self.recovery_count = 0
        self.total_sleep_s = 0.0
        self.sequence_counter = 0
        self.selected_definitions = apply_profile(self.definitions, self.profile)

    def _sleep_seconds(self, delay_s: float):
        if delay_s <= 0:
            return
        with self.state_lock:
            self.total_sleep_s += delay_s
        self.sleep(delay_s)

    def _current_pass_index(self) -> int:
        if not self.selected_definitions:
            return 1
        with self.state_lock:
            minimum_completed = min(
                self.completed_runs_by_check.get(definition.identifier, 0)
                for definition in self.selected_definitions
            )
        return minimum_completed + 1

    def _summarize_result(
        self,
        definition: CheckDefinition,
        result: CheckExecutionResult | None,
        error: BaseException | None,
    ) -> tuple[str, str, str, str, float | None, tuple[str, ...], str | None]:
        diagnostics = () if result is None else _diagnostic_messages(result)
        exception_detail = _exception_text(error)
        if result is None:
            return (
                Status.FAIL.value,
                "unexpected_exception",
                exception_detail or "executor raised",
                "",
                None,
                diagnostics,
                exception_detail,
            )

        if definition.check_type == CheckType.SERVICE and _is_service_payload_result(definition, result):
            summary = _service_summary(result)
            return (
                Status.OK.value,
                "success",
                summary,
                summary,
                None,
                diagnostics,
                exception_detail,
            )

        observation = _source_observation(definition, result)
        status_text = getattr(observation.status, "value", observation.status) if observation is not None else Status.FAIL.value
        detail = observation.details if observation is not None else ""
        latency_ms = observation.latency_ms if observation is not None else None
        response_summary = _direct_summary(detail, diagnostics, exception_detail)
        failure_class = _failure_class_from_detail(status_text, detail, exception_detail)
        return (
            status_text,
            failure_class,
            response_summary,
            detail,
            latency_ms,
            diagnostics,
            exception_detail,
        )

    def _record_boundary(self, event: TraceEvent) -> FailureBoundary | None:
        if not event.is_transport_failure:
            return None
        host_key = event.probe_host_key
        if host_key in self.boundary_recorded_for_host:
            return None
        last_success = self.last_success_by_host.get(host_key or "")
        if last_success is None:
            return None
        boundary = FailureBoundary(
            target=event.target,
            probe_host_key=host_key,
            last_success=last_success,
            first_failure=event,
            preceding_context=tuple(self.recent_events),
        )
        self.failure_boundaries.append(boundary)
        self.boundary_recorded_for_host.add(host_key)
        if host_key:
            self.blocked_host_keys.add(host_key)
        return boundary

    def _handle_recovery(self, boundary: FailureBoundary | None):
        if boundary is None:
            return
        if self.stop_on_failure:
            with self.state_lock:
                self.aborted = True
                self.aborted_reason = (
                    f"stopped after first transport failure for {boundary.probe_host_key or boundary.target}"
                )
        if not self.interactive_recovery or self.recovery_callback is None:
            return
        resume = bool(self.recovery_callback(boundary))
        if resume and self.resume_after_recovery and boundary.probe_host_key:
            with self.state_lock:
                self.blocked_host_keys.discard(boundary.probe_host_key)
                self.boundary_recorded_for_host.discard(boundary.probe_host_key)
                self.recovery_count += 1

    def _host_groups(
        self,
        definitions: tuple[CheckDefinition, ...] | list[CheckDefinition],
    ) -> tuple[tuple[str | None, tuple[CheckDefinition, ...]], ...]:
        groups: dict[str | None, list[CheckDefinition]] = defaultdict(list)
        host_order: list[str | None] = []
        for definition in definitions:
            host_key = probe_host_key(definition)
            if host_key not in groups:
                host_order.append(host_key)
            groups[host_key].append(definition)
        return tuple((host_key, tuple(groups[host_key])) for host_key in host_order)

    def _reserve_execution_metadata(
        self,
        definition: CheckDefinition,
        started_monotonic: float,
    ) -> tuple[int, int]:
        with self.state_lock:
            self.last_started_at[definition.identifier] = started_monotonic
            self.per_target_sequence[definition.target] += 1
            target_sequence = self.per_target_sequence[definition.target]
            self.sequence_counter += 1
            sequence = self.sequence_counter
        return sequence, target_sequence

    def _record_event(
        self,
        definition: CheckDefinition,
        event: TraceEvent,
        completed_monotonic: float,
    ):
        host_key = probe_host_key(definition)
        with self.state_lock:
            if host_key is not None:
                self.last_completed_at_by_host[host_key] = completed_monotonic
            if event.failure_class == "success":
                self.last_success_by_host[host_key or ""] = event
            boundary = self._record_boundary(event)
            self.trace_events.append(event)
            self.recent_events.append(event)
            self.completed_runs_by_check[definition.identifier] += 1
        if self.trace_sink is not None:
            self.trace_sink(event)
        self._handle_recovery(boundary)

    def _is_host_blocked(self, host_key: str | None) -> bool:
        with self.state_lock:
            return bool(self.aborted or (host_key is not None and host_key in self.blocked_host_keys))

    def _run_definition(
        self,
        definition: CheckDefinition,
        pass_index: int,
        *,
        previous_host_key: str | None = None,
    ) -> TraceEvent:
        host_key = probe_host_key(definition)
        if self._is_host_blocked(host_key):
            raise RuntimeError("blocked hosts must be filtered before execution")

        with self.state_lock:
            completed_at_by_host = dict(self.last_completed_at_by_host)
        delay_s = probe_backoff_remaining_s(
            definition,
            completed_at_by_host,
            self.monotonic_time_provider(),
            self.profile.probe_policy(),
        )
        if host_key is not None and host_key == previous_host_key and self.profile.same_host_spacing_ms:
            delay_s = max(delay_s, float(self.profile.same_host_spacing_ms) / 1000.0)
        sleep_before_ms = max(0, int(round(delay_s * 1000.0)))
        self._sleep_seconds(delay_s)

        started_wall = float(self.wall_time_provider())
        started_monotonic = float(self.monotonic_time_provider())
        sequence, target_sequence = self._reserve_execution_metadata(definition, started_monotonic)

        result: CheckExecutionResult | None = None
        error: BaseException | None = None
        try:
            result = self.executor(definition, started_wall)
        except Exception as caught_error:  # pragma: no cover - exercised through tests
            error = caught_error

        completed_monotonic = float(self.monotonic_time_provider())

        status_text, failure_class, response_summary, raw_detail, latency_ms, diagnostics, exception_detail = self._summarize_result(
            definition,
            result,
            error,
        )
        observed_latency_ms = latency_ms
        if observed_latency_ms is None:
            observed_latency_ms = max(0.0, (completed_monotonic - started_monotonic) * 1000.0)

        event = TraceEvent(
            wall_time=_isoformat_utc(started_wall),
            monotonic_s=started_monotonic,
            sequence=sequence,
            target_sequence=target_sequence,
            mode=self.mode,
            pass_index=pass_index,
            check_id=definition.identifier,
            check_name=definition.name,
            check_type=definition.check_type.value,
            target=definition.target,
            probe_host_key=host_key,
            timeout_s=definition.timeout_s,
            same_host_backoff_ms=self.profile.same_host_backoff_ms,
            called_function_path=self.execution_path,
            latency_ms=observed_latency_ms,
            observation_status=status_text,
            failure_class=failure_class,
            response_summary=response_summary,
            raw_detail=raw_detail,
            diagnostics=diagnostics,
            exception_detail=exception_detail,
            sleep_before_ms=sleep_before_ms,
        )
        self._record_event(definition, event, completed_monotonic)
        return event

    def _run_host_group(self, definitions: tuple[CheckDefinition, ...], pass_index: int):
        if self.profile.allow_concurrent_same_host:
            runnable = [
                definition
                for definition in definitions
                if not self._is_host_blocked(probe_host_key(definition))
            ]
            if not runnable:
                return
            threads = [
                threading.Thread(target=self._run_definition, args=(definition, pass_index), daemon=True)
                for definition in runnable
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            return
        previous_host_key = None
        for definition in definitions:
            host_key = probe_host_key(definition)
            if self._is_host_blocked(host_key):
                break
            self._run_definition(definition, pass_index, previous_host_key=previous_host_key)
            previous_host_key = host_key
            with self.state_lock:
                if self.aborted:
                    break

    def _run_parallel_groups(
        self,
        host_groups: tuple[tuple[str | None, tuple[CheckDefinition, ...]], ...],
        pass_index: int,
    ):
        if not self.profile.allow_concurrent_hosts:
            for _, definitions in host_groups:
                if not definitions:
                    continue
                self._run_host_group(definitions, pass_index)
                with self.state_lock:
                    if self.aborted:
                        break
            return
        threads = [
            threading.Thread(target=self._run_host_group, args=(definitions, pass_index), daemon=True)
            for _, definitions in host_groups
            if definitions
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _build_outcome(self, started_at: str) -> RunOutcome:
        with self.state_lock:
            trace_events = tuple(sorted(self.trace_events, key=lambda event: event.sequence))
            failure_boundaries = tuple(self.failure_boundaries)
            blocked_host_keys = tuple(sorted(self.blocked_host_keys))
            aborted = self.aborted
            aborted_reason = self.aborted_reason
            recovery_count = self.recovery_count
            total_sleep_s = self.total_sleep_s
        return RunOutcome(
            mode=self.mode,
            profile=self.profile,
            started_at=started_at,
            completed_at=_isoformat_utc(float(self.wall_time_provider())),
            trace_events=trace_events,
            failure_boundaries=failure_boundaries,
            selected_definition_ids=tuple(definition.identifier for definition in self.selected_definitions),
            blocked_host_keys=blocked_host_keys,
            aborted=aborted,
            aborted_reason=aborted_reason,
            recovery_count=recovery_count,
            total_sleep_s=total_sleep_s,
        )

    def run_passes(self, passes: int) -> RunOutcome:
        started_at = _isoformat_utc(float(self.wall_time_provider()))
        ordered = ordered_definitions_for_pass(self.selected_definitions, self.profile)
        host_groups = self._host_groups(ordered)
        for pass_index in range(1, passes + 1):
            self._run_parallel_groups(host_groups, pass_index)
            with self.state_lock:
                if self.aborted:
                    break
            if pass_index >= passes:
                break
            if self.profile.pass_spacing_s:
                self._sleep_seconds(self.profile.pass_spacing_s)
        return self._build_outcome(started_at)

    def run_duration(self, duration_s: float) -> RunOutcome:
        started_wall = float(self.wall_time_provider())
        started_monotonic = float(self.monotonic_time_provider())
        started_at = _isoformat_utc(started_wall)
        while (self.monotonic_time_provider() - started_monotonic) < duration_s and not self.aborted:
            now_s = float(self.monotonic_time_provider())
            with self.state_lock:
                last_started_at = dict(self.last_started_at)
                blocked_host_keys = set(self.blocked_host_keys)
            scheduled = [
                item.definition
                for item in due_checks(self.selected_definitions, last_started_at, now_s)
                if not (probe_host_key(item.definition) in blocked_host_keys if probe_host_key(item.definition) else False)
            ]
            if not scheduled:
                remaining_s = duration_s - (self.monotonic_time_provider() - started_monotonic)
                self._sleep_seconds(min(0.05, max(0.0, remaining_s)))
                continue
            self._run_parallel_groups(self._host_groups(scheduled), self._current_pass_index())
        return self._build_outcome(started_at)


def _profile_cost(profile: VivipulseProfile, base: VivipulseProfile) -> tuple[float, int, int, int]:
    return (
        abs(profile.pass_spacing_s - base.pass_spacing_s),
        abs(profile.same_host_backoff_ms - base.same_host_backoff_ms),
        abs(profile.same_host_spacing_ms - base.same_host_spacing_ms),
        len(profile.disabled_check_ids),
    )


def _outcome_score(outcome: RunOutcome) -> tuple[int, int, int, int]:
    return (
        outcome.transport_failure_count,
        outcome.unexpected_exception_count,
        1 if outcome.aborted else 0,
        len(outcome.blocked_host_keys),
    )


def generate_candidate_profiles(
    base_profile: VivipulseProfile,
    research: FirmwareResearchHints,
    definitions: tuple[CheckDefinition, ...],
    failure_boundary: FailureBoundary | None = None,
    *,
    max_candidates: int = 6,
) -> tuple[VivipulseProfile, ...]:
    candidates: list[VivipulseProfile] = []
    seen: set[tuple[object, ...]] = set()

    def add_candidate(candidate: VivipulseProfile):
        signature = (
            candidate.allow_concurrent_hosts,
            candidate.allow_concurrent_same_host,
            candidate.same_host_backoff_ms,
            candidate.pass_spacing_s,
            candidate.same_host_spacing_ms,
            candidate.check_order,
            tuple(sorted(candidate.interval_scale_by_check_id.items())),
            candidate.disabled_check_ids,
        )
        if signature in seen or candidate == base_profile:
            return
        seen.add(signature)
        candidates.append(candidate)

    recommended_backoff = max(base_profile.same_host_backoff_ms, research.recommended_same_host_backoff_ms)
    add_candidate(
        replace(
            base_profile,
            allow_concurrent_same_host=research.recommended_allow_concurrent_same_host,
            same_host_backoff_ms=recommended_backoff,
        )
    )
    for backoff_ms in (
        recommended_backoff,
        max(recommended_backoff, base_profile.same_host_backoff_ms + 250),
        max(recommended_backoff, base_profile.same_host_backoff_ms + 500),
        max(recommended_backoff, base_profile.same_host_backoff_ms + 1000),
    ):
        add_candidate(replace(base_profile, same_host_backoff_ms=backoff_ms))

    for pass_spacing_s in (0.25, 0.5, 1.0):
        add_candidate(replace(base_profile, pass_spacing_s=max(base_profile.pass_spacing_s, pass_spacing_s)))

    add_candidate(replace(base_profile, check_order=research.recommended_check_order))

    for same_host_spacing_ms in (250, 500):
        add_candidate(replace(base_profile, same_host_spacing_ms=same_host_spacing_ms))

    problem_check_id = failure_boundary.first_failure.check_id if failure_boundary is not None else None
    if problem_check_id:
        add_candidate(
            replace(
                base_profile,
                interval_scale_by_check_id={**base_profile.interval_scale_by_check_id, problem_check_id: 2.0},
            )
        )
        add_candidate(
            replace(
                base_profile,
                disabled_check_ids=tuple(sorted(set(base_profile.disabled_check_ids) | {problem_check_id})),
            )
        )

    return tuple(candidates[:max_candidates])


def run_search(
    runner_factory: Callable[[VivipulseProfile], HostProbeRunner],
    *,
    base_profile: VivipulseProfile,
    research: FirmwareResearchHints,
    definitions: tuple[CheckDefinition, ...],
    passes: int,
    max_experiments: int,
) -> SearchOutcome:
    baseline_outcome = runner_factory(base_profile).run_passes(passes)
    baseline = SearchExperiment(label="baseline", profile=base_profile, outcome=baseline_outcome)
    experiments: list[SearchExperiment] = []
    selected = baseline

    if baseline_outcome.transport_failure_count == 0 and not baseline_outcome.aborted:
        return SearchOutcome(baseline=baseline, experiments=tuple(experiments), selected=selected)

    failure_boundary = baseline_outcome.failure_boundaries[0] if baseline_outcome.failure_boundaries else None
    for index, candidate in enumerate(
        generate_candidate_profiles(
            base_profile,
            research,
            definitions,
            failure_boundary,
            max_candidates=max_experiments,
        ),
        start=1,
    ):
        outcome = runner_factory(candidate).run_passes(passes)
        experiment = SearchExperiment(label=f"candidate-{index}", profile=candidate, outcome=outcome)
        experiments.append(experiment)
        if (_outcome_score(outcome), _profile_cost(candidate, base_profile)) < (
            _outcome_score(selected.outcome),
            _profile_cost(selected.profile, base_profile),
        ):
            selected = experiment
        if outcome.transport_failure_count == 0 and not outcome.aborted:
            break

    return SearchOutcome(baseline=baseline, experiments=tuple(experiments), selected=selected)
