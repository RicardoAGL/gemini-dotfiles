#!/usr/bin/env python3
"""Strictly install and attest the Antigravity governance plugin."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


PLUGIN_NAME = "claude-governance"
COMMAND_TIMEOUT_SECONDS = 180


class GovernanceInstallError(RuntimeError):
    """Raised when governance cannot be proven enabled and current."""


def _load_source_manifest(source: Path) -> dict[str, Any]:
    source = source.absolute()
    if source.is_symlink() or not source.is_dir():
        raise GovernanceInstallError(f"Plugin source must be a regular directory: {source}")
    manifest_path = source / "plugin.json"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise GovernanceInstallError("Plugin source manifest is missing or symlinked")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceInstallError(f"Cannot read plugin source manifest: {error}") from error
    if not isinstance(manifest, dict):
        raise GovernanceInstallError("Plugin source manifest must be a JSON object")
    if manifest.get("name") != PLUGIN_NAME:
        raise GovernanceInstallError(f"Plugin source name must be {PLUGIN_NAME}")
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        raise GovernanceInstallError("Plugin source version must be a non-empty string")
    return manifest


def _run_checked(
    runner: Callable[..., Any], command: list[str]
) -> Any:
    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise GovernanceInstallError(f"Command could not run: {command[0]}: {error}") from error
    if not isinstance(getattr(result, "returncode", None), int) or result.returncode != 0:
        stderr = getattr(result, "stderr", "")
        detail = stderr.strip() if isinstance(stderr, str) else ""
        raise GovernanceInstallError(
            f"Command failed ({getattr(result, 'returncode', 'unknown')}): {' '.join(command)}"
            + (f": {detail}" if detail else "")
        )
    return result


def _load_plugin_entries(result: Any) -> list[dict[str, Any]]:
    stdout = getattr(result, "stdout", None)
    if not isinstance(stdout, str) or not stdout.strip():
        raise GovernanceInstallError("agy plugin list returned no JSON")
    try:
        entries = json.loads(stdout)
    except json.JSONDecodeError as error:
        raise GovernanceInstallError("agy plugin list returned malformed JSON") from error
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise GovernanceInstallError("agy plugin list JSON must be an array of objects")
    return entries


def _regular_tree_files(root_path: Path, label: str) -> list[Path]:
    files: list[Path] = []

    def walk_failed(error: OSError) -> None:
        raise GovernanceInstallError(f"Cannot inspect {label}: {error}") from error

    for root, directories, names in os.walk(
        root_path, followlinks=False, onerror=walk_failed
    ):
        current_root = Path(root)
        for directory in directories:
            if (current_root / directory).is_symlink():
                raise GovernanceInstallError(f"{label} must not contain symlinked directories")
        for name in names:
            candidate = current_root / name
            if candidate.is_symlink() or not candidate.is_file():
                raise GovernanceInstallError(f"{label} must contain only regular files")
            files.append(candidate.relative_to(root_path))
    return sorted(files)


def verify_current_plugin(source: Path, entries: Sequence[dict[str, Any]]) -> Path:
    """Prove the active installed plugin is enabled and byte-current."""

    source = Path(source).absolute()
    manifest = _load_source_manifest(source)
    matches = [entry for entry in entries if entry.get("name") == PLUGIN_NAME]
    if len(matches) != 1:
        raise GovernanceInstallError("Exactly one installed governance plugin is required")
    entry = matches[0]
    if entry.get("enabled") is not True:
        raise GovernanceInstallError("Governance plugin is not enabled")
    if entry.get("version") != manifest["version"]:
        raise GovernanceInstallError("Installed governance plugin version is stale")
    hooks = entry.get("hooks")
    if not isinstance(hooks, list) or PLUGIN_NAME not in hooks:
        raise GovernanceInstallError("Installed governance plugin does not expose its required hook")

    installed_raw = entry.get("path")
    if not isinstance(installed_raw, str) or not installed_raw:
        raise GovernanceInstallError("Installed governance plugin path is missing")
    installed = Path(installed_raw).absolute()
    if installed.is_symlink() or not installed.is_dir():
        raise GovernanceInstallError("Installed governance plugin path is not a regular directory")

    source_files = _regular_tree_files(source, "Plugin source")
    installed_files = _regular_tree_files(installed, "Installed plugin")
    if installed_files != source_files:
        raise GovernanceInstallError("Installed governance plugin file set is stale")

    for relative in source_files:
        source_file = source / relative
        installed_file = installed / relative
        if installed_file.is_symlink() or not installed_file.is_file():
            raise GovernanceInstallError(f"Installed governance file is missing or symlinked: {relative}")
        try:
            if installed_file.read_bytes() != source_file.read_bytes():
                raise GovernanceInstallError(f"Installed governance file is stale: {relative}")
        except OSError as error:
            raise GovernanceInstallError(f"Cannot attest installed governance file {relative}: {error}") from error
    return installed


def _list_and_verify(agy: str, source: Path, runner: Callable[..., Any]) -> Path:
    listed = _run_checked(runner, [agy, "plugin", "list", "--json"])
    return verify_current_plugin(source, _load_plugin_entries(listed))


def install_and_verify(
    agy: str,
    source: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> Path:
    source = Path(source).absolute()
    _load_source_manifest(source)
    _run_checked(runner, [agy, "plugin", "validate", "--strict", str(source)])
    _run_checked(runner, [agy, "plugin", "install", str(source)])
    _run_checked(runner, [agy, "plugin", "enable", PLUGIN_NAME])
    return _list_and_verify(agy, source, runner)


def check_only(
    agy: str,
    source: Path,
    *,
    runner: Callable[..., Any] = subprocess.run,
) -> Path:
    source = Path(source).absolute()
    _load_source_manifest(source)
    return _list_and_verify(agy, source, runner)


def main(argv: Sequence[str] | None = None) -> int:
    default_source = Path(__file__).resolve().parents[1] / "plugins" / PLUGIN_NAME
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agy", help="agy executable (defaults to PATH discovery)")
    parser.add_argument("--plugin-dir", type=Path, default=default_source)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)

    agy = args.agy or shutil.which("agy")
    if not agy:
        raise GovernanceInstallError("agy is required to install or verify governance")
    operation = check_only if args.check_only else install_and_verify
    installed = operation(agy, args.plugin_dir)
    print(f"Verified enabled {PLUGIN_NAME} {installed}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GovernanceInstallError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
