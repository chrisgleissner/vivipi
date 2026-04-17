from __future__ import annotations

from tests.unit.tooling._script_loader import load_script_module


def load_runtime():
    return load_script_module("u64_connection_runtime")


def load_ftp():
    return load_script_module("u64_ftp")


def load_http():
    return load_script_module("u64_http")


def load_ping():
    return load_script_module("u64_ping")


def load_connection_test():
    return load_script_module("u64_connection_test")


class RotatingState:
    def __init__(self):
        self.counts = {}

    def next_probe_operation_index(self, protocol, runner_id, surface, pool_size):
        key = (runner_id, protocol, surface.value)
        counter = self.counts.get(key, 0)
        self.counts[key] = counter + 1
        return counter % pool_size


def make_settings(runtime):
    return runtime.RuntimeSettings("host", "v1/version", 80, 23, 21, "anonymous", "", 0, 1, True)


def test_http_normal_mode_requests_connection_close_and_reads_body(monkeypatch):
    runtime = load_runtime()
    module = load_http()
    calls = []

    class FakeResponse:
        status = 200

        def read(self):
            calls.append("read")
            return b"ok"

    class FakeConnection:
        def __init__(self, host, port, timeout):
            calls.append(("init", host, port, timeout))

        def request(self, method, path, headers):
            calls.append(("request", method, path, headers))

        def getresponse(self):
            calls.append("getresponse")
            return FakeResponse()

        def close(self):
            calls.append("close")

    monkeypatch.setattr(module.http.client, "HTTPConnection", FakeConnection)

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE)

    assert outcome.result == "OK"
    assert ("request", "GET", "/v1/version", {"Connection": "close"}) in calls
    assert calls[-1] == "close"


def test_http_surface_operations_retry_transient_transport_errors(monkeypatch):
    runtime = load_runtime()
    module = load_http()
    attempts = []
    sleeps = []

    def flaky_operation(settings):
        del settings
        attempts.append("call")
        if len(attempts) < 3:
            raise ConnectionResetError(104, "Connection reset by peer")
        return "http_status=200 body_bytes=43"

    monkeypatch.setattr(
        module,
        "surface_operations",
        lambda surface, *, runner_id=1, concurrent_multi_runner=False, shared_state=None: (("flaky", flaky_operation),),
    )
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    context = runtime.ProbeExecutionContext(
        protocol="http",
        runner_id=1,
        iteration=1,
        surface=runtime.ProbeSurface.READ,
        state=RotatingState(),
    )

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE, context=context)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=read op=flaky http_status=200 body_bytes=43"
    assert attempts == ["call", "call", "call"]
    assert sleeps == [0.1, 0.25]


def test_ping_probe_uses_ping_terminology(monkeypatch):
    runtime = load_runtime()
    module = load_ping()

    class Completed:
        returncode = 0
        stdout = "64 bytes from host: time=1.23 ms"
        stderr = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Completed())

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE)

    assert outcome.result == "OK"
    assert outcome.detail.startswith("ping_reply_ms=")


def test_http_audio_mixer_write_preserves_latest_known_state(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_http()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    current = {"value": "+1 dB"}

    monkeypatch.setattr(module, "audio_mixer_item_state", lambda current_settings: (current["value"], ("0 dB", "+1 dB"), 123))

    def fake_request_bytes(current_settings, method, path):
        del current_settings
        if method == "PUT":
            current["value"] = "0 dB" if "0%20dB" in path else "+1 dB"
            return 200, b"", {}
        raise AssertionError((method, path))

    monkeypatch.setattr(module, "request_bytes", fake_request_bytes)

    detail = module.write_audio_mixer_item(settings, "0 dB", shared_state=state)

    assert detail == "from=+1 dB to=0 dB"
    assert state.get_shared_resource_value(module.AUDIO_MIXER_SHARED_STATE_KEY) == "0 dB"
    assert state.get_shared_resource_value(module.AUDIO_MIXER_TENTATIVE_STATE_KEY) is None


def test_ftp_normal_mode_performs_login_pasv_nlst_quit_and_close():
    runtime = load_runtime()
    module = load_ftp()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def set_pasv(self, enabled):
            calls.append(("set_pasv", enabled))

        def nlst(self, path):
            calls.append(("nlst", path))
            return ["file1", "file2"]

        def quit(self):
            calls.append("quit")
            return "221 bye"

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE)

    assert outcome.result == "OK"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("set_pasv", True),
        ("nlst", "."),
        "quit",
        "close",
    ]


