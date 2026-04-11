from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from textwrap import dedent

from vivipi.core.config import load_checks_config, parse_probe_schedule_config
from vivipi.core.models import CheckDefinition, ProbeSchedulingPolicy
from vivipi.core.vivipulse import (
    FirmwareResearchHints,
    HostProbeRunner,
    PlanView,
    RunOutcome,
    SearchOutcome,
    VivipulseProfile,
    build_plan_view,
    definitions_to_runtime_config,
    run_search,
    select_definitions,
)
from vivipi.runtime.checks import build_executor, build_runtime_definitions
from vivipi.tooling.build_deploy import (
    _resolve_checks_path,
    load_build_deploy_settings,
    load_runtime_checks,
    render_device_runtime_config,
    resolve_config_path,
)


REUSE_PATHS = (
    "firmware.main.main -> firmware.runtime.run_forever",
    "firmware.runtime.build_runtime_app -> vivipi.runtime.checks.build_runtime_definitions",
    "firmware.runtime.build_runtime_app -> vivipi.runtime.checks.build_executor",
    "vivipi.runtime.checks.build_executor.<locals>.executor -> vivipi.core.execution.execute_check",
    "vivipi.core.scheduler.due_checks",
    "vivipi.core.scheduler.probe_host_key",
    "vivipi.core.scheduler.probe_backoff_remaining_s",
)


@dataclass(frozen=True)
class ResolvedInput:
    definitions: tuple[CheckDefinition, ...]
    profile: VivipulseProfile
    runtime_config: dict[str, object]
    input_kind: str
    input_path: Path
    parser_reuse: tuple[str, ...]


@dataclass(frozen=True)
class FirmwareResearchReport:
    hints: FirmwareResearchHints
    confirmed_facts: tuple[str, ...]
    strong_inferences: tuple[str, ...]
    open_questions: tuple[str, ...]


class JsonlTraceWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open("w", encoding="utf-8")

    def write(self, event):
        self.handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        self.handle.flush()

    def close(self):
        self.handle.flush()
        self.handle.close()


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def parse_duration(value: str) -> float:
    text = value.strip().lower()
    if not text:
        raise ValueError("duration must not be blank")
    units = {"s": 1.0, "m": 60.0, "h": 3600.0}
    suffix = text[-1]
    if suffix in units:
        amount = float(text[:-1])
        return amount * units[suffix]
    return float(text)


def _profile_from_policy(policy: ProbeSchedulingPolicy) -> VivipulseProfile:
    return VivipulseProfile(
        allow_concurrent_same_host=policy.allow_concurrent_same_host,
        same_host_backoff_ms=policy.same_host_backoff_ms,
    )


def _profile_from_runtime_config(runtime_config: dict[str, object]) -> VivipulseProfile:
    policy = parse_probe_schedule_config(runtime_config.get("probe_schedule"))
    return _profile_from_policy(policy)


def _runtime_config_from_runtime_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("runtime config must be a JSON object")
    return raw


def _checks_to_runtime_config(definitions: tuple[CheckDefinition, ...]) -> dict[str, object]:
    return definitions_to_runtime_config(definitions)


