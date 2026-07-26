#!/usr/bin/env python3
"""Apply one-shot fnOS wizard values inside Nexus' own data directory."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DATA_DIR = Path("/data")
SETUP_DIR = DATA_DIR / ".fnos-setup"
ACCOUNT_PATH = DATA_DIR / "account.json"
CONFIG_PATH = DATA_DIR / "config.json"
MAX_FIELD_BYTES = 64 * 1024
SETUP_FIELDS = {
    "mode",
    "username",
    "password",
    "hermes_api_url",
    "hermes_api_token",
}


class SetupError(RuntimeError):
    pass


def _safe_regular_file(path: Path, *, required: bool = False) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if required:
            raise SetupError(f"required Nexus setup field is missing: {path.name}")
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SetupError(f"unsafe Nexus setup file: {path.name}")
    if info.st_size > MAX_FIELD_BYTES:
        raise SetupError(f"Nexus setup field is too large: {path.name}")
    return True


def _read_field(name: str, *, required: bool = False) -> str:
    path = SETUP_DIR / name
    if not _safe_regular_file(path, required=required):
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SetupError(f"could not read Nexus setup field: {name}") from exc


def _read_json(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SetupError(f"unsafe Nexus data file: {path.name}")
    if info.st_size > 1024 * 1024:
        raise SetupError(f"Nexus data file is too large: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SetupError(f"invalid Nexus data file: {path.name}") from exc
    if not isinstance(value, dict):
        raise SetupError(f"invalid Nexus data object: {path.name}")
    return value


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise SetupError(f"refusing to replace a symbolic link: {path.name}")
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError:
            directory_fd = -1
        if directory_fd >= 0:
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return salt.hex(), digest.hex()


def _valid_account(value: dict[str, Any]) -> bool:
    username = str(value.get("username", ""))
    legacy_password = str(value.get("password", ""))
    salt = str(value.get("password_salt", ""))
    digest = str(value.get("password_hash", ""))
    return bool(username and (legacy_password or (salt and digest)))


def _is_container_local_host(hostname: str) -> bool:
    normalized = hostname.strip().lower().rstrip(".")
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _container_safe_hermes_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return value
    if not parsed.hostname or not _is_container_local_host(parsed.hostname):
        return value
    netloc = "host.docker.internal"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return parsed._replace(netloc=netloc).geturl()


def _valid_hermes_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return bool(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not _is_container_local_host(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and not any(character.isspace() or character == "\\" for character in parsed.netloc)
        and (port is None or 1 <= port <= 65535)
    )


def _account_revision(value: dict[str, Any]) -> int:
    raw_revision = value.get("revision", 1)
    if isinstance(raw_revision, int) and not isinstance(raw_revision, bool):
        revision = raw_revision
    elif isinstance(raw_revision, str) and re.fullmatch(r"[1-9][0-9]*", raw_revision.strip()):
        revision = int(raw_revision)
    else:
        raise SetupError("invalid Nexus account revision")
    if revision < 1:
        raise SetupError("invalid Nexus account revision")
    return revision


def _valid_config(value: dict[str, Any]) -> bool:
    url = str(value.get("hermes_api_url", ""))
    token = str(value.get("hermes_api_token", ""))
    secret = str(value.get("session_secret", ""))
    return bool(_valid_hermes_url(url) and token and len(secret) >= 16)


def _validate_setup_directory() -> bool:
    try:
        info = SETUP_DIR.lstat()
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SetupError("unsafe Nexus fnOS setup directory")
    for child in SETUP_DIR.iterdir():
        if child.name not in SETUP_FIELDS:
            raise SetupError(f"unexpected Nexus setup field: {child.name}")
        _safe_regular_file(child, required=True)
    return True


def _migrate_legacy_loopback_config() -> None:
    existing_config = _read_json(CONFIG_PATH)
    current_url = str(existing_config.get("hermes_api_url", "")).strip().rstrip("/")
    migrated_url = _container_safe_hermes_url(current_url)
    if not current_url or migrated_url == current_url:
        return
    updated_config = dict(existing_config)
    updated_config["hermes_api_url"] = migrated_url
    if _valid_config(updated_config):
        _atomic_json_write(CONFIG_PATH, updated_config)


def apply_pending_setup() -> None:
    _migrate_legacy_loopback_config()
    if not _validate_setup_directory():
        return

    mode = _read_field("mode", required=True).strip()
    if mode not in {"install", "config"}:
        raise SetupError("invalid Nexus fnOS setup mode")

    existing_account = _read_json(ACCOUNT_PATH)
    existing_config = _read_json(CONFIG_PATH)
    if mode == "config" and (not _valid_account(existing_account) or not _valid_config(existing_config)):
        raise SetupError("Nexus must be initialized before applying configuration changes")

    supplied_username = _read_field("username").strip()
    supplied_password = _read_field("password")
    supplied_url = _read_field("hermes_api_url").strip().rstrip("/")
    supplied_token = _read_field("hermes_api_token").strip()

    if mode == "install":
        if not supplied_username or not supplied_password or not supplied_url or not supplied_token:
            raise SetupError("the fnOS installation wizard did not provide all required values")

    username = supplied_username or str(existing_account.get("username", ""))
    if len(username) < 3 or len(username) > 48:
        raise SetupError("Nexus username must contain 3 to 48 characters")
    if supplied_password and len(supplied_password) < 8:
        raise SetupError("Nexus password must contain at least 8 characters")

    hermes_api_url = supplied_url or str(existing_config.get("hermes_api_url", "")).rstrip("/")
    hermes_api_token = supplied_token or str(existing_config.get("hermes_api_token", ""))
    if not _valid_hermes_url(hermes_api_url):
        raise SetupError("Hermes API URL must be a valid http:// or https:// address")
    if not hermes_api_token:
        raise SetupError("Hermes API Server Key is required")

    previous_revision = _account_revision(existing_account) if existing_account else 0
    account_changed = mode == "install" or supplied_username != "" or supplied_password != ""
    config_changed = mode == "install" or supplied_url != "" or supplied_token != ""

    if supplied_password:
        password_salt, password_hash = _password_record(supplied_password)
    elif existing_account.get("password_salt") and existing_account.get("password_hash"):
        password_salt = str(existing_account["password_salt"])
        password_hash = str(existing_account["password_hash"])
    elif existing_account.get("password"):
        password_salt, password_hash = _password_record(str(existing_account["password"]))
        account_changed = True
    else:
        raise SetupError("a Nexus password is required")

    session_secret = str(existing_config.get("session_secret", ""))
    if len(session_secret) < 16:
        session_secret = secrets.token_urlsafe(48)
        config_changed = True

    new_config = {
        "hermes_api_url": hermes_api_url,
        "hermes_api_token": hermes_api_token,
        "session_secret": session_secret,
    }
    new_account = {
        "username": username,
        "password_scheme": "scrypt",
        "password_salt": password_salt,
        "password_hash": password_hash,
        "revision": previous_revision + 1 if account_changed else previous_revision,
    }

    if config_changed or not _valid_config(existing_config):
        _atomic_json_write(CONFIG_PATH, new_config)
    if account_changed or not _valid_account(existing_account):
        _atomic_json_write(ACCOUNT_PATH, new_account)

    shutil.rmtree(SETUP_DIR)


def main() -> None:
    if len(sys.argv) < 2:
        raise SetupError("missing Nexus Gateway command")
    apply_pending_setup()
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(f"Nexus fnOS configuration failed: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(78)
