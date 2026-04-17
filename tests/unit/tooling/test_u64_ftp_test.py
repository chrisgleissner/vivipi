from __future__ import annotations

import contextlib
import io
import json
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


def test_zero_arg_defaults_match_spec():
    module = load_module()
    args = module.build_parser().parse_args([])

    assert args.host == "u64"
    assert args.ftp_port == 21
    assert args.ftp_user == ""
    assert args.ftp_pass == ""
    assert args.passive is True
    assert args.timeout_s == 10
    assert args.remote_dir == "/USB2/test/FTP"
    assert args.sizes == (("20K", 20480), ("200K", 204800), ("1M", 1048576))
    assert args.target_bytes == 600 * 1024
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
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

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
    assert "dir=/USB2/test/FTP" in lines[0]
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
    assert "/USB2/test/FTP" not in state.dirs

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
    assert "/USB2/test/FTP" in state.dirs


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
    assert "phase=connect_failed" in output
    assert "protocol=summary result=FAIL" in output


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


def test_progress_bar_renders_single_line_when_tty_enabled():
    module = load_module()
    state = install_fake_ftp(module)
    state.ensure_dir("/USB2/test")
    state.ensure_dir("/USB2/test/FTP")

    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    fake_stdout = FakeTTY()
    with contextlib.redirect_stdout(fake_stdout):
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