def resolve_input(args) -> ResolvedInput:
    explicit_sources = [
        source
        for source in (args.checks_config, args.runtime_config, args.build_config)
        if source is not None
    ]
    if len(explicit_sources) > 1:
        raise ValueError("choose exactly one of --checks-config, --runtime-config, or --build-config")

    root = repository_root()
    if args.runtime_config is not None:
        input_path = Path(args.runtime_config).resolve()
        runtime_config = _runtime_config_from_runtime_json(input_path)
        definitions = build_runtime_definitions(runtime_config)
        parser_reuse = (
            "json.load",
            "vivipi.runtime.checks.build_runtime_definitions",
        )
        input_kind = "runtime-config"
    elif args.checks_config is not None:
        input_path = Path(args.checks_config).resolve()
        parsed_definitions = load_checks_config(input_path)
        runtime_config = _checks_to_runtime_config(parsed_definitions)
        definitions = build_runtime_definitions(runtime_config)
        parser_reuse = (
            "vivipi.core.config.load_checks_config",
            "vivipi.runtime.checks.build_runtime_definitions",
        )
        input_kind = "checks-config"
    else:
        if args.build_config is not None:
            input_path = Path(args.build_config).resolve()
        else:
            input_path = resolve_config_path(root / "config" / "build-deploy.yaml", prefer_local_config=True).resolve()
        settings = load_build_deploy_settings(input_path)
        checks_path = _resolve_checks_path(input_path, settings)
        runtime_checks = load_runtime_checks(checks_path)
        runtime_config = render_device_runtime_config(settings, runtime_checks)
        definitions = build_runtime_definitions(runtime_config)
        parser_reuse = (
            "vivipi.tooling.build_deploy.load_build_deploy_settings",
            "vivipi.tooling.build_deploy.load_runtime_checks",
            "vivipi.tooling.build_deploy.render_device_runtime_config",
            "vivipi.runtime.checks.build_runtime_definitions",
        )
        input_kind = "build-config"

    selected = select_definitions(
        definitions,
        target=args.target,
        check_ids=tuple(args.check_id or ()),
    )
    if not selected:
        raise ValueError("no checks matched the requested --target/--check-id filters")

    profile = _profile_from_runtime_config(runtime_config)
    if args.same_host_backoff_ms is not None:
        profile = VivipulseProfile(
            allow_concurrent_same_host=profile.allow_concurrent_same_host,
            same_host_backoff_ms=int(args.same_host_backoff_ms),
            pass_spacing_s=profile.pass_spacing_s,
            same_host_spacing_ms=profile.same_host_spacing_ms,
            check_order=profile.check_order,
            interval_scale_by_check_id=dict(profile.interval_scale_by_check_id),
            disabled_check_ids=tuple(profile.disabled_check_ids),
        )
    if args.allow_concurrent_same_host:
        profile = VivipulseProfile(
            allow_concurrent_same_host=True,
            same_host_backoff_ms=profile.same_host_backoff_ms,
            pass_spacing_s=profile.pass_spacing_s,
            same_host_spacing_ms=profile.same_host_spacing_ms,
            check_order=profile.check_order,
            interval_scale_by_check_id=dict(profile.interval_scale_by_check_id),
            disabled_check_ids=tuple(profile.disabled_check_ids),
        )

    return ResolvedInput(
        definitions=selected,
        profile=profile,
        runtime_config=runtime_config,
        input_kind=input_kind,
        input_path=input_path,
        parser_reuse=parser_reuse,
    )


