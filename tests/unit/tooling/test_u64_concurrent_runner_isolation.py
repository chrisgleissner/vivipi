from __future__ import annotations

from tests.unit.tooling._script_loader import load_script_module


def load_runtime():
    return load_script_module("u64_connection_runtime")


def load_connection_test():
    return load_script_module("u64_connection_test")


def load_http():
    return load_script_module("u64_http")


def load_telnet():
    return load_script_module("u64_telnet")


def load_ftp():
    return load_script_module("u64_ftp")


def make_settings(runtime):
    return runtime.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)


def test_execution_state_phase_shifts_operation_selection_per_runner():
    runtime = load_runtime()
    module = load_connection_test()
    state = module.ExecutionState(settings=make_settings(runtime), include_runner_context=False, runner_count=3, random_seed=13)

    indices = [state.next_probe_operation_index("ftp", runner_id, runtime.ProbeSurface.READWRITE, 6) for runner_id in (1, 2, 3)]

    assert len(set(indices)) == 3


def test_http_multi_runner_readwrite_uses_runner_local_memory_slots_and_keeps_audio_mixer_writes():
    runtime = load_runtime()
    module = load_http()

    runner_1_operations = module.surface_operations(runtime.ProbeSurface.READWRITE, runner_id=1, concurrent_multi_runner=True)
    runner_2_operations = module.surface_operations(runtime.ProbeSurface.READWRITE, runner_id=2, concurrent_multi_runner=True)

    assert [name for name, _ in runner_1_operations] == [
        "get_version",
        "get_info",
        "get_configs",
        "get_config_audio_mixer",
        "get_vol_ultisid_1",
        "get_drives",
        "get_files_temp",
        "mem_read_zero_page",
        "mem_read_screen_ram",
        "mem_read_io_area",
        "mem_read_debug_register",
        "mem_write_screen_space",
        "mem_write_screen_exclam",
        "set_vol_ultisid_1_0_db",
        "set_vol_ultisid_1_plus_1_db",
    ]
    assert [name for name, _ in runner_2_operations] == [name for name, _ in runner_1_operations]

    captured_addresses: list[str] = []

    def fake_memory_write_verify(settings, address, data_hex):
        del settings, data_hex
        captured_addresses.append(address)
        return f"verified={address}"

    module.memory_write_verify = fake_memory_write_verify

    next(operation for name, operation in runner_1_operations if name == "mem_write_screen_space")(None)
    next(operation for name, operation in runner_2_operations if name == "mem_write_screen_space")(None)

    assert captured_addresses == ["0x0400", "0x0401"]


def test_telnet_multi_runner_readwrite_keeps_shared_audio_mixer_writes():
    runtime = load_runtime()
    module = load_telnet()

    operations = module.surface_operations(runtime.ProbeSurface.READWRITE, concurrent_multi_runner=True)

    assert [name for name, _ in operations] == [
        "telnet_smoke_connect",
        "telnet_open_menu",
        "telnet_open_audio_mixer",
        "telnet_read_vol_ultisid_1",
        "set_vol_ultisid_1_0_db",
        "set_vol_ultisid_1_plus_1_db",
    ]


def test_ftp_multi_runner_readwrite_uses_runner_specific_self_file_prefixes(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()
    settings = make_settings(runtime)
    captured_paths: list[str] = []

    class FakeFTP:
        def storbinary(self, command, payload):
            del payload
            captured_paths.append(command.removeprefix("STOR "))

    fake_ftp = FakeFTP()
    runner_1_operations = module.surface_operations(runtime.ProbeSurface.READWRITE, runner_id=1, concurrent_multi_runner=True)
    runner_2_operations = module.surface_operations(runtime.ProbeSurface.READWRITE, runner_id=2, concurrent_multi_runner=True)

    monkeypatch.setattr(module, "track_self_file", lambda current_settings, path: None)

    for name, operation in (
        next(item for item in runner_1_operations if item[0] == "ftp_upload_tiny_self_file"),
        next(item for item in runner_1_operations if item[0] == "ftp_upload_large_self_file"),
        next(item for item in runner_2_operations if item[0] == "ftp_upload_tiny_self_file"),
        next(item for item in runner_2_operations if item[0] == "ftp_upload_large_self_file"),
    ):
        assert name in {"ftp_upload_tiny_self_file", "ftp_upload_large_self_file"}
        operation(settings, fake_ftp)

    assert captured_paths[0].startswith(f"{module.FTP_TEMP_DIR}/{module.FTP_SELF_FILE_PREFIX}r1_")
    assert captured_paths[1].startswith(f"{module.FTP_TEMP_DIR}/{module.FTP_SELF_FILE_PREFIX}r1_")
    assert captured_paths[2].startswith(f"{module.FTP_TEMP_DIR}/{module.FTP_SELF_FILE_PREFIX}r2_")
    assert captured_paths[3].startswith(f"{module.FTP_TEMP_DIR}/{module.FTP_SELF_FILE_PREFIX}r2_")