# ViviPi Productionization Audit

Date: 2026-04-06
Scope: repository-local productionization convergence across product claims, config and runtime behavior, build and release workflows, service path correctness, test coverage, and operator usability.

## Method

The audit started from the documented contract in `README.md`, `docs/spec.md`, and `docs/spec-traceability.md`, then reconciled those claims against the executable entrypoints (`build`, `.github/workflows/*`, `src/vivipi/tooling/build_deploy.py`), runtime and service code (`src/vivipi/core/*`, `src/vivipi/runtime/*`, `src/vivipi/services/*`, `firmware/*`), and the existing regression suites.

Every fixed finding below includes code changes, regression coverage, and executable validation. Findings were prioritized in this order: correctness and false claims, build and release reliability, configuration hazards, then narrower validation and usability issues.

## Findings Fixed In This Pass

### PA-001: Release asset set used inconsistent version sources

- Severity: H2 high
- Symptom: `./build release-assets` generated release files named `0.2.2` while the embedded service wheel was `vivipi-0.2.3.dev0+g029105f9a.d20260406-py3-none-any.whl` on the same checkout.
- Root cause: `stage_release_assets()` named the release set from the repository git-derived version while `build_service_bundle()` embedded the version produced by the wheel build. Those sources diverged on non-release development checkouts.
- Fix implemented: `src/vivipi/tooling/build_deploy.py` now inspects the built wheel version, normalizes prerelease tag formats for comparison, and selects a single authoritative version for the entire release set: the repository tag form when it semantically matches the wheel, otherwise the built wheel version.
- Tests added or updated: `tests/unit/tooling/test_build_deploy.py::test_stage_release_assets_falls_back_to_the_built_wheel_version_when_repo_version_diverges`
- Validation run:
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build release-assets --config config/build-deploy.yaml`
  - `unzip -l artifacts/release/vivipi-service-bundle-0.2.3.dev0+g029105f9a.d20260406.zip | sed -n '1,20p'`
- Final status: fixed

### PA-002: Tracked default configs shipped an invalid `standard + multi-column` overview contract

- Severity: H2 high
- Symptom: tracked configs used `device.display.mode: standard` together with `device.display.columns: 2`, while the renderer only has a true legacy standard layout for one column and otherwise falls through to packed compact-style cells.
- Root cause: there was no validation coupling between display mode and overview columns, and the tracked config/example drifted away from the documented one-row-per-check standard layout.
- Fix implemented:
  - `src/vivipi/core/display.py` rejects `standard` mode with more than one column.
  - `src/vivipi/core/models.py` enforces the same rule for `AppState` construction.
  - `config/build-deploy.yaml` and `config/build-deploy.local.example.yaml` now ship the default OLED config as single-column standard mode again.
  - `README.md` and `docs/spec.md` now state explicitly that multi-column packing is compact-only.
  - `docs/spec-traceability.md` was updated to point at the new validation coverage.
- Tests added or updated:
  - `tests/unit/core/test_display_config.py::test_normalize_display_config_rejects_standard_multi_column_layouts`
  - `tests/unit/core/test_models.py::test_app_state_validates_overview_columns_separator_and_width`
  - `tests/unit/core/test_state.py::test_visible_checks_use_page_capacity_when_multiple_columns_are_enabled`
  - `tests/unit/core/test_render.py::test_compact_multi_column_layout_uses_exact_column_math_and_no_overflow`
  - `tests/unit/tooling/test_build_deploy.py::test_load_build_deploy_settings_rejects_standard_multi_column_overview`
- Validation run:
  - `.venv/bin/python -m pytest --no-cov tests/unit/core/test_display_config.py tests/unit/core/test_models.py tests/unit/core/test_state.py tests/unit/core/test_render.py tests/unit/tooling/test_build_deploy.py -q`
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build build-firmware --config config/build-deploy.yaml`
- Final status: fixed

### PA-003: Explicit `--config` could not bypass the automatic local override

