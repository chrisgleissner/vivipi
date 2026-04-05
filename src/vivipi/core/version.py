from __future__ import annotations

import re
import subprocess
from pathlib import Path

GIT_DESCRIBE_PATTERN = re.compile(r"^v?(.+)-(\d+)-g([0-9a-f]+)$")


def resolve_version(repo_root: str | Path, fallback_version: str = "0.0.0") -> str:
    root = Path(repo_root)
    tag_version = _git_describe_version(root)
    if tag_version is not None:
        return tag_version
    commit_hash = _git_head_hash(root)
    base_version = fallback_version
    if commit_hash:
        return f"{base_version}-{commit_hash[:8]}"
    return base_version


def _git_describe_version(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--long", "--match", "v*"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        output = result.stdout.strip()
        match = GIT_DESCRIBE_PATTERN.match(output)
        if match is None:
            return None
        tag = match.group(1)
        count = int(match.group(2))
        commit_hash = match.group(3)
        if count == 0:
            return tag
        return f"{tag}-{commit_hash[:8]}"
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None


def _git_head_hash(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:8]
        return None
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
