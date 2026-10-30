#!/usr/bin/env python3
"""Verify the published Infisical wrapper checkout before every invocation."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
from typing import NoReturn


EXPECTED_KEYS = {
    "schemaVersion", "repo", "expectedRemote", "expectedCommit", "entrypoint", "executor",
}


class VerificationError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise VerificationError(message)


def has_reparse_attribute(path: Path) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def reject_redirecting_components(path: Path) -> None:
    candidate = Path(os.path.abspath(os.fspath(path)))
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        if not current.exists() and not current.is_symlink():
            continue
        if current.is_symlink() or has_reparse_attribute(current):
            fail(f"redirecting path component is not allowed: {current}")


def git(repo: Path, *args: str, allow_empty: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(repo), *args], check=False,
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        fail(f"git provenance check failed: {exc}")
    if result.returncode != 0:
        fail(f"git provenance check failed: git {' '.join(args)}")
    output = result.stdout.strip()
    if not allow_empty and not output:
        fail(f"git provenance check returned no value: git {' '.join(args)}")
    return output


def load_config(path: Path) -> dict[str, object]:
    reject_redirecting_components(path)
    if path.is_symlink() or not path.is_file():
        fail("runtime config must be a regular non-symlink file")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"invalid runtime config: {exc}")
    if not isinstance(data, dict) or set(data) != EXPECTED_KEYS or data.get("schemaVersion") != 1:
        fail("runtime config schema is invalid")
    for key in EXPECTED_KEYS - {"schemaVersion"}:
        if not isinstance(data.get(key), str) or not data[key]:
            fail(f"runtime config field is invalid: {key}")
    return data


def safe_entrypoint(repo: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        fail("entrypoint must be a safe repository-relative path")
    entrypoint = repo.joinpath(*pure.parts)
    reject_redirecting_components(entrypoint)
    if entrypoint.is_symlink() or not entrypoint.is_file():
        fail("entrypoint must be a regular non-symlink file")
    return entrypoint


def verify(config: dict[str, object]) -> tuple[Path, Path, str]:
    repo = Path(str(config["repo"]))
    if not repo.is_absolute():
        fail("repository path must be absolute")
    reject_redirecting_components(repo)
    if repo.is_symlink() or not repo.is_dir():
        fail("repository must be a regular non-symlink directory")
    commit = str(config["expectedCommit"])
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        fail("expected commit must be 40 lowercase hexadecimal characters")
    if git(repo, "rev-parse", "--is-inside-work-tree", allow_empty=False) != "true":
        fail("configured path is not a Git worktree")
    if git(repo, "remote", "get-url", "origin", allow_empty=False) != config["expectedRemote"]:
        fail("repository origin does not match the reviewed identity")
    if git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", allow_empty=False) != "main":
        fail("repository must be on main")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        fail("repository must be clean")
    head = git(repo, "rev-parse", "HEAD", allow_empty=False)
    published = git(repo, "rev-parse", "origin/main", allow_empty=False)
    if head != commit or published != commit:
        fail("repository HEAD, origin/main, and pinned commit must match")
    relative = str(config["entrypoint"])
    entrypoint = safe_entrypoint(repo, relative)
    git(repo, "ls-files", "--error-unmatch", "--", relative, allow_empty=False)
    if git(repo, "rev-parse", "HEAD", allow_empty=False) != commit:
        fail("repository changed during provenance validation")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        fail("repository changed during provenance validation")
    return repo, entrypoint, str(config["executor"])


def execute(entrypoint: Path, executor: str, args: list[str]) -> NoReturn:
    if executor == "bash":
        command = ["bash", os.fspath(entrypoint), *args]
    elif executor == "powershell":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            fail("PowerShell is required to invoke the configured wrapper")
        command = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", os.fspath(entrypoint), *args]
    else:
        fail("unsupported wrapper executor")
    os.execvp(command[0], command)


def main(argv: list[str]) -> int:
    verify_only = bool(argv and argv[0] == "--verify-only")
    if verify_only:
        argv = argv[1:]
    if not argv:
        print("usage: infisical-wrapper-runtime.py [--verify-only] CONFIG [ARGS...]", file=sys.stderr)
        return 2
    try:
        config = load_config(Path(argv[0]))
        _, entrypoint, executor = verify(config)
        if verify_only:
            return 0
        execute(entrypoint, executor, argv[1:])
    except VerificationError as exc:
        print(f"infisical-safe-wrapper refused to run: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
