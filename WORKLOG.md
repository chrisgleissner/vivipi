# Worklog

## 2026-04-05

- Bootstrapped the ViviPi repository from `docs/spec.md`
- Added pure-core application modules for layout, rendering, selection, state transitions, input, and burn-in shift
- Added the default ADB-backed Vivi Service and its contract tests
- Added build/deploy tooling that renders the runtime config and builds a firmware bundle
- Added a top-level `build` Bash script as the standard Linux entrypoint for install, test, coverage, package, deploy, and service tasks
- Added CI, coverage, release automation, and agent-facing repo guidance
