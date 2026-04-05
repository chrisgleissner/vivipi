# Spec Traceability

| Requirement | Test Coverage |
| --- | --- |
| VIVIPI-DISPLAY-001 | tests/unit/core/test_render.py::test_idle_mode_is_centered_and_uses_the_full_grid; tests/unit/core/test_render.py::test_overview_paginates_and_inverts_the_selected_row |
| VIVIPI-UX-GRID-001 | tests/unit/core/test_text.py::test_overview_row_reserves_status_column_and_truncates_name; tests/unit/core/test_render.py::test_overview_paginates_and_inverts_the_selected_row |
| VIVIPI-UX-TYPO-001 | tests/unit/core/test_text.py::test_center_text_returns_fixed_width_idle_row; tests/unit/core/test_render.py::test_idle_mode_is_centered_and_uses_the_full_grid |
| VIVIPI-UX-IDLE-001 | tests/unit/core/test_render.py::test_idle_mode_is_centered_and_uses_the_full_grid |
| VIVIPI-CHECK-001 | tests/unit/core/test_config.py::test_load_checks_config_reads_yaml_definitions |
| VIVIPI-CHECK-002 | tests/unit/core/test_config.py::test_load_checks_config_supports_service_checks |
| VIVIPI-CHECK-SCHEMA-001 | tests/contract/test_service_schema.py::test_parse_service_payload_validates_schema_and_builds_stable_ids; tests/contract/test_service_schema.py::test_parse_service_payload_accepts_unknown_status_display |
| VIVIPI-CHECK-ID-001 | tests/unit/core/test_config.py::test_check_ids_are_stable_for_direct_and_service_checks; tests/contract/test_service_schema.py::test_parse_service_payload_validates_schema_and_builds_stable_ids |
| VIVIPI-UX-STATUS-001 | tests/unit/core/test_render.py::test_overview_displays_unknown_status_as_question_mark; tests/contract/test_service_schema.py::test_parse_service_payload_accepts_unknown_status_display |
| VIVIPI-CHECK-STATE-001 | tests/unit/core/test_state.py::test_failure_hysteresis_moves_from_ok_to_deg_to_fail; tests/unit/core/test_state.py::test_success_recovers_from_fail_and_unknown |
| VIVIPI-CHECK-TIME-001 | tests/unit/core/test_config.py::test_load_checks_config_rejects_timeout_too_close_to_interval |
| VIVIPI-UX-PAGE-001 | tests/unit/core/test_state.py::test_visible_checks_keeps_selected_check_on_current_page; tests/unit/core/test_render.py::test_overview_paginates_and_inverts_the_selected_row |
| VIVIPI-UX-SELECT-001 | tests/unit/core/test_state.py::test_selection_tracks_identity_when_wrapping_sorted_checks; tests/unit/core/test_render.py::test_overview_normalizes_selection_when_checks_exist |
| VIVIPI-INPUT-001 | tests/unit/core/test_input.py::test_button_a_debounces_short_presses; tests/unit/core/test_input.py::test_button_a_auto_repeats_every_500ms; tests/unit/core/test_input.py::test_button_b_toggles_detail_and_back_to_overview |
| VIVIPI-UX-DETAIL-001 | tests/unit/core/test_render.py::test_detail_view_omits_unavailable_lines; tests/unit/core/test_render.py::test_detail_view_truncates_details_before_overflowing |
| VIVIPI-INPUT-DETAIL-001 | tests/unit/core/test_input.py::test_button_a_cycles_checks_in_detail_view; tests/unit/core/test_input.py::test_button_b_toggles_detail_and_back_to_overview |
| VIVIPI-UX-DIAG-001 | tests/unit/core/test_render.py::test_diagnostics_view_truncates_without_wrapping |
| VIVIPI-RENDER-001 | tests/unit/core/test_scheduler.py::test_render_reason_reports_bootstrap_when_no_previous_state_exists; tests/unit/core/test_scheduler.py::test_render_reason_reports_none_for_identical_states |
| VIVIPI-RENDER-SHIFT-001 | tests/unit/core/test_shift.py::test_shift_cycle_advances_in_the_expected_order |
| VIVIPI-ARCH-001 | tests/unit/core/test_input.py::test_button_b_toggles_detail_and_back_to_overview; tests/unit/core/test_scheduler.py::test_render_reason_reports_state_changes; tests/unit/tooling/test_build_deploy.py::test_write_runtime_config_embeds_wifi_and_checks |
| VIVIPI-PERF-001 | tests/unit/core/test_scheduler.py::test_render_reason_reports_none_for_identical_states |
| VIVIPI-DET-001 | tests/unit/core/test_render.py::test_rendering_is_deterministic_for_identical_inputs |
| VIVIPI-TEST-001 | tests/spec/test_traceability.py::test_every_requirement_in_the_spec_has_a_traceability_mapping; tests/spec/test_traceability.py::test_pytest_config_enforces_the_coverage_gate |
| VIVIPI-ANTI-001 | tests/unit/core/test_render.py::test_diagnostics_view_truncates_without_wrapping; tests/unit/core/test_text.py::test_overview_row_reserves_status_column_and_truncates_name |