- Severity: H3 medium
- Symptom: `./build build-firmware --config config/build-deploy.yaml` still preferred a sibling `config/build-deploy.local.yaml` when one existed, leaving no supported way to force the tracked config through the canonical build entrypoint.
- Root cause: the shell entrypoint always auto-preferred the sibling `.local` file and also skipped runtime-env validation whenever such a file existed, even when the operator had explicitly selected a different config path.
- Fix implemented:
  - `build` now tracks whether `--config` was provided explicitly.
  - automatic local-config preference only applies when the default config path is in use.
  - runtime-env skipping only happens when the local override will actually be used.
  - `README.md` now documents the explicit bypass path.
- Tests added or updated: `tests/unit/tooling/test_build_entrypoint.py::test_explicit_config_bypasses_automatic_local_override`
- Validation run:
  - `.venv/bin/python -m pytest --no-cov tests/unit/tooling/test_build_entrypoint.py -q`
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build build-firmware --config config/build-deploy.yaml`
- Final status: fixed

### PA-004: Service schema accepted impossible negative latency values

- Severity: H3 medium
- Symptom: `/checks` payload validation accepted negative `latency_ms` values even though they are nonsensical and would pollute diagnostics.
- Root cause: `parse_service_payload()` validated only the type of `latency_ms`, not its bounds.
- Fix implemented: `src/vivipi/services/schema.py` now rejects negative `latency_ms` values.
- Tests added or updated: `tests/contract/test_service_schema.py::test_parse_service_payload_rejects_invalid_field_types`
- Validation run:
  - `.venv/bin/python -m pytest --no-cov tests/contract/test_service_schema.py -q`
- Final status: fixed

## Findings Already Correct

- The repository enforces the documented `>= 96%` coverage gate in `pyproject.toml`, and `tests/spec/test_traceability.py` explicitly checks that the gate remains aligned.
- The release workflow already fetched full git history (`fetch-depth: 0`) and published the explicit versioned assets documented in `README.md`.
- The default SERVICE-omission behavior when `VIVIPI_SERVICE_BASE_URL` is absent was already documented in `README.md` and covered in `tests/unit/tooling/test_build_deploy.py`.

## Considered And Rejected

### PA-NAB-001: `config/build-deploy.local.yaml` appears to be a tracked default

- Rationale: not a repository defect.
- Evidence: `.gitignore` excludes `config/build-deploy.local.yaml`, and `git ls-files` shows only `config/build-deploy.yaml` and `config/build-deploy.local.example.yaml` are tracked.
- Status: NOT-A-BUG

### PA-NAB-002: SERVICE checks being dropped when `VIVIPI_SERVICE_BASE_URL` is unset are a silent correctness defect

- Rationale: considered and rejected because this behavior is explicitly documented, intentionally optional, and already covered by tests. The build only includes SERVICE checks when a device-reachable base URL is configured.
- Evidence: `README.md`, `config/checks.yaml`, and `tests/unit/tooling/test_build_deploy.py::test_write_runtime_config_excludes_service_checks_without_service_url`
- Status: NOT-A-BUG

## Deferred Or External Items

### PA-EXT-001: Real hardware deploy verification

- Rationale: external hardware access limit, not an in-repo correctness defect.
- Evidence: `./build deploy` requires an accessible Pico serial device and `mpremote`; this environment does not expose a connected board.
- Status: DEFERRED-OUT-OF-SCOPE

## Validation Summary

- Focused regressions:
  - `.venv/bin/python -m pytest --no-cov tests/unit/tooling/test_build_deploy.py tests/unit/tooling/test_build_entrypoint.py tests/unit/core/test_display_config.py tests/unit/core/test_models.py tests/unit/core/test_state.py tests/unit/core/test_render.py tests/contract/test_service_schema.py -q`
  - Result: `113 passed`
- Repository-wide validation:
  - `./build coverage`
  - Result: `279 passed`, `96.24%` total coverage
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build ci --config config/build-deploy.yaml`
  - Result: lint passed, `279 passed`, `96.24%` total coverage
- Packaging and firmware workflows:
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build build-firmware --config config/build-deploy.yaml`
  - `VIVIPI_WIFI_SSID=ci-ssid VIVIPI_WIFI_PASSWORD=ci-password VIVIPI_SERVICE_BASE_URL=http://192.0.2.10:8080/checks ./build release-assets --config config/build-deploy.yaml`
  - Verified the release bundle names and embedded service wheel version were consistent.

## Residual Risk

- External only: live `./build deploy` was not exercised against physical hardware because this environment has no accessible Pico serial device.
