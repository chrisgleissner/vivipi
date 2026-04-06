import subprocess

from vivipi.core.version import GIT_DESCRIBE_PATTERN, _git_describe_version, _git_head_hash, resolve_version


def test_git_describe_pattern_matches_long_format_with_zero_count():
    match = GIT_DESCRIBE_PATTERN.match("v0.1.0-0-gabcdef12")
    assert match is not None
    assert match.group(1) == "0.1.0"
    assert match.group(2) == "0"
    assert match.group(3) == "abcdef12"


def test_git_describe_pattern_matches_long_format_after_tag():
    match = GIT_DESCRIBE_PATTERN.match("v1.2.3-5-g11223344")
    assert match is not None
    assert match.group(1) == "1.2.3"
    assert match.group(2) == "5"
    assert match.group(3) == "11223344"


def test_resolve_version_returns_tag_when_on_tag(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        if "describe" in args:
            return subprocess.CompletedProcess(args, 0, stdout="v0.1.0-0-gabcdef12\n")
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_version(tmp_path) == "0.1.0"


def test_resolve_version_returns_bare_tag_when_on_tag(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        if "describe" in args and "[0-9]*" in args:
            return subprocess.CompletedProcess(args, 0, stdout="0.2.0-0-gabcdef12\n")
        return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_version(tmp_path) == "0.2.0"


def test_resolve_version_appends_hash_when_ahead_of_tag(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        if "describe" in args:
            return subprocess.CompletedProcess(args, 0, stdout="v0.1.0-3-gabcdef12\n")
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_version(tmp_path) == "0.1.0-abcdef12"


def test_resolve_version_uses_fallback_and_hash_when_no_tags(monkeypatch, tmp_path):
    call_index = {"n": 0}

    def fake_run(args, **kwargs):
        call_index["n"] += 1
        if "describe" in args:
            return subprocess.CompletedProcess(args, 128, stdout="", stderr="fatal")
        if "rev-parse" in args:
            return subprocess.CompletedProcess(args, 0, stdout="aabbccdd11223344\n")
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_version(tmp_path, fallback_version="0.2.0") == "0.2.0-aabbccdd"


def test_resolve_version_returns_fallback_when_git_unavailable(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        raise FileNotFoundError("git not found")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert resolve_version(tmp_path, fallback_version="0.3.0") == "0.3.0"


def test_git_describe_version_returns_none_on_subprocess_error(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        raise subprocess.SubprocessError("boom")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _git_describe_version(tmp_path) is None


def test_git_describe_version_returns_none_on_parse_failure(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout="not-a-version\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _git_describe_version(tmp_path) is None


def test_git_describe_version_requests_eight_character_abbrev(monkeypatch, tmp_path):
    observed = {}

    def fake_run(args, **kwargs):
        observed["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout="0.2.0-0-gabcdef12\n")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _git_describe_version(tmp_path) == "0.2.0"
    assert "--abbrev=8" in observed["args"]


def test_git_head_hash_returns_none_on_error(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        raise OSError("no git")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _git_head_hash(tmp_path) is None


def test_git_head_hash_returns_none_on_nonzero_exit(monkeypatch, tmp_path):
    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(args, 128, stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert _git_head_hash(tmp_path) is None