def inspect_ultimate_repo(path: Path) -> FirmwareResearchReport:
    repo_path = path.resolve()
    if not repo_path.exists():
        raise FileNotFoundError(f"Ultimate repository not found: {repo_path}")

    ftpd_path = repo_path / "software/network/ftpd.cc"
    telnet_path = repo_path / "software/network/socket_gui.cc"
    httpd_path = repo_path / "software/network/httpd.cc"
    http_server_path = repo_path / "software/httpd/c-version/lib/server.c"
    network_config_path = repo_path / "software/network/network_config.cc"
    makefile_path = repo_path / "target/u64/nios2/ultimate/Makefile"

    confirmed_facts = (
        f"lwIP is a core dependency for this firmware build; `{makefile_path.relative_to(repo_path)}` links `liblwip.a` and includes `ftpd.cc`, `httpd.cc`, `socket_stream.cc`, and `listener_socket.cc`.",
        f"`{ftpd_path.relative_to(repo_path)}` binds FTP on port 21, listens with backlog 2, sets a 100 ms receive timeout per accepted socket, spawns a task per control connection, and allocates passive data ports from 51000 upward.",
        f"`{telnet_path.relative_to(repo_path)}` binds telnet on port 23, listens with backlog 2, sets a 200 ms receive timeout, and spawns a task per accepted socket.",
        f"`{httpd_path.relative_to(repo_path)}` starts the MicroHTTPServer loop, while `{http_server_path.relative_to(repo_path)}` tracks a bounded `MAX_HTTP_CLIENT` connection pool and closes sockets after request completion.",
        f"`{network_config_path.relative_to(repo_path)}` exposes shared network password and service toggles for telnet, FTP, and HTTP.",
    )
    strong_inferences = (
        "FTP is the heaviest direct probe because passive-mode listing opens an extra data socket and an extra server-side task, so it should run later than lightweight probes when the same host is already stressed.",
        "The telnet path uses a minimal VT100-oriented parser with per-character reads and tight receive timeouts, so unsolicited probe bytes are more likely to perturb it than a connect-and-observe probe.",
        "HTTP is likely bounded by a small client pool, so aggressive same-host concurrency or rapid back-to-back retries can starve later requests even when individual requests are short.",
    )
    open_questions = (
        "The exact runtime value of `MAX_HTTP_CLIENT` depends on the bundled MicroHTTPServer headers for the active target and is not surfaced in the ViviPi repo.",
        "The active production target may differ between U64, U2+, and other variants, so listener/task behavior is confirmed from shared sources but exact stack sizes are target-dependent.",
    )

    hints = FirmwareResearchHints(
        repo_path=str(repo_path),
        recommended_same_host_backoff_ms=1000,
        recommended_check_order="network-light-first",
        notes=tuple(confirmed_facts[:3]),
    )
    return FirmwareResearchReport(
        hints=hints,
        confirmed_facts=confirmed_facts,
        strong_inferences=strong_inferences,
        open_questions=open_questions,
    )


def render_firmware_research_summary(report: FirmwareResearchReport | None) -> str:
    if report is None:
        return "Firmware research was not run for this invocation.\n"
    parts = ["Confirmed Facts:"]
    parts.extend(f"- {item}" for item in report.confirmed_facts)
    parts.append("")
    parts.append("Strong Inferences:")
    parts.extend(f"- {item}" for item in report.strong_inferences)
    parts.append("")
    parts.append("Open Questions:")
    parts.extend(f"- {item}" for item in report.open_questions)
    return "\n".join(parts) + "\n"


def render_reuse_map(resolved: ResolvedInput) -> str:
    lines = [
        "Pico production entrypoint:",
        "- firmware/main.py -> firmware.runtime.run_forever()",
        "",
        "Shared production functions reused by vivipulse:",
        *[f"- {path}" for path in REUSE_PATHS],
        *[f"- {path}" for path in resolved.parser_reuse],
        "",
        "Intentionally not reused:",
        "- firmware.runtime.run_forever() as the host execution loop",
        "- vivipi.runtime.RuntimeApp",
        "- display rendering and device backends",
        "- button handling",
        "- Wi-Fi bootstrap and reconnection",
        "",
        "Reason:",
        "- vivipulse reuses the shared lower-level probe execution seam rather than pretending to run the full Pico shell on Linux.",
    ]
    return "\n".join(lines) + "\n"


def render_plan_summary(plan: PlanView, resolved: ResolvedInput) -> str:
    lines = [
        "Mode: plan",
        f"Input: {resolved.input_kind} -> {resolved.input_path}",
        f"Checks: {', '.join(plan.selected_definition_ids) if plan.selected_definition_ids else '(none)'}",
        f"Pass order: {', '.join(plan.pass_order) if plan.pass_order else '(none)'}",
        "Same-host groups:",
    ]
    for host_key, check_ids in plan.same_host_groups:
        label = host_key if host_key is not None else "<none>"
        lines.append(f"- {label}: {', '.join(check_ids)}")
    lines.extend(
        [
            "",
            "Probe schedule:",
            f"- allow_concurrent_same_host={plan.probe_schedule.allow_concurrent_same_host}",
            f"- same_host_backoff_ms={plan.probe_schedule.same_host_backoff_ms}",
        ]
    )
    return "\n".join(lines) + "\n"


