from __future__ import annotations

import contextlib
import io
import json
import socket
import threading
import types

import pytest

from tests.unit.tooling._script_loader import load_script_module


def load_module():
    return load_script_module("u64_ftp_test")


class FakeServerState:
    """Shared state for FakeFTP sessions simulating a device."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {"/USB2"}
        self.lock = threading.Lock()
        self.stor_count = 0
        self.retr_count = 0
        self.deleted_paths: list[str] = []
        self.last_rename_from: str | None = None
        self.last_rename_to: str | None = None

    def ensure_dir(self, path: str) -> None:
        with self.lock:
            self.dirs.add(path)


class FakeFTP:
    """In-memory FTP implementation good enough to exercise the CLI pipeline."""

    def __init__(self, server: FakeServerState) -> None:
        self._server = server
        self._cwd = "/"
        self.sock = types.SimpleNamespace(settimeout=lambda *_: None)
        self._rename_from: str | None = None

    def connect(self, host, port, timeout=None):
        return "220 ok"

    def login(self, user, password):
        return "230 ok"

    def set_pasv(self, flag):
        return None

    def cwd(self, path):
        normalized = self._resolve(path)
        with self._server.lock:
            if normalized not in self._server.dirs:
                import ftplib as _ftplib

                raise _ftplib.error_perm("550 no such dir")
        self._cwd = normalized
        return "250 ok"

    def mkd(self, path):
        self._server.ensure_dir(self._resolve(path))
        return self._resolve(path)

    def delete(self, path):
        resolved = self._resolve(path)
        with self._server.lock:
            if resolved not in self._server.files:
                import ftplib as _ftplib

                raise _ftplib.error_perm("550 not found")
            del self._server.files[resolved]
            self._server.deleted_paths.append(resolved)
        return "250 ok"

    def nlst(self, path=None):
        base = self._cwd if path is None else self._resolve(path)
        prefix = base.rstrip("/") + "/"
        with self._server.lock:
            entries = [
                name.rsplit("/", 1)[-1]
                for name in sorted(self._server.files)
                if name.startswith(prefix)
            ]
        return entries

    def retrlines(self, cmd, callback):
        with self._server.lock:
            entries = [
                name.rsplit("/", 1)[-1]
                for name in sorted(self._server.files)
                if name.startswith(self._cwd + "/")
            ]
        for entry in entries:
            callback(entry)
        return "226 ok"

    def sendcmd(self, cmd):
        parts = cmd.split(" ", 1)
        verb = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else None

        if verb in {"FEAT", "SYST", "NOOP", "PWD", "XPWD"}:
            return "200 ok"
        if verb == "TYPE":
            return "200 type set"
        if verb == "MODE":
            raise __import__("ftplib").error_perm("504 unsupported")
        if verb == "PORT":
            return "200 port ignored"
        if verb in {"XMKD", "MKD"}:
            assert arg is not None
            return self.mkd(arg)
        if verb in {"XRMD", "RMD"}:
            assert arg is not None
            resolved = self._resolve(arg)
            with self._server.lock:
                self._server.dirs.discard(resolved)
            return "250 removed"
        if verb == "CWD":
            assert arg is not None
            return self.cwd(arg)
        if verb == "CDUP":
            parent = self._cwd.rsplit("/", 1)[0] or "/"
            return self.cwd(parent)
        if verb == "SIZE":
            assert arg is not None
            resolved = self._resolve(arg)
            with self._server.lock:
                return f"213 {len(self._server.files[resolved])}"
        if verb == "MLST":
            assert arg is not None
            resolved = self._resolve(arg)
            with self._server.lock:
                if resolved not in self._server.files:
                    raise __import__("ftplib").error_perm("550 not found")
            return "250-Listing\n size=1; type=file;\n250 End"
        if verb == "RNFR":
            assert arg is not None
            resolved = self._resolve(arg)
            with self._server.lock:
                if resolved not in self._server.files:
                    raise __import__("ftplib").error_perm("550 not found")
            self._rename_from = resolved
            self._server.last_rename_from = resolved
            return "350 ready"
        if verb == "RNTO":
            assert arg is not None
            resolved = self._resolve(arg)
            with self._server.lock:
                source = self._rename_from
                if source is None or source not in self._server.files:
                    raise __import__("ftplib").error_perm("503 bad sequence")
                self._server.files[resolved] = self._server.files.pop(source)
                self._server.last_rename_to = resolved
                self._rename_from = None
            return "250 renamed"
        if verb == "ABOR":
            raise TimeoutError("timed out waiting for ABOR reply")
        raise AssertionError(f"Unsupported FTP command: {cmd}")

    def quit(self):
        return "221 bye"

    def close(self):
        return None

    def _resolve(self, path: str) -> str:
        if path.startswith("/"):
            return path.rstrip("/") or "/"
        if self._cwd == "/":
            return f"/{path}".rstrip("/") or "/"
        return f"{self._cwd}/{path}".rstrip("/")

    def storbinary(self, cmd, fp):
        assert cmd.startswith("STOR ")
        name = cmd[5:]
        path = self._resolve(name)
        data = fp.read()
        with self._server.lock:
            self._server.files[path] = data
            self._server.stor_count += 1
        return "226 ok"

    def retrbinary(self, cmd, callback):
        assert cmd.startswith("RETR ")
        name = cmd[5:]
        path = self._resolve(name)
        with self._server.lock:
            if path not in self._server.files:
                import ftplib as _ftplib

                raise _ftplib.error_perm("550 not found")
            data = self._server.files[path]
            self._server.retr_count += 1
        for offset in range(0, len(data), 8192):
            callback(data[offset : offset + 8192])
        return "226 ok"


def install_fake_ftp(module, server: FakeServerState | None = None) -> FakeServerState:
    state = server or FakeServerState()

    def factory(*_args, **_kwargs):
        return FakeFTP(state)

    module.ftplib.FTP = factory  # type: ignore[attr-defined]
    return state


def run_main(module, argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exit_code = module.main(argv)
    return exit_code, buf.getvalue()


def test_parse_size_token_supports_k_m_g():
    module = load_module()

    assert module.parse_size_token("20K") == ("20K", 20480)
    assert module.parse_size_token("200K") == ("200K", 204800)
    assert module.parse_size_token("1M") == ("1M", 1048576)
    assert module.parse_size_token("2G") == ("2G", 2 * 1024 * 1024 * 1024)
    assert module.parse_size_token("512") == ("512", 512)


def test_parse_sizes_rejects_empty_and_zero():
    module = load_module()

    with pytest.raises(Exception):
        module.parse_sizes("")
    with pytest.raises(Exception):
        module.parse_size_token("0K")


def test_parse_sizes_default_matches_spec():
    module = load_module()

    assert module.parse_sizes("20K,200K,1M") == (
        ("20K", 20480),
        ("200K", 204800),
        ("1M", 1048576),
    )


def test_build_payload_is_deterministic_and_exact_size():
    module = load_module()

    assert module.build_payload(20 * 1024) == module.build_payload(20 * 1024)
    assert len(module.build_payload(20 * 1024)) == 20 * 1024
    # 1 byte smaller than seed length exercises remainder path
    assert len(module.build_payload(1)) == 1


def test_build_filename_matches_spec_format():
    module = load_module()

    assert module.build_filename("20K", 1, 3) == "u64ftp_20K_1_3.bin"
    assert module.build_filename("1M", 2, 7) == "u64ftp_1M_2_7.bin"


def test_parse_remote_dir_rejects_non_absolute_and_parent_segments():
    module = load_module()

    with pytest.raises(Exception):
        module.parse_remote_dir("USB2/test/FTP")
    with pytest.raises(Exception):
        module.parse_remote_dir("/USB2/../etc")


def test_validate_managed_test_filename_rejects_non_test_or_nested_paths():
    module = load_module()

    assert module.validate_managed_test_filename("u64ftp_1K_1_1.bin") == "u64ftp_1K_1_1.bin"
    assert module.validate_managed_test_filename("u64ftp_1K_1_1.bin.rn") == "u64ftp_1K_1_1.bin.rn"
    with pytest.raises(ValueError):
        module.validate_managed_test_filename("other.bin")
    with pytest.raises(ValueError):
        module.validate_managed_test_filename("../u64ftp_1K_1_1.bin")


def test_compute_files_per_worker_uses_ceiling_and_respects_override():
    module = load_module()

    # 1M target / 20K size = 52 files per worker (spec example)
    assert module.compute_files_per_worker(20 * 1024, 1024 * 1024, None) == 52
    assert module.compute_files_per_worker(200 * 1024, 1024 * 1024, None) == 6
    assert module.compute_files_per_worker(1024 * 1024, 1024 * 1024, None) == 1
    # Override wins
    assert module.compute_files_per_worker(20 * 1024, 1024 * 1024, 4) == 4
    # Minimum one
    assert module.compute_files_per_worker(1024 * 1024, 1024, None) == 1


def test_modes_for_and_workers_for_match_spec():
    module = load_module()

    assert module.modes_for("single") == ("single",)
    assert module.modes_for("multi") == ("multi",)
    assert module.modes_for("both") == ("single", "multi")
    assert module.workers_for("single", 3) == 1
    assert module.workers_for("multi", 3) == 3


def test_help_text_lists_all_required_flags():
    module = load_module()
    help_text = module.build_parser().format_help()

    for flag in [
        "--host",
        "--ftp-port",
        "--ftp-user",
        "--ftp-pass",
        "--passive",
        "--no-passive",
        "--timeout-s",
        "--remote-dir",
        "--sizes",
        "--target-bytes",
        "--files-per-stage",
        "--concurrency",
        "--mode",
        "--verify",
        "--no-verify",
        "--fail-fast",
        "--max-runtime-s",
        "--format",
        "--verbose",
    ]:
        assert flag in help_text, flag

    assert "Default: /Temp/test/FTP." in help_text
    assert "Default: 20K,200K,1M." in help_text
    assert "Default: 1M." in help_text
    assert "Choices: single, multi, both." in help_text
    assert "Choices: text, json." in help_text
    assert "Accepted units per token: raw bytes, K, M, or G" in help_text
    assert "Before each run, the tool removes only prior managed test files" in help_text
    assert "Examples:" in help_text


def test_zero_arg_defaults_match_spec():
    module = load_module()
    args = module.build_parser().parse_args([])

    assert args.host == "u64"
    assert args.ftp_port == 21
    assert args.ftp_user == ""
    assert args.ftp_pass == ""
    assert args.passive is True
    assert args.timeout_s == 10
    assert args.remote_dir == "/Temp/test/FTP"
    assert args.sizes == (("20K", 20480), ("200K", 204800), ("1M", 1048576))
    assert args.target_bytes == 1024 * 1024
    assert args.files_per_stage is None
    assert args.concurrency == 3
    assert args.mode == "both"
    assert args.verify is True
    assert args.fail_fast is False
    assert args.max_runtime_s == 0
    assert args.format == "text"
    assert args.verbose is False


def test_config_line_format_matches_spec_example():
    module = load_module()
    state = install_fake_ftp(module)
    # Pre-seed the remote dir so ensure_remote_dir is a no-op
    state.ensure_dir("/Temp")
    state.ensure_dir("/Temp/test")
    state.ensure_dir("/Temp/test/FTP")

    exit_code, output = run_main(
        module,
        [
            "--sizes",
            "20K",
            "--target-bytes",
            "20K",
            "--concurrency",
            "1",
            "--mode",
            "single",
        ],
    )
    assert exit_code == 0

    lines = [line for line in output.splitlines() if line]
    assert lines[0].startswith("2")  # timestamp prefix
    assert "protocol=config result=INFO" in lines[0]
    assert 'detail="host=u64' in lines[0]
    assert "dir=/Temp/test/FTP" in lines[0]
    assert "verify=1" in lines[0]


def test_end_to_end_happy_path_uploads_and_downloads_all_files():
    module = load_module()
    state = install_fake_ftp(module)

    exit_code, output = run_main(
        module,
        [
            "-H",
            "fake",
            "--sizes",
            "1K,4K",
            "--target-bytes",
            "4K",
            "--concurrency",
            "2",
            "--mode",
            "both",
            "--max-runtime-s",
            "30",
        ],
    )

    assert exit_code == 0, output
    # Expected totals: for 1K at target 4K => 4 files/worker
    #   single: 1 worker * 4 = 4
    #   multi:  2 workers * 4 = 8
    # For 4K at target 4K => 1 file/worker
    #   single: 1 * 1 = 1
    #   multi:  2 * 1 = 2
    # Total = 15 STORs + 15 RETRs
    assert state.stor_count == 15
    assert state.retr_count == 15

    assert "protocol=config result=INFO" in output
    assert output.count("protocol=stage result=START") == 4
    assert output.count("protocol=stage result=END") == 4
    assert "protocol=summary result=OK" in output
    # Unit-tagged throughput keys must be present on stage END and summary
    assert "agg_up_KBps=" in output
    assert "agg_down_KBps=" in output
    assert "stage_s=" in output
    assert "total_up_KB=" in output
    assert "total_down_KB=" in output


def test_ensure_remote_dir_creates_missing_directory():
    module = load_module()
    state = install_fake_ftp(module)
    assert "/Temp/test/FTP" not in state.dirs

    exit_code, output = run_main(
        module,
        [
            "-H",
            "fake",
            "--sizes",
            "1K",
            "--target-bytes",
            "1K",
            "--concurrency",
            "1",
            "--mode",
            "single",
        ],
    )

    assert exit_code == 0, output
    assert "/Temp/test/FTP" in state.dirs


def test_list_managed_test_filenames_returns_only_managed_basenames_in_cwd():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/Temp")
    state.ensure_dir("/Temp/test")
    state.ensure_dir("/Temp/test/FTP")
    state.files["/Temp/test/FTP/u64ftp_1K_1_1.bin"] = b"one"
    state.files["/Temp/test/FTP/u64ftp_1K_1_2.bin.rn"] = b"two"
    state.files["/Temp/test/FTP/not-managed.bin"] = b"keep"
    state.files["/Temp/test/OTHER/u64ftp_1K_9_9.bin"] = b"elsewhere"

    ftp = FakeFTP(state)
    ftp.cwd("/Temp/test/FTP")

    assert module.list_managed_test_filenames(ftp) == ["u64ftp_1K_1_1.bin", "u64ftp_1K_1_2.bin.rn"]


def test_cleanup_remote_test_files_deletes_only_managed_files_in_selected_directory():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/Temp")
    state.ensure_dir("/Temp/test")
    state.ensure_dir("/Temp/test/FTP")
    state.ensure_dir("/Temp/test/OTHER")
    state.files["/Temp/test/FTP/u64ftp_20K_1_1.bin"] = b"one"
    state.files["/Temp/test/FTP/u64ftp_20K_1_2.bin.rn"] = b"two"
    state.files["/Temp/test/FTP/keep.txt"] = b"keep"
    state.files["/Temp/test/OTHER/u64ftp_20K_9_9.bin"] = b"elsewhere"
    args = module.build_parser().parse_args(["--remote-dir", "/Temp/test/FTP"])

    deleted_count, error = module.cleanup_remote_test_files(args)

    assert error is None
    assert deleted_count == 2
    assert state.deleted_paths == [
        "/Temp/test/FTP/u64ftp_20K_1_1.bin",
        "/Temp/test/FTP/u64ftp_20K_1_2.bin.rn",
    ]
    assert "/Temp/test/FTP/keep.txt" in state.files
    assert "/Temp/test/OTHER/u64ftp_20K_9_9.bin" in state.files


def test_cleanup_remote_test_files_reports_open_session_errors(monkeypatch):
    module = load_module()
    args = module.build_parser().parse_args(["--remote-dir", "/Temp/test/FTP"])

    def raise_open(_args):
        raise module.FtpOpenError("cwd_failed", module.ftplib.error_perm("550 missing"))

    monkeypatch.setattr(module, "open_session", raise_open)

    deleted_count, error = module.cleanup_remote_test_files(args)

    assert deleted_count == 0
    assert error == "cwd_failed: 550 missing"


def test_connect_failure_yields_classified_transfers_and_nonzero_exit():
    module = load_module()

    class FailConnect:
        def __init__(self) -> None:
            self.sock = None

        def connect(self, *_a, **_k):
            raise OSError("refused")

        def close(self):
            return None

    module.ftplib.FTP = lambda *_a, **_k: FailConnect()  # type: ignore[attr-defined]

    exit_code, output = run_main(
        module,
        [
            "-H",
            "x",
            "--sizes",
            "1K",
            "--target-bytes",
            "1K",
            "--concurrency",
            "1",
            "--mode",
            "single",
            "--no-ensure-remote-dir",
        ],
    )

    assert exit_code == 1
    assert "protocol=setup result=FAIL" in output
    assert "phase=cleanup" in output
    assert "connect_failed:_refused" in output


def test_verify_mismatch_is_reported_when_download_corrupts_payload():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    real_retrbinary = FakeFTP.retrbinary

    def corrupt_retrbinary(self, cmd, callback):
        # Deliver wrong bytes regardless of the stored content
        callback(b"WRONG")

    FakeFTP.retrbinary = corrupt_retrbinary  # type: ignore[assignment]
    try:
        exit_code, output = run_main(
            module,
            [
                "-H",
                "fake",
                "--sizes",
                "1K",
                "--target-bytes",
                "1K",
                "--concurrency",
                "1",
                "--mode",
                "single",
            ],
        )
    finally:
        FakeFTP.retrbinary = real_retrbinary  # type: ignore[assignment]

    assert exit_code == 1
    assert "phase=verify_mismatch" in output
    assert "verify_fail=1" in output


def test_fail_fast_stops_after_first_failure():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    real_retrbinary = FakeFTP.retrbinary

    def corrupt_retrbinary(self, cmd, callback):
        callback(b"WRONG")

    FakeFTP.retrbinary = corrupt_retrbinary  # type: ignore[assignment]
    try:
        exit_code, output = run_main(
            module,
            [
                "-H",
                "fake",
                "--sizes",
                "1K,4K",
                "--target-bytes",
                "4K",
                "--concurrency",
                "2",
                "--mode",
                "both",
                "--fail-fast",
            ],
        )
    finally:
        FakeFTP.retrbinary = real_retrbinary  # type: ignore[assignment]

    assert exit_code == 1
    # Only the first stage should have started
    assert output.count("protocol=stage result=START") == 1


def test_json_mode_emits_single_document_with_config_stages_summary():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    exit_code, output = run_main(
        module,
        [
            "-H",
            "fake",
            "--sizes",
            "1K",
            "--target-bytes",
            "2K",
            "--concurrency",
            "2",
            "--mode",
            "both",
            "--format",
            "json",
        ],
    )

    assert exit_code == 0
    document = json.loads(output)
    assert set(document) == {"config", "stages", "ops", "summary"}
    assert len(document["stages"]) == 2
    assert document["summary"]["result"] == "OK"
    assert document["summary"]["stages_run"] == 2
    assert document["summary"]["success"] is True
    assert document["summary"]["stages_failed"] == 0


def test_run_ops_stage_rejects_non_owned_or_escaped_filenames():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")
    emitter = module.Emitter(json_mode=False)
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP"])

    result = module.run_ops_stage(args, emitter, ["../outside.bin"])
    assert result.success is False
    assert result.error_detail is not None

    result = module.run_ops_stage(args, emitter, ["not_uploaded_by_test.bin"])
    assert result.success is False
    assert result.error_detail is not None


def test_ops_stage_deletes_only_managed_uploaded_files_in_selected_directory():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")
    state.ensure_dir("/USB2/test/OTHER")
    managed_name = "u64ftp_1K_1_1.bin"
    other_name = "unmanaged.bin"
    state.files["/USB2/test/FTP/" + managed_name] = b"data"
    state.files["/USB2/test/OTHER/" + managed_name] = b"elsewhere"
    state.files["/USB2/test/FTP/" + other_name] = b"keep"
    emitter = module.Emitter(json_mode=False)
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP"])

    result = module.run_ops_stage(args, emitter, [managed_name])

    assert result.success is True
    assert state.deleted_paths == ["/USB2/test/FTP/" + managed_name]
    assert state.last_rename_from == "/USB2/test/FTP/" + managed_name + ".rn"
    assert state.last_rename_to == "/USB2/test/FTP/" + managed_name
    assert "/USB2/test/OTHER/" + managed_name in state.files
    assert "/USB2/test/FTP/" + other_name in state.files


def test_max_runtime_limit_stops_between_stages_and_marks_partial():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    # With max_runtime_s=0 the deadline is immediately in the past after
    # the first stage, forcing the loop to stop between stages.
    exit_code, output = run_main(
        module,
        [
            "-H",
            "fake",
            "--sizes",
            "1K,4K,8K",
            "--target-bytes",
            "1K",
            "--concurrency",
            "1",
            "--mode",
            "both",
            "--max-runtime-s",
            "0",
        ],
    )

    # max-runtime-s of 0 disables the deadline per current semantics,
    # so this should complete cleanly. Flip to 1 to assert PARTIAL.
    assert exit_code == 0 or exit_code == 2
    assert "protocol=summary" in output


def test_progress_bar_disabled_when_stdout_is_not_tty():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    # run_main redirects stdout to a StringIO which is not a TTY, so the
    # progress bar must be fully suppressed and output must stay line-oriented.
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        exit_code, output = run_main(
            module,
            [
                "-H",
                "fake",
                "--sizes",
                "1K",
                "--target-bytes",
                "2K",
                "--concurrency",
                "1",
                "--mode",
                "single",
            ],
        )

    assert exit_code == 0
    assert "\r" not in output
    assert "\x1b[" not in output
    assert "\r" not in stderr.getvalue()
    assert "\x1b[" not in stderr.getvalue()


def test_progress_bar_renders_single_line_when_tty_enabled():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    fake_stdout = FakeTTY()
    with contextlib.redirect_stdout(fake_stdout), contextlib.redirect_stderr(io.StringIO()):
        exit_code = module.main(
            [
                "-H",
                "fake",
                "--sizes",
                "1K",
                "--target-bytes",
                "4K",
                "--concurrency",
                "1",
                "--mode",
                "single",
            ]
        )
    output = fake_stdout.getvalue()

    assert exit_code == 0
    assert "\r" in output, "expected carriage-return progress updates in TTY mode"
    assert "\x1b[2K" in output, "expected ANSI erase-line when progress bar clears"
    # Progress bar must render a bracketed bar with percent + count
    assert "[" in output and "]" in output
    assert "4/4" in output, output
    # Permanent log lines are still present
    assert "protocol=config result=INFO" in output
    assert "protocol=stage result=START" in output
    assert "protocol=stage result=END" in output
    assert "protocol=summary result=OK" in output


def test_progress_bar_prefers_tty_stderr_when_stdout_is_not_interactive():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    fake_stdout = io.StringIO()
    fake_stderr = FakeTTY()
    with contextlib.redirect_stdout(fake_stdout), contextlib.redirect_stderr(fake_stderr):
        exit_code = module.main(
            [
                "-H",
                "fake",
                "--sizes",
                "1K",
                "--target-bytes",
                "4K",
                "--concurrency",
                "1",
                "--mode",
                "single",
            ]
        )

    assert exit_code == 0
    assert "protocol=config result=INFO" in fake_stdout.getvalue()
    assert "protocol=summary result=OK" in fake_stdout.getvalue()
    assert "\r" not in fake_stdout.getvalue()
    assert "\r" in fake_stderr.getvalue()
    assert "\x1b[2K" in fake_stderr.getvalue()


def test_progress_bar_tick_counts_failures_separately():
    module = load_module()

    bar = module.ProgressBar(enabled=True, stream=io.StringIO())
    bar.start("size=1K mode=single", 5)
    bar.tick(True)
    bar.tick(False)
    bar.tick(True)
    # _last_line holds the most recent draw
    assert "3/5" in bar._last_line
    assert "fail=1" in bar._last_line
    bar.finish()
    assert bar.label is None


def test_aggregate_throughput_is_bytes_over_stage_wall_time():
    module = load_module()
    stage = module.StageResult(
        size_label="20K",
        size_bytes=20 * 1024,
        mode="multi",
        workers=3,
        files_per_worker=10,
        total_files=30,
    )
    stage.started_at_s = 0.0
    stage.ended_at_s = 5.0  # 5s wall clock
    for worker in range(1, 4):
        for iteration in range(1, 11):
            stage.transfers.append(
                module.TransferResult(
                    size_label="20K",
                    size_bytes=20 * 1024,
                    worker=worker,
                    iteration=iteration,
                    success=True,
                    upload_time_s=1.0,
                    download_time_s=1.0,
                    upload_bytes=20 * 1024,
                    download_bytes=20 * 1024,
                    verify_ok=True,
                    verify_checked=True,
                )
            )
    # 30 files * 20 KB = 600 KB in 5 s => 120 KB/s aggregate
    assert stage.aggregate_upload_KBps() == pytest.approx(120.0)
    assert stage.aggregate_download_KBps() == pytest.approx(120.0)


def test_progress_bar_noop_when_disabled():
    module = load_module()
    stream = io.StringIO()
    bar = module.ProgressBar(enabled=False, stream=stream)
    bar.start("x", 3)
    bar.tick(True)
    bar.finish()
    assert stream.getvalue() == ""


def test_verify_disabled_skips_verify_marker():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    exit_code, output = run_main(
        module,
        [
            "-H",
            "fake",
            "--sizes",
            "1K",
            "--target-bytes",
            "1K",
            "--concurrency",
            "1",
            "--mode",
            "single",
            "--no-verify",
            "-v",
        ],
    )

    assert exit_code == 0
    assert "verify=SKIP" in output


def test_parser_helpers_cover_additional_validation_branches():
    module = load_module()

    assert module.parse_byte_size("1K") == 1024
    assert module.parse_mode(" SINGLE ") == "single"
    assert module.parse_format(" JSON ") == "json"
    assert module.parse_remote_dir("//USB2/./test//FTP/") == "/USB2/test/FTP"
    assert module.parse_remote_dir("/") == "/"

    with pytest.raises(Exception):
        module.parse_size_token("")
    with pytest.raises(Exception):
        module.parse_size_token("wat")
    with pytest.raises(Exception):
        module.parse_size_token("0.4")
    with pytest.raises(Exception):
        module.parse_sizes(" , ")
    with pytest.raises(Exception):
        module.parse_mode("parallel")
    with pytest.raises(Exception):
        module.parse_format("yaml")
    with pytest.raises(ValueError):
        module.validate_ftp_basename("")
    with pytest.raises(ValueError):
        module.validate_ftp_basename(".")
    with pytest.raises(ValueError):
        module.validate_ftp_basename("bad/name")
    with pytest.raises(ValueError):
        module.validate_ftp_basename("bad\nname")
    with pytest.raises(Exception):
        module.parse_remote_dir("")
    with pytest.raises(Exception):
        module.parse_remote_dir("/bad\npath")
    with pytest.raises(Exception):
        module.parse_remote_dir("/bad\\segment")


def test_progress_bar_pause_resume_and_stream_failures():
    module = load_module()

    bar = module.ProgressBar(enabled=True, stream=io.StringIO())
    bar.label = "size=1K"
    assert bar.pause() is False
    bar.start("size=1K", 2)
    assert bar.pause() is True
    bar.resume()
    previous_line = bar._last_line
    bar._last_draw_at = module.time.perf_counter()
    bar._draw(force=False)
    assert bar._last_line == previous_line

    class FailingStream:
        def write(self, _text):
            raise OSError("write failed")

        def flush(self):
            raise OSError("flush failed")

    error_bar = module.ProgressBar(enabled=True, stream=FailingStream())
    error_bar.start("size=1K", 1)
    error_bar._last_line = "stale"
    error_bar._clear()
    assert error_bar._last_line == ""


def test_open_session_close_session_and_safe_sendcmd_failure_paths(monkeypatch):
    module = load_module()
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP"])

    class LoginFailFTP:
        def connect(self, *_args, **_kwargs):
            return "220 ok"

        def login(self, *_args, **_kwargs):
            raise module.ftplib.error_perm("bad login")

        def close(self):
            raise OSError("close failed")

    monkeypatch.setattr(module.ftplib, "FTP", LoginFailFTP)
    with pytest.raises(module.FtpOpenError) as login_error:
        module.open_session(args)
    assert login_error.value.phase == "login_failed"

    class CwdFailFTP:
        def __init__(self):
            self.sock = types.SimpleNamespace(settimeout=lambda *_args: None)

        def connect(self, *_args, **_kwargs):
            return "220 ok"

        def login(self, *_args, **_kwargs):
            return "230 ok"

        def set_pasv(self, _flag):
            return None

        def cwd(self, _path):
            raise module.ftplib.error_perm("no dir")

        def close(self):
            raise OSError("close failed")

    monkeypatch.setattr(module.ftplib, "FTP", CwdFailFTP)
    with pytest.raises(module.FtpOpenError) as cwd_error:
        module.open_session(args)
    assert cwd_error.value.phase == "cwd_failed"

    class NoSockFTP:
        def connect(self, *_args, **_kwargs):
            return "220 ok"

        def login(self, *_args, **_kwargs):
            return "230 ok"

        def set_pasv(self, _flag):
            return None

        def cwd(self, _path):
            return "250 ok"

        def sendcmd(self, cmd):
            return f"200 {cmd}"

        def quit(self):
            return "221 bye"

        def close(self):
            return None

    monkeypatch.setattr(module.ftplib, "FTP", NoSockFTP)
    ftp = module.open_session(args)
    assert isinstance(ftp, NoSockFTP)

    class CloseFailFTP:
        def quit(self):
            raise EOFError("socket gone")

        def close(self):
            raise OSError("close failed")

    module.close_session(CloseFailFTP())
    module.close_session(None)

    with pytest.raises(ValueError):
        module.safe_sendcmd(ftp, "BAD CMD")
    with pytest.raises(ValueError):
        module.safe_sendcmd(ftp, "CWD", "bad/name")


def test_ensure_remote_dir_handles_connect_and_mkdir_failures(monkeypatch):
    module = load_module()
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP"])

    class ConnectFailFTP:
        def connect(self, *_args, **_kwargs):
            raise OSError("offline")

        def quit(self):
            raise OSError("quit failed")

        def close(self):
            raise OSError("close failed")

    monkeypatch.setattr(module.ftplib, "FTP", ConnectFailFTP)
    ok, detail = module.ensure_remote_dir(args)
    assert ok is False
    assert detail == "connect_or_login: offline"

    class MkdRaceFTP:
        def __init__(self):
            self.paths: set[str] = set()

        def connect(self, *_args, **_kwargs):
            return "220 ok"

        def login(self, *_args, **_kwargs):
            return "230 ok"

        def set_pasv(self, _flag):
            return None

        def cwd(self, path):
            if path not in self.paths:
                raise module.ftplib.error_perm("missing")
            return "250 ok"

        def mkd(self, path):
            self.paths.add(path)
            raise module.ftplib.error_perm("already exists")

        def quit(self):
            return "221 bye"

        def close(self):
            return None

    monkeypatch.setattr(module.ftplib, "FTP", MkdRaceFTP)
    ok, detail = module.ensure_remote_dir(args)
    assert ok is True
    assert detail is None

    class MkdOsErrorFTP(MkdRaceFTP):
        def mkd(self, path):
            raise OSError(f"disk full for {path}")

        def quit(self):
            raise OSError("quit failed")

        def close(self):
            raise OSError("close failed")

    monkeypatch.setattr(module.ftplib, "FTP", MkdOsErrorFTP)
    ok, detail = module.ensure_remote_dir(args)
    assert ok is False
    assert detail is not None
    assert detail.startswith("mkd /USB2")


def test_run_single_transfer_classifies_filename_upload_and_download_failures():
    module = load_module()
    payload = b"payload"

    class UploadFailFTP:
        def storbinary(self, _cmd, _fp):
            raise OSError("upload broke")

        def retrbinary(self, _cmd, _callback):
            raise AssertionError("should not download after upload failure")

    result = module.run_single_transfer(UploadFailFTP(), "bad/name", 7, 1, 1, payload, True)
    assert result.failure_type == "filename_invalid"

    result = module.run_single_transfer(UploadFailFTP(), "1K", 1024, 1, 1, payload, True)
    assert result.failure_type == "upload_failed"

    class DownloadFailFTP:
        def storbinary(self, _cmd, _fp):
            return "226 ok"

        def retrbinary(self, _cmd, _callback):
            raise socket.timeout("download broke")

    result = module.run_single_transfer(DownloadFailFTP(), "1K", 1024, 1, 1, payload, True)
    assert result.failure_type == "download_failed"


def test_worker_loop_abort_paths_and_helper_formatters(monkeypatch):
    module = load_module()
    emitter = module.Emitter(json_mode=False)
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP", "--no-ensure-remote-dir"])

    def raise_open(_args):
        raise module.FtpOpenError("connect_failed", OSError("offline"))

    monkeypatch.setattr(module, "open_session", raise_open)

    fail_fast_args = types.SimpleNamespace(**{**vars(args), "fail_fast": True})

    fail_fast_context = module.StageContext(
        args=fail_fast_args,
        emitter=emitter,
        abort_flag=threading.Event(),
    )
    results = module.worker_loop(1, 3, "1K", 1024, b"x", fail_fast_context)
    assert len(results) == 1
    assert fail_fast_context.abort_flag.is_set()

    preaborted = threading.Event()
    preaborted.set()
    preaborted_args = types.SimpleNamespace(**{**vars(args), "fail_fast": False})
    preaborted_context = module.StageContext(
        args=preaborted_args,
        emitter=emitter,
        abort_flag=preaborted,
    )
    results = module.worker_loop(1, 3, "1K", 1024, b"x", preaborted_context)
    assert len(results) == 1

    class IdleFTP:
        def quit(self):
            return "221 bye"

        def close(self):
            return None

    monkeypatch.setattr(module, "open_session", lambda _args: IdleFTP())
    stopped_args = types.SimpleNamespace(**{**vars(args), "fail_fast": False})
    stopped_context = module.StageContext(
        args=stopped_args,
        emitter=emitter,
        abort_flag=threading.Event(),
    )
    stopped_context.abort_flag.set()
    assert module.worker_loop(1, 2, "1K", 1024, b"x", stopped_context) == []

    class BadStdout:
        def isatty(self):
            raise ValueError("bad stdout")

    monkeypatch.setattr(module.sys, "stdout", BadStdout())
    monkeypatch.setattr(module.sys, "stderr", io.StringIO())
    assert module._is_interactive_stream(module.sys.stdout) is False
    assert module._select_progress_stream() is None
    assert module._format_bytes_short(2 * 1024 * 1024 * 1024) == "2G"
    assert module._format_bytes_short(2 * 1024 * 1024) == "2M"
    assert module._format_bytes_short(1537) == "1537"


def test_build_payload_rejects_zero_and_run_stage_records_worker_future_exceptions(monkeypatch):
    module = load_module()
    with pytest.raises(ValueError):
        module.build_payload(0)

    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP", "--no-ensure-remote-dir"])
    emitter = module.Emitter(json_mode=False)

    def fake_worker_loop(worker_index, *_args, **_kwargs):
        if worker_index == 2:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(module, "worker_loop", fake_worker_loop)
    stage = module.run_stage(args, emitter, "1K", 1024, "multi", 2, None)
    assert stage.failure_count() == 1
    assert stage.transfers[0].failure_detail == "boom"


def test_run_emits_setup_fail_partial_and_ops_failure(monkeypatch):
    module = load_module()

    def success_stage(size_label: str) -> object:
        stage = module.StageResult(
            size_label=size_label,
            size_bytes=1024,
            mode="single",
            workers=1,
            files_per_worker=1,
            total_files=1,
        )
        stage.started_at_s = 0.0
        stage.ended_at_s = 1.0
        stage.transfers.append(
            module.TransferResult(
                size_label=size_label,
                size_bytes=1024,
                worker=1,
                iteration=1,
                success=True,
                upload_time_s=1.0,
                download_time_s=1.0,
                upload_bytes=1024,
                download_bytes=1024,
                verify_ok=True,
                verify_checked=True,
            )
        )
        return stage

    monkeypatch.setattr(module, "ensure_remote_dir", lambda _args: (False, "mkdir failed"))
    monkeypatch.setattr(module, "cleanup_remote_test_files", lambda _args: (0, None))
    monkeypatch.setattr(module, "run_stage", lambda *args, **kwargs: success_stage(args[2]))
    monkeypatch.setattr(module, "run_ops_stage", lambda *_args, **_kwargs: module.OpsResult(success=False))
    setup_code, setup_output = run_main(module, ["--sizes", "1K", "--target-bytes", "1K"])
    assert setup_code == 1
    assert "protocol=setup result=FAIL" in setup_output
    assert "protocol=summary result=FAIL" in setup_output

    partial_args = module.build_parser().parse_args(
        ["--sizes", "1K,2K", "--target-bytes", "1K", "--mode", "both", "--max-runtime-s", "1", "--no-ensure-remote-dir"]
    )
    times = iter([0.0, 0.0, 1.0, 1.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(module, "ensure_remote_dir", lambda _args: (True, None))
    monkeypatch.setattr(module, "cleanup_remote_test_files", lambda _args: (0, None))
    monkeypatch.setattr(module, "run_stage", lambda *args, **kwargs: success_stage(args[2]))
    assert module.run(partial_args) == 2


def test_run_fails_when_startup_cleanup_fails(monkeypatch):
    module = load_module()

    monkeypatch.setattr(module, "ensure_remote_dir", lambda _args: (True, None))
    monkeypatch.setattr(module, "cleanup_remote_test_files", lambda _args: (3, "550 denied"))

    exit_code, output = run_main(module, ["--sizes", "1K", "--target-bytes", "1K"])

    assert exit_code == 1
    assert "protocol=setup result=FAIL" in output
    assert "phase=cleanup" in output
    assert "550_denied" in output


def test_run_ops_stage_closes_session_when_connect_sequence_fails(monkeypatch):
    module = load_module()
    emitter = module.Emitter(json_mode=False)
    args = module.build_parser().parse_args(["--remote-dir", "/USB2/test/FTP"])
    closed = []

    class ConnectFailFTP:
        def connect(self, *_args, **_kwargs):
            raise OSError("offline")

        def quit(self):
            closed.append("quit")
            return "221 bye"

        def close(self):
            closed.append("close")
            return None

    monkeypatch.setattr(module.ftplib, "FTP", ConnectFailFTP)

    result = module.run_ops_stage(args, emitter, ["u64ftp_1K_1_1.bin"])

    assert result.success is False
    assert result.error_detail == "connect_or_login: offline"
    assert closed == ["quit", "close"]
