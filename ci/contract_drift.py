#!/usr/bin/env python3
"""Check the contract tree manifest and its local pin without external access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

TREE_HASH_ALGO = "sha256"
SHA256_RE = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def contract_files(contract_root: Path) -> tuple[dict[str, Path], list[str]]:
    files: dict[str, Path] = {}
    errors: list[str] = []
    if not contract_root.is_dir():
        return files, [f"missing contract directory: {contract_root}"]
    for candidate in sorted(contract_root.rglob("*")):
        if candidate.is_symlink():
            errors.append(f"symbolic links are not allowed: {candidate.relative_to(contract_root)}")
            continue
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(contract_root).as_posix()
        if relative == "MANIFEST.sha256":
            continue
        files[relative] = candidate
    return files, errors


def parse_manifest(data: bytes) -> tuple[dict[str, str], bytes, list[str]]:
    entries: dict[str, str] = {}
    errors: list[str] = []
    if b"\r" in data:
        errors.append("MANIFEST.sha256 must use LF line endings")
    if data and not data.endswith(b"\n"):
        errors.append("MANIFEST.sha256 must end with LF")
    previous: str | None = None
    for line_number, line in enumerate(data.splitlines(), 1):
        if line.count(b"\0") != 1:
            errors.append(f"manifest line {line_number} must contain exactly one NUL separator")
            continue
        raw_path, raw_hash = line.split(b"\0", 1)
        try:
            relative = raw_path.decode("utf-8")
            digest = raw_hash.decode("ascii")
        except UnicodeError as error:
            errors.append(f"manifest line {line_number} has invalid encoding: {error}")
            continue
        normalized = PurePosixPath(relative)
        if (
            not relative
            or normalized.is_absolute()
            or ".." in normalized.parts
            or "\\" in relative
            or normalized.as_posix() != relative
            or relative == "MANIFEST.sha256"
        ):
            errors.append(f"manifest line {line_number} has an invalid path: {relative!r}")
        if not SHA256_RE.fullmatch(digest):
            errors.append(f"manifest line {line_number} has an invalid sha256")
        if relative in entries:
            errors.append(f"manifest path is duplicated: {relative}")
        if previous is not None and relative <= previous:
            errors.append("manifest paths must be strictly lexicographically sorted")
        entries[relative] = digest
        previous = relative
    canonical = b"".join(
        relative.encode("utf-8") + b"\0" + entries[relative].encode("ascii") + b"\n"
        for relative in sorted(entries)
        if SHA256_RE.fullmatch(entries[relative])
    )
    if data != canonical:
        errors.append("MANIFEST.sha256 bytes are not the canonical preimage")
    return entries, canonical, errors


def read_pin(path: Path) -> tuple[dict[str, Any], list[str]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {}, [f"cannot parse {path}: {error}"]
    if not isinstance(value, dict):
        return {}, [f"{path} must contain a JSON object"]
    return value, []


def validate(root: Path) -> list[str]:
    contract_root = root / "contract"
    manifest_path = contract_root / "MANIFEST.sha256"
    pin_path = root / "ci/contract.pin"
    errors: list[str] = []
    files, file_errors = contract_files(contract_root)
    errors.extend(file_errors)
    try:
        manifest_data = manifest_path.read_bytes()
    except OSError as error:
        return errors + [f"cannot read {manifest_path}: {error}"]
    entries, canonical, manifest_errors = parse_manifest(manifest_data)
    errors.extend(manifest_errors)
    actual_paths = set(files)
    manifest_paths = set(entries)
    for missing in sorted(manifest_paths - actual_paths):
        errors.append(f"manifest entry has no file: {missing}")
    for additional in sorted(actual_paths - manifest_paths):
        errors.append(f"contract file is not in the manifest: {additional}")
    for relative in sorted(actual_paths & manifest_paths):
        actual_hash = sha256_file(files[relative])
        if actual_hash != entries[relative]:
            errors.append(f"contract file hash differs: {relative}")
    pin, pin_errors = read_pin(pin_path)
    errors.extend(pin_errors)
    if pin:
        if pin.get("treeHashAlgo") != TREE_HASH_ALGO:
            errors.append(f"contract.pin treeHashAlgo must be {TREE_HASH_ALGO}")
        expected_tree_hash = hashlib.sha256(canonical).hexdigest()
        if pin.get("contractTreeSha256") != expected_tree_hash:
            errors.append("contract.pin contractTreeSha256 does not match MANIFEST.sha256 bytes")
        source_commit = pin.get("gyroxCommitSha")
        if not isinstance(source_commit, str) or not source_commit:
            errors.append("contract.pin gyroxCommitSha must be a non-empty string")
    return errors


def self_test(root: Path) -> list[str]:
    failures: list[str] = []
    cases = ("add", "delete", "modify")
    for case in cases:
        with tempfile.TemporaryDirectory(prefix=f"contract-drift-{case}-") as temporary:
            fixture_root = Path(temporary)
            shutil.copytree(root / "contract", fixture_root / "contract")
            (fixture_root / "ci").mkdir()
            shutil.copy2(root / "ci/contract.pin", fixture_root / "ci/contract.pin")
            files, _ = contract_files(fixture_root / "contract")
            if case == "add":
                (fixture_root / "contract/unlisted.fixture").write_bytes(b"added")
            elif case == "delete":
                files[sorted(files)[0]].unlink()
            else:
                target = files[sorted(files)[0]]
                target.write_bytes(target.read_bytes() + b"changed")
            rejected = bool(validate(fixture_root))
            print(f"[contract-drift self-test] {case}: {'rejected' if rejected else 'NOT REJECTED'}")
            if not rejected:
                failures.append(f"self-test did not reject {case}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    errors = validate(root)
    if not errors and args.self_test:
        errors.extend(self_test(root))
    for error in errors:
        print(f"ERROR {error}", file=sys.stderr)
    print(f"[contract-drift] errors={len(errors)} pass={not errors}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