def render_run_summary(outcome: RunOutcome) -> str:
    return dedent(
        f"""\
        Mode: {outcome.mode}
        Started: {outcome.started_at}
        Completed: {outcome.completed_at}
        Selected checks: {', '.join(outcome.selected_definition_ids) if outcome.selected_definition_ids else '(none)'}
        Total requests: {len(outcome.trace_events)}
        Successes: {outcome.success_count}
        Transport failures: {outcome.transport_failure_count}
        Unexpected exceptions: {outcome.unexpected_exception_count}
        Recovery count: {outcome.recovery_count}
        Blocked hosts: {', '.join(outcome.blocked_host_keys) if outcome.blocked_host_keys else '(none)'}
        Total sleep seconds: {outcome.total_sleep_s:.3f}
        Aborted: {outcome.aborted}
        Aborted reason: {outcome.aborted_reason or '-'}
        """
    )


def render_failure_boundary_summary(outcome: RunOutcome) -> str:
    if not outcome.failure_boundaries:
        return "No transport failure boundaries were recorded.\n"
    lines = []
    for boundary in outcome.failure_boundaries:
        lines.append(f"Target: {boundary.target}")
        lines.append(f"Same-host key: {boundary.probe_host_key or '<none>'}")
        if boundary.last_success is not None:
            lines.append(
                f"Last success: seq={boundary.last_success.sequence} check={boundary.last_success.check_id} "
                f"class={boundary.last_success.failure_class} summary={boundary.last_success.response_summary}"
            )
        else:
            lines.append("Last success: none")
        lines.append(
            f"First failure: seq={boundary.first_failure.sequence} check={boundary.first_failure.check_id} "
            f"class={boundary.first_failure.failure_class} summary={boundary.first_failure.response_summary}"
        )
        if boundary.preceding_context:
            lines.append("Preceding context:")
            for event in boundary.preceding_context:
                lines.append(
                    f"- seq={event.sequence} check={event.check_id} class={event.failure_class} summary={event.response_summary}"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_search_summary(result: SearchOutcome | None) -> str:
    if result is None:
        return "Search mode was not run for this invocation.\n"
    lines = [
        f"Baseline transport failures: {result.baseline.outcome.transport_failure_count}",
        f"Selected profile: backoff={result.selected.profile.same_host_backoff_ms}ms "
        f"pass_spacing={result.selected.profile.pass_spacing_s:.2f}s "
        f"same_host_spacing={result.selected.profile.same_host_spacing_ms}ms "
        f"order={result.selected.profile.check_order}",
        f"Selected experiment: {result.selected.label}",
        "",
        "Experiments:",
        f"- {result.baseline.label}: transport_failures={result.baseline.outcome.transport_failure_count} blocked_hosts={len(result.baseline.outcome.blocked_host_keys)}",
    ]
    for experiment in result.experiments:
        lines.append(
            f"- {experiment.label}: transport_failures={experiment.outcome.transport_failure_count} "
            f"blocked_hosts={len(experiment.outcome.blocked_host_keys)} "
            f"backoff={experiment.profile.same_host_backoff_ms}ms "
            f"pass_spacing={experiment.profile.pass_spacing_s:.2f}s "
            f"same_host_spacing={experiment.profile.same_host_spacing_ms}ms "
            f"order={experiment.profile.check_order}"
        )
    return "\n".join(lines) + "\n"


def render_soak_summary(outcome: RunOutcome | None, duration_s: float | None) -> str:
    if outcome is None or duration_s is None:
        return "Soak mode was not run for this invocation.\n"
    return dedent(
        f"""\
        Requested duration seconds: {duration_s:.1f}
        Total requests: {len(outcome.trace_events)}
        Transport failures: {outcome.transport_failure_count}
        Blocked hosts: {', '.join(outcome.blocked_host_keys) if outcome.blocked_host_keys else '(none)'}
        """
    )


def _ensure_artifact_dir(root: Path, mode: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = root / f"{timestamp}-{mode}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _write_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _recovery_callback_factory(args, prompt, output_stream):
    def callback(boundary) -> bool:
        output_stream.write("\n")
        output_stream.write(f"Transport failure detected for {boundary.target}\n")
        if boundary.last_success is not None:
            output_stream.write(
                f"Last success: seq={boundary.last_success.sequence} {boundary.last_success.check_id} {boundary.last_success.response_summary}\n"
            )
        output_stream.write(
            f"First failure: seq={boundary.first_failure.sequence} {boundary.first_failure.check_id} {boundary.first_failure.response_summary}\n"
        )
        if boundary.first_failure.failure_class == "refused":
            output_stream.write("Minimum recovery action: restart the affected service or power-cycle the device.\n")
        elif boundary.first_failure.failure_class == "dns":
            output_stream.write("Minimum recovery action: restore name resolution or switch the target to a reachable host/IP.\n")
        else:
            output_stream.write("Minimum recovery action: restore the target device to a responsive network state before resuming.\n")
        output_stream.flush()
        if not args.resume_after_recovery:
            return False
        answer = prompt("Type 'resume' once recovery is complete: ").strip().lower()
        return answer == "resume"

    return callback


def _summary_payload(
    *,
    mode: str,
    artifacts_dir: Path,
    resolved: ResolvedInput,
    plan: PlanView | None = None,
    outcome: RunOutcome | None = None,
    search: SearchOutcome | None = None,
) -> dict[str, object]:
    payload = {
        "mode": mode,
        "artifacts_dir": str(artifacts_dir),
        "input_kind": resolved.input_kind,
        "input_path": str(resolved.input_path),
        "selected_check_ids": [definition.identifier for definition in resolved.definitions],
    }
    if plan is not None:
        payload["plan"] = {
            "selected_definition_ids": list(plan.selected_definition_ids),
            "same_host_groups": [[host_key, list(check_ids)] for host_key, check_ids in plan.same_host_groups],
            "pass_order": list(plan.pass_order),
            "probe_schedule": {
                "allow_concurrent_same_host": plan.probe_schedule.allow_concurrent_same_host,
                "same_host_backoff_ms": plan.probe_schedule.same_host_backoff_ms,
            },
        }
    if outcome is not None:
        payload["outcome"] = {
            "started_at": outcome.started_at,
            "completed_at": outcome.completed_at,
            "request_count": len(outcome.trace_events),
            "transport_failures": outcome.transport_failure_count,
            "unexpected_exceptions": outcome.unexpected_exception_count,
            "blocked_host_keys": list(outcome.blocked_host_keys),
            "aborted": outcome.aborted,
            "aborted_reason": outcome.aborted_reason,
        }
    if search is not None:
        payload["search"] = {
            "selected_label": search.selected.label,
            "selected_profile": asdict(search.selected.profile),
            "baseline_transport_failures": search.baseline.outcome.transport_failure_count,
        }
    return payload


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ViviPi shared probes from a Linux host")
    parser.add_argument("--checks-config")
    parser.add_argument("--runtime-config")
    parser.add_argument("--build-config")
    parser.add_argument("--mode", choices=("plan", "local", "reproduce", "search", "soak"), required=True)
    parser.add_argument("--duration")
    parser.add_argument("--passes", type=int)
    parser.add_argument("--same-host-backoff-ms", type=int)
    parser.add_argument("--allow-concurrent-same-host", action="store_true")
    parser.add_argument("--target")
    parser.add_argument("--check-id", action="append")
    parser.add_argument("--artifacts-dir", default=str(repository_root() / "artifacts" / "vivipulse"))
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--interactive-recovery", action="store_true")
    parser.add_argument("--resume-after-recovery", action="store_true")
    parser.add_argument("--max-experiments", type=int, default=4)
    parser.add_argument("--ultimate-repo")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None, *, prompt=input, output_stream=None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    output_stream = output_stream or sys.stdout

    resolved = resolve_input(args)
    artifacts_root = Path(args.artifacts_dir).resolve()
    run_dir = _ensure_artifact_dir(artifacts_root, args.mode)
    trace_writer = JsonlTraceWriter(run_dir / "trace.jsonl")

    plan = build_plan_view(resolved.definitions, resolved.profile)
    executor = build_executor()

    research_report = None
    if args.ultimate_repo is not None:
        research_report = inspect_ultimate_repo(Path(args.ultimate_repo))
    elif args.mode == "search":
        default_repo = repository_root().parent / "1541ultimate"
        if not default_repo.exists():
            raise FileNotFoundError(
                "search mode requires the Ultimate firmware checkout; pass --ultimate-repo PATH"
            )
        research_report = inspect_ultimate_repo(default_repo)

    recovery_callback = _recovery_callback_factory(args, prompt, output_stream)

    outcome = None
    search_result = None
    duration_s = parse_duration(args.duration) if args.duration is not None else None
    passes = args.passes or (1 if args.mode != "soak" else None)

    def runner_factory(profile: VivipulseProfile) -> HostProbeRunner:
        return HostProbeRunner(
            resolved.definitions,
            executor,
            args.mode,
            profile,
            trace_sink=trace_writer.write,
            recovery_callback=recovery_callback,
            stop_on_failure=args.stop_on_failure,
            interactive_recovery=args.interactive_recovery,
            resume_after_recovery=args.resume_after_recovery,
        )

    if args.mode == "plan":
        pass
    elif args.mode == "local":
        runner = runner_factory(resolved.profile)
        outcome = runner.run_passes(1)
    elif args.mode == "reproduce":
        runner = runner_factory(resolved.profile)
        if duration_s is not None:
            outcome = runner.run_duration(duration_s)
        else:
            outcome = runner.run_passes(passes or 1)
    elif args.mode == "search":
        if research_report is None:
            raise FileNotFoundError("search mode requires firmware research; pass --ultimate-repo PATH")
        search_result = run_search(
            runner_factory,
            base_profile=resolved.profile,
            research=research_report.hints,
            definitions=resolved.definitions,
            passes=passes or 1,
            max_experiments=args.max_experiments,
        )
        outcome = search_result.selected.outcome
    elif args.mode == "soak":
        if duration_s is None:
            raise ValueError("soak mode requires --duration")
        outcome = runner_factory(resolved.profile).run_duration(duration_s)
    else:  # pragma: no cover - argparse guards this
        raise ValueError(f"unsupported mode: {args.mode}")

    _write_text(run_dir / "reuse-map.txt", render_reuse_map(resolved))
    _write_text(run_dir / "firmware-research.txt", render_firmware_research_summary(research_report))
    _write_text(run_dir / "failure-boundary.txt", render_failure_boundary_summary(outcome) if outcome else "No run executed.\n")
    _write_text(run_dir / "search-summary.txt", render_search_summary(search_result))
    _write_text(run_dir / "soak-summary.txt", render_soak_summary(outcome if args.mode == "soak" else None, duration_s if args.mode == "soak" else None))

    if args.mode == "plan":
        summary_text = render_plan_summary(plan, resolved)
    else:
        summary_text = render_run_summary(outcome)
    _write_text(run_dir / "run-summary.txt", summary_text)
    trace_writer.close()

    payload = _summary_payload(
        mode=args.mode,
        artifacts_dir=run_dir,
        resolved=resolved,
        plan=plan if args.mode == "plan" else None,
        outcome=outcome,
        search=search_result,
    )
    if args.json:
        output_stream.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    else:
        output_stream.write(summary_text)
        output_stream.write(f"Artifacts: {run_dir}\n")
        if args.debug:
            output_stream.write(render_reuse_map(resolved))
            output_stream.write(render_firmware_research_summary(research_report))
    output_stream.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