def test_ftp_open_correctness_skips_quit_after_completed_operation():
    runtime = load_runtime()
    module = load_ftp()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def set_pasv(self, enabled):
            calls.append(("set_pasv", enabled))

        def nlst(self, path):
            calls.append(("nlst", path))
            return ["file1", "file2"]

        def quit(self):
            calls.append("quit")
            return "221 bye"

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.OPEN)

    assert outcome.result == "OK"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("set_pasv", True),
        ("nlst", "."),
        "close",
    ]


def test_ftp_incomplete_correctness_skips_quit_and_passive():
    runtime = load_runtime()
    module = load_ftp()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def set_pasv(self, enabled):
            calls.append(("set_pasv", enabled))

        def nlst(self, path):
            calls.append(("nlst", path))
            return ["file1"]

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.INCOMPLETE)

    assert outcome.result == "OK"
    assert outcome.detail == "NLST bytes=5"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("set_pasv", False),
        ("nlst", "."),
        "close",
    ]


def test_ftp_invalid_correctness_sends_wrong_command_without_quit():
    runtime = load_runtime()
    module = load_ftp()
    calls = []

    class FakeFTP:
        def connect(self, host, port, timeout):
            calls.append(("connect", host, port, timeout))
            return "220 ready"

        def login(self, user, password):
            calls.append(("login", user, password))
            return "230 logged in"

        def sendcmd(self, command):
            calls.append(("sendcmd", command))
            return "500 syntax error"

        def close(self):
            calls.append("close")

    module.ftplib.FTP = FakeFTP

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.INVALID)

    assert outcome.result == "OK"
    assert outcome.detail == "invalid_reply=500 syntax error"
    assert calls == [
        ("connect", "host", 21, 8),
        ("login", "anonymous", ""),
        ("sendcmd", "VIVIPI-WRONG"),
        "close",
    ]


def test_ftp_smoke_incomplete_operations_match_historical_probe():
    runtime = load_runtime()
    module = load_ftp()

    operations = module.incomplete_operations(runtime.ProbeSurface.SMOKE)

    assert [name for name, _ in operations] == ["ftp_greeting_only_quit"]


def test_ftp_context_incomplete_readwrite_uses_incomplete_operations(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()

    def unexpected_surface_operations(*args, **kwargs):
        raise AssertionError("surface_operations should not be used for incomplete FTP probes")

    monkeypatch.setattr(module, "surface_operations", unexpected_surface_operations)
    monkeypatch.setattr(
        module,
        "incomplete_operations",
        lambda surface, *, runner_id=1, concurrent_multi_runner=False: ((
            "ftp_partial_stor_temp",
            lambda settings: (_ for _ in ()).throw(ConnectionResetError(104, "Connection reset by peer")),
        ),),
    )

    context = runtime.ProbeExecutionContext(
        protocol="ftp",
        runner_id=1,
        iteration=1,
        surface=runtime.ProbeSurface.READWRITE,
        state=RotatingState(),
    )

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.INCOMPLETE, context=context)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=ftp_partial_stor_temp expected_disconnect_after_abort"


def test_ftp_context_open_readwrite_uses_surface_operations(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()

    def unexpected_incomplete_operations(*args, **kwargs):
        raise AssertionError("incomplete_operations should not be used for open FTP probes")

    monkeypatch.setattr(module, "incomplete_operations", unexpected_incomplete_operations)
    monkeypatch.setattr(
        module,
        "surface_operations",
        lambda surface, *, runner_id=1, concurrent_multi_runner=False, shared_state=None: ((
            "ftp_upload_tiny_self_file",
            lambda settings, ftp: "open_surface_path",
        ),),
    )
    monkeypatch.setattr(module, "run_open_surface_probe", lambda current_settings, operation: operation(current_settings, None))

    context = runtime.ProbeExecutionContext(
        protocol="ftp",
        runner_id=1,
        iteration=1,
        surface=runtime.ProbeSurface.READWRITE,
        state=RotatingState(),
    )

    outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.OPEN, context=context)

    assert outcome.result == "OK"
    assert outcome.detail == "surface=readwrite op=ftp_upload_tiny_self_file open_surface_path"


