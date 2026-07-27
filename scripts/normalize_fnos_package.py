#!/usr/bin/env python3
"""Normalize fnpack archives to deterministic, non-world-writable POSIX modes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_BYTES = 768 * 1024 * 1024
RUNTIME_EXECUTABLE = "runtime/nexus-gateway/nexus-gateway"


class NormalizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    is_directory: bool
    data: bytes = b""


def _read_entries(payload: bytes, *, mode: str, label: str) -> list[Entry]:
    entries: list[Entry] = []
    names: set[str] = set()
    total_size = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode=mode) as archive:
            for member in archive.getmembers():
                name = member.name.rstrip("/")
                path = PurePosixPath(name)
                if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
                    raise NormalizationError(f"{label} contains an unsafe path: {member.name!r}")
                if name in names:
                    raise NormalizationError(f"{label} contains a duplicate entry: {name}")
                names.add(name)
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise NormalizationError(f"{label} contains an unsupported link or device: {name}")
                if member.isdir():
                    entries.append(Entry(name=name, is_directory=True))
                    continue
                if not member.isfile():
                    raise NormalizationError(f"{label} contains an unsupported entry: {name}")
                total_size += member.size
                if member.size > MAX_FILE_BYTES or total_size > MAX_ARCHIVE_BYTES:
                    raise NormalizationError(f"{label} is unexpectedly large")
                stream = archive.extractfile(member)
                if stream is None:
                    raise NormalizationError(f"{label} file cannot be read: {name}")
                data = stream.read(MAX_FILE_BYTES + 1)
                if len(data) != member.size or len(data) > MAX_FILE_BYTES:
                    raise NormalizationError(f"{label} file size is invalid: {name}")
                entries.append(Entry(name=name, is_directory=False, data=data))
    except (OSError, tarfile.TarError) as exc:
        raise NormalizationError(f"invalid {label} archive: {exc}") from exc
    return entries


def _write_entries(entries: list[Entry], *, gzip_compressed: bool, executable) -> bytes:
    output = io.BytesIO()
    gzip_stream = None
    target = output
    if gzip_compressed:
        gzip_stream = gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0)
        target = gzip_stream
    try:
        with tarfile.open(fileobj=target, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for entry in entries:
                info = tarfile.TarInfo(entry.name)
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                if entry.is_directory:
                    info.type = tarfile.DIRTYPE
                    info.mode = 0o755
                    info.size = 0
                    archive.addfile(info)
                    continue
                info.type = tarfile.REGTYPE
                info.mode = 0o755 if executable(entry.name) else 0o644
                info.size = len(entry.data)
                archive.addfile(info, io.BytesIO(entry.data))
    finally:
        if gzip_stream is not None:
            gzip_stream.close()
    return output.getvalue()


def _normalize_app(payload: bytes) -> bytes:
    entries = _read_entries(payload, mode="r:gz", label="app.tgz")
    return _write_entries(
        entries,
        gzip_compressed=True,
        executable=lambda name: name == RUNTIME_EXECUTABLE,
    )


def _update_manifest_checksum(payload: bytes, app_payload: bytes) -> bytes:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NormalizationError("manifest is not UTF-8") from exc
    if text.startswith("\ufeff") or "\x00" in text:
        raise NormalizationError("manifest contains unsupported content")

    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    checksum_indexes = [
        index
        for index, line in enumerate(lines)
        if "=" in line and line.split("=", 1)[0].strip() == "checksum"
    ]
    if len(checksum_indexes) != 1:
        raise NormalizationError("manifest must contain exactly one checksum field")

    index = checksum_indexes[0]
    left = lines[index].split("=", 1)[0]
    digest = hashlib.md5(app_payload, usedforsecurity=False).hexdigest()
    lines[index] = f"{left}= {digest}"
    while lines and lines[-1] == "":
        lines.pop()
    return ("\n".join(lines) + "\n").encode("utf-8")


def normalize_package(package_path: Path) -> None:
    path = package_path.resolve()
    if not path.is_file() or path.is_symlink():
        raise NormalizationError(f"fnOS package is missing or unsafe: {path}")
    size = path.stat().st_size
    if size <= 0 or size > MAX_ARCHIVE_BYTES:
        raise NormalizationError("fnOS package size is invalid")

    outer_entries = _read_entries(path.read_bytes(), mode="r:*", label="FPK")
    app_entries = [entry for entry in outer_entries if not entry.is_directory and entry.name == "app.tgz"]
    if len(app_entries) != 1:
        raise NormalizationError("FPK must contain exactly one app.tgz")
    normalized_app = _normalize_app(app_entries[0].data)

    normalized: list[Entry] = []
    found_manifest = False
    for entry in outer_entries:
        if not entry.is_directory and entry.name == "app.tgz":
            normalized.append(Entry(name=entry.name, is_directory=False, data=normalized_app))
        elif not entry.is_directory and entry.name == "manifest":
            found_manifest = True
            normalized.append(
                Entry(
                    name=entry.name,
                    is_directory=False,
                    data=_update_manifest_checksum(entry.data, normalized_app),
                )
            )
        else:
            normalized.append(entry)
    if not found_manifest:
        raise NormalizationError("FPK is missing manifest")

    payload = _write_entries(
        normalized,
        gzip_compressed=True,
        executable=lambda name: name.startswith("cmd/"),
    )
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Normalize a Nexus fnOS FPK after fnpack")
    parser.add_argument("fpk", type=Path)
    args = parser.parse_args()
    try:
        normalize_package(args.fpk)
    except (NormalizationError, OSError) as exc:
        print(f"fnOS package normalization failed: {exc}", file=os.sys.stderr)
        return 1
    print(f"Normalized fnOS package permissions: {args.fpk}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