def test_ftp_read_surface_rotates_across_operation_names(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()

    class FakeFTP:
        def pwd(self):
            return "/"

        def nlst(self, path):
            if path == ".":
                return ["a", "b", "c"]
            if path == module.FTP_TEMP_DIR:
                return [f"{module.FTP_SELF_FILE_PREFIX}existing.txt"]
            raise AssertionError(path)

        def retrlines(self, command, callback):
            assert command == "LIST ."
            callback("line 1")
            callback("line 2")

    fake_ftp = FakeFTP()
    monkeypatch.setattr(module, "connect", lambda settings: fake_ftp)
    monkeypatch.setattr(module, "close", lambda ftp: None)

    state = RotatingState()
    details = []
    for iteration in range(1, 5):
        context = runtime.ProbeExecutionContext(
            protocol="ftp",
            runner_id=1,
            iteration=iteration,
            surface=runtime.ProbeSurface.READ,
            state=state,
        )
        outcome = module.run_probe(make_settings(runtime), runtime.ProbeCorrectness.COMPLETE, context=context)
        details.append(outcome.detail)

    assert [detail.split()[1] for detail in details] == [
        "op=ftp_pwd",
        "op=ftp_nlst_root",
        "op=ftp_list_root",
        "op=ftp_nlst_temp",
    ]


def test_try_ftp_prime_temp_dir_swallows_failures_and_logs(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()
    logs = []

    monkeypatch.setattr(module, "prime_temp_dir", lambda settings, minimum_count=1: (_ for _ in ()).throw(TimeoutError("timed out")))

    assert module.try_prime_temp_dir(make_settings(runtime), log_fn=logs.append) == ()
    assert logs == ["prime_temp_dir_failed detail=timed out continuing=1"]


def test_ftp_partial_stor_temp_reuses_bounded_temp_path(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()
    settings = make_settings(runtime)
    calls = []

    monkeypatch.setattr(module, "track_self_file", lambda current_settings, path: calls.append(("track", path)))
    monkeypatch.setattr(module, "partial_transfer_abort", lambda current_settings, command, **kwargs: calls.append(("stor", command)) or command)

    first = module.partial_stor_temp(settings)
    second = module.partial_stor_temp(settings)

    assert first == second
    assert calls == [
        ("track", first.removeprefix("STOR ")),
        ("stor", first),
        ("track", second.removeprefix("STOR ")),
        ("stor", second),
    ]


def test_ftp_readwrite_surface_uploads_and_downloads_tiny_and_large_files(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_ftp()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    stored_payloads: dict[str, bytes] = {}

    class FakeFTP:
        def storbinary(self, command, payload):
            path = command.removeprefix("STOR ")
            stored_payloads[path] = payload.read()

        def retrbinary(self, command, callback):
            path = command.removeprefix("RETR ")
            callback(stored_payloads[path])

        def nlst(self, path):
            assert path == module.FTP_TEMP_DIR
            return list(stored_payloads)

    monkeypatch.setattr(module, "track_self_file", lambda current_settings, path: None)

    ftp = FakeFTP()
    operations = dict(module.surface_operations(runtime.ProbeSurface.READWRITE, shared_state=state))

    tiny_upload = operations["ftp_upload_tiny_self_file"](settings, ftp)
    tiny_download = operations["ftp_download_tiny_self_file"](settings, ftp)
    large_upload = operations["ftp_upload_large_self_file"](settings, ftp)
    large_download = operations["ftp_download_large_self_file"](settings, ftp)

    assert tiny_upload.endswith("bytes=1")
    assert tiny_download.endswith("bytes=1")
    assert large_upload.endswith(f"bytes={module.FTP_LARGE_FILE_SIZE_BYTES}")
    assert large_download.endswith(f"bytes={module.FTP_LARGE_FILE_SIZE_BYTES}")
    assert sorted(len(payload) for payload in stored_payloads.values()) == [1, module.FTP_LARGE_FILE_SIZE_BYTES]


def test_ftp_rename_provisions_confirmed_file_instead_of_skipping(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_ftp()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    stored_payloads: dict[str, bytes] = {}

    class FakeFTP:
        def storbinary(self, command, payload):
            stored_payloads[command.removeprefix("STOR ")] = payload.read()

        def rename(self, source, target):
            stored_payloads[target] = stored_payloads.pop(source)

        def nlst(self, path):
            assert path == module.FTP_TEMP_DIR
            return list(stored_payloads)

    monkeypatch.setattr(module, "track_self_file", lambda current_settings, path: None)
    monkeypatch.setattr(module, "forget_self_file", lambda path: None)

    detail = module.rename_self_file(settings, FakeFTP(), shared_state=state)

    assert detail.startswith("from=/Temp/u64test_")
    assert " to=/Temp/u64test_" in detail
    assert state.get_shared_resource_value(module.FTP_SHARED_TENTATIVE_FILES_KEY) == {}
    confirmed = state.get_shared_resource_value(module.FTP_SHARED_CONFIRMED_FILES_KEY)
    assert isinstance(confirmed, dict)
    assert len(confirmed) == 1


def test_ftp_delete_provisions_confirmed_file_instead_of_skipping(monkeypatch):
    runtime = load_runtime()
    connection_test = load_connection_test()
    module = load_ftp()
    settings = make_settings(runtime)
    state = connection_test.ExecutionState(settings=settings, include_runner_context=False, random_seed=1)
    stored_payloads: dict[str, bytes] = {}

    class FakeFTP:
        def storbinary(self, command, payload):
            stored_payloads[command.removeprefix("STOR ")] = payload.read()

        def delete(self, path):
            stored_payloads.pop(path)

        def nlst(self, path):
            assert path == module.FTP_TEMP_DIR
            return list(stored_payloads)

    monkeypatch.setattr(module, "track_self_file", lambda current_settings, path: None)
    monkeypatch.setattr(module, "forget_self_file", lambda path: None)

    detail = module.delete_self_file(settings, FakeFTP(), shared_state=state)

    assert detail.startswith("path=/Temp/u64test_")
    assert state.get_shared_resource_value(module.FTP_SHARED_CONFIRMED_FILES_KEY) == {}


def test_ftp_prime_temp_dir_deletes_stale_self_files_before_seeding(monkeypatch):
    runtime = load_runtime()
    module = load_ftp()
    settings = make_settings(runtime)
    calls = []
    ftp = object()

    monkeypatch.setattr(module, "connect", lambda current_settings: ftp)
    monkeypatch.setattr(module, "close", lambda current_ftp: calls.append(("close", current_ftp)))
    monkeypatch.setattr(module, "collect_temp_entries_if_available", lambda current_ftp: ("/Temp/u64test_old.txt", "/Temp/keep.txt"))
    monkeypatch.setattr(module, "delete_readable_self_files", lambda current_ftp, entries, file_prefix=module.FTP_SELF_FILE_PREFIX: calls.append(("delete", current_ftp, entries, file_prefix)) or ("/Temp/u64test_old.txt",))
    monkeypatch.setattr(module, "seed_self_file", lambda current_settings, current_ftp, ordinal: calls.append(("seed", ordinal)) or f"/Temp/u64test_seed_{ordinal}.txt")

    seeded = module.prime_temp_dir(settings, minimum_count=2)

    assert seeded == ("/Temp/u64test_seed_1.txt", "/Temp/u64test_seed_2.txt")
    assert calls == [
        ("delete", ftp, ("/Temp/u64test_old.txt", "/Temp/keep.txt"), module.FTP_SELF_FILE_PREFIX),
        ("seed", 1),
        ("seed", 2),
        ("close", ftp),
    ]


def test_delete_readable_self_files_forgets_deleted_paths(monkeypatch):
    module = load_ftp()
    deleted = []
    forgotten = []

    class FakeFTP:
        def delete(self, path):
            deleted.append(path)

    monkeypatch.setattr(module, "forget_self_file", lambda path: forgotten.append(path))

    removed = module.delete_readable_self_files(FakeFTP(), ("/Temp/u64test_old.txt", "/Temp/keep.txt"))

    assert removed == ("/Temp/u64test_old.txt",)
    assert deleted == ["/Temp/u64test_old.txt"]
    assert forgotten == ["/Temp/u64test_old.txt"]
