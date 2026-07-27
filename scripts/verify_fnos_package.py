from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import struct
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "fnos" / "nexus-gateway"
LAUNCHER_SOURCE = ROOT / "gateway" / "nexus_gateway" / "fnos_launcher.py"

OUTER_FILES = {
    "app.tgz",
    "LICENSE",
    "ICON.PNG",
    "ICON_256.PNG",
    "manifest",
    "cmd/config_callback",
    "cmd/config_init",
    "cmd/install_callback",
    "cmd/install_init",
    "cmd/main",
    "cmd/setup_common.sh",
    "cmd/uninstall_callback",
    "cmd/uninstall_init",
    "cmd/upgrade_callback",
    "cmd/upgrade_init",
    "config/privilege",
    "config/resource",
    "wizard/config",
    "wizard/install",
}
STATIC_APP_FILES = {
    "ui/config",
    "ui/images/icon_64.png",
    "ui/images/icon_256.png",
    "config/privilege",
    "config/resource",
}
RUNTIME_PREFIX = "runtime/"
RUNTIME_PLATFORM_PATH = "runtime/runtime.platform"
RUNTIME_CHECKSUM_PATH = "runtime/runtime.sha256"
RUNTIME_EXECUTABLE_PATH = "runtime/nexus-gateway/nexus-gateway"
RUNTIME_CA_BUNDLE_PATH = "runtime/ca-certificates.crt"
RUNTIME_WEB_FILES = {
    "runtime/nexus-gateway/_internal/nexus_gateway/web/index.html",
    "runtime/nexus-gateway/_internal/nexus_gateway/web/app.js",
    "runtime/nexus-gateway/_internal/nexus_gateway/web/styles.css",
}
FORBIDDEN_NAMES = {".ds_store", "account.json", "config.json", "bootstrap.token", ".env"}
TEXT_SUFFIXES = {"", ".json", ".py", ".sh", ".yaml", ".yml", ".txt", ".sha256", ".platform"}
ARCHITECTURES = {
    "amd64": {"manifest": "x86", "platform": "linux/amd64", "elf_machine": 62},
    "arm64": {"manifest": "arm", "platform": "linux/arm64", "elf_machine": 183},
}


class VerificationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ArchiveEntry:
    data: bytes
    mode: int


def _fail(message: str) -> None:
    raise VerificationError(message)


def _read_manifest(data: bytes) -> dict[str, str]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("manifest is not UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            _fail(f"invalid manifest line: {raw_line!r}")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _safe_archive_entries(archive: tarfile.TarFile, label: str) -> dict[str, ArchiveEntry]:
    files: dict[str, ArchiveEntry] = {}
    total_size = 0
    for member in archive.getmembers():
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\\" in name:
            _fail(f"{label} contains an unsafe path: {member.name!r}")
        if member.issym() or member.islnk() or member.isdev():
            _fail(f"{label} contains a link or device: {name}")
        if member.isdir():
            continue
        if not member.isfile():
            _fail(f"{label} contains an unsupported entry: {name}")
        if name in files:
            _fail(f"{label} contains a duplicate file: {name}")
        if member.mode & 0o6000 or member.mode & 0o002:
            _fail(f"{label} contains unsafe permissions: {name}")
        total_size += member.size
        if member.size > 256 * 1024 * 1024 or total_size > 512 * 1024 * 1024:
            _fail(f"{label} is unexpectedly large")
        stream = archive.extractfile(member)
        if stream is None:
            _fail(f"{label} file cannot be read: {name}")
        files[name] = ArchiveEntry(stream.read(), member.mode & 0o7777)
    return files


def _assert_expected_files(actual: set[str], expected: set[str], label: str) -> None:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing or extra:
        _fail(f"{label} file set mismatch; missing={missing}, extra={extra}")


def _assert_clean_names(names: set[str], label: str) -> None:
    for name in names:
        lowered_parts = {part.casefold() for part in PurePosixPath(name).parts}
        if lowered_parts & FORBIDDEN_NAMES:
            _fail(f"{label} contains a forbidden file: {name}")
        if any(part.startswith(".") and part not in {"."} for part in PurePosixPath(name).parts):
            _fail(f"{label} contains a hidden file: {name}")


def _assert_clean_text(name: str, data: bytes, *, allow_crlf: bool = False) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        _fail(f"{name} contains a UTF-8 BOM")
    if b"\r" in data:
        if not allow_crlf or data.replace(b"\r\n", b"").find(b"\r") >= 0:
            _fail(f"{name} has invalid line endings")
        data = data.replace(b"\r\n", b"\n")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{name} is not UTF-8") from exc
    if "\x00" in text or "\x0c" in text or "???" in text:
        _fail(f"{name} contains invalid or corrupted text")
    return text


def _png_size(data: bytes, name: str) -> tuple[int, int]:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24:
        _fail(f"{name} is not a valid PNG")
    return struct.unpack(">II", data[16:24])


def _source_bytes(path: str, *, in_app: bool = False) -> bytes:
    base = PACKAGE_SOURCE / "app" if in_app else PACKAGE_SOURCE
    return (base / path).read_bytes()


def _verify_checksum_file(fpk_path: Path, digest: str, checksum_path: Path) -> None:
    try:
        lines = checksum_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise VerificationError(f"checksum file is not UTF-8: {checksum_path}") from exc

    entries: dict[str, str] = {}
    for raw_line in lines:
        if not raw_line:
            continue
        match = re.fullmatch(r"([0-9A-Fa-f]{64})  ([^\x2f\x5c]+)", raw_line)
        if not match:
            _fail(f"invalid checksum line in {checksum_path.name}")
        checksum, name = match.groups()
        if name in entries:
            _fail(f"duplicate checksum entry in {checksum_path.name}: {name}")
        entries[name] = checksum.lower()

    expected = entries.get(fpk_path.name)
    if expected is None:
        _fail(f"{checksum_path.name} does not contain {fpk_path.name}")
    if expected != digest:
        _fail(f"{checksum_path.name} does not match the package")


def _resolve_checksum_file(fpk_path: Path, requested: Path | None) -> Path | None:
    if requested is not None:
        if not requested.is_file():
            _fail(f"checksum file not found: {requested}")
        return requested
    unified = fpk_path.parent / "SHA256SUMS.txt"
    if unified.is_file():
        return unified
    legacy = Path(f"{fpk_path}.sha256")
    return legacy if legacy.is_file() else None


def _package_architecture(fpk_path: Path, package_version: str) -> tuple[str, dict[str, str | int]]:
    match = re.fullmatch(
        rf"Nexus-fnOS-{re.escape(package_version)}-(amd64|arm64)\.fpk",
        fpk_path.name,
    )
    if not match:
        _fail("fnOS package filename does not include a supported architecture")
    architecture = match.group(1)
    return architecture, ARCHITECTURES[architecture]


def _parse_runtime_checksums(text: str) -> dict[str, str]:
    if not text.endswith("\n") or "\r" in text:
        _fail("runtime checksum manifest must use LF line endings and end with a newline")
    entries: dict[str, str] = {}
    order: list[str] = []
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        if not match:
            _fail("runtime checksum manifest contains an invalid line")
        digest, name = match.groups()
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name or name == "runtime.sha256":
            _fail("runtime checksum manifest contains an unsafe path")
        if name in entries:
            _fail("runtime checksum manifest contains a duplicate path")
        entries[name] = digest
        order.append(name)
    if not entries:
        _fail("runtime checksum manifest is empty")
    if order != sorted(order):
        _fail("runtime checksum manifest is not byte-order sorted")
    return entries


def _verify_runtime(app: dict[str, ArchiveEntry], architecture_metadata: dict[str, str | int]) -> None:
    runtime_names = {name for name in app if name.startswith(RUNTIME_PREFIX)}
    required = {
        RUNTIME_PLATFORM_PATH,
        RUNTIME_CHECKSUM_PATH,
        RUNTIME_EXECUTABLE_PATH,
        RUNTIME_CA_BUNDLE_PATH,
        *RUNTIME_WEB_FILES,
    }
    missing = sorted(required - runtime_names)
    if missing:
        _fail(f"app.tgz native runtime is incomplete; missing={missing}")
    if any(name.startswith("runtime/docker/") for name in runtime_names):
        _fail("app.tgz contains a container runtime")

    platform_text = _assert_clean_text(
        f"app.tgz/{RUNTIME_PLATFORM_PATH}", app[RUNTIME_PLATFORM_PATH].data
    )
    expected_platform = str(architecture_metadata["platform"])
    if platform_text != f"{expected_platform}\n":
        _fail("native runtime platform marker does not match the FPK architecture")

    checksum_text = _assert_clean_text(
        f"app.tgz/{RUNTIME_CHECKSUM_PATH}", app[RUNTIME_CHECKSUM_PATH].data
    )
    checksums = _parse_runtime_checksums(checksum_text)
    actual_relative_names = {
        name.removeprefix(RUNTIME_PREFIX)
        for name in runtime_names
        if name != RUNTIME_CHECKSUM_PATH
    }
    if set(checksums) != actual_relative_names:
        _fail(
            "native runtime checksum file set mismatch; "
            f"missing={sorted(actual_relative_names - set(checksums))}, "
            f"extra={sorted(set(checksums) - actual_relative_names)}"
        )
    for relative, expected_digest in checksums.items():
        actual_digest = hashlib.sha256(app[f"{RUNTIME_PREFIX}{relative}"].data).hexdigest()
        if actual_digest != expected_digest:
            _fail(f"native runtime checksum mismatch: {relative}")

    executable = app[RUNTIME_EXECUTABLE_PATH]
    if executable.mode & 0o111 == 0:
        _fail("native Gateway executable is not executable")
    data = executable.data
    if len(data) < 20 or data[:4] != b"\x7fELF" or data[4:6] != b"\x02\x01":
        _fail("native Gateway executable is not a 64-bit little-endian ELF binary")
    machine = struct.unpack_from("<H", data, 18)[0]
    if machine != architecture_metadata["elf_machine"]:
        _fail("native Gateway ELF architecture does not match the FPK architecture")

    ca_bundle = app[RUNTIME_CA_BUNDLE_PATH].data
    if len(ca_bundle) < 1024 or b"-----BEGIN CERTIFICATE-----" not in ca_bundle:
        _fail("native runtime CA bundle is invalid")


def verify_package(fpk_path: Path, sha256_path: Path | None = None) -> str:
    if not fpk_path.is_file():
        _fail(f"FPK not found: {fpk_path}")

    gateway_source = (ROOT / "gateway" / "nexus_gateway" / "__init__.py").read_text(encoding="utf-8")
    gateway_match = re.search(r'__version__\s*=\s*"([^"]+)"', gateway_source)
    if not gateway_match:
        _fail("could not read the Gateway version")
    gateway_version = gateway_match.group(1)
    source_manifest = _read_manifest((PACKAGE_SOURCE / "manifest").read_bytes())
    package_version = source_manifest.get("version", "")
    if not re.fullmatch(rf"{re.escape(gateway_version)}-fnos[1-9][0-9]*", package_version):
        _fail("fnOS package version does not match the Gateway version")
    if source_manifest.get("platform") != "all":
        _fail("fnOS source manifest must be an architecture-neutral build template")
    manifest_urls = " ".join(
        source_manifest.get(field, "") for field in ("maintainer_url", "distributor_url")
    )
    if re.search(r"(?i)github\.com|ghcr\.io", manifest_urls):
        _fail("fnOS package metadata must not depend on GitHub or GHCR")
    architecture, architecture_metadata = _package_architecture(fpk_path, package_version)

    try:
        with tarfile.open(fpk_path, mode="r:*") as outer_archive:
            outer = _safe_archive_entries(outer_archive, "FPK")
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError(f"invalid FPK archive: {exc}") from exc

    _assert_expected_files(set(outer), OUTER_FILES, "FPK")
    _assert_clean_names(set(outer), "FPK")

    try:
        with tarfile.open(fileobj=io.BytesIO(outer["app.tgz"].data), mode="r:gz") as app_archive:
            app = _safe_archive_entries(app_archive, "app.tgz")
    except tarfile.TarError as exc:
        raise VerificationError(f"invalid app.tgz archive: {exc}") from exc

    app_names = set(app)
    if not STATIC_APP_FILES <= app_names:
        _fail(f"app.tgz is missing static files: {sorted(STATIC_APP_FILES - app_names)}")
    unexpected = sorted(name for name in app_names if name not in STATIC_APP_FILES and not name.startswith(RUNTIME_PREFIX))
    if unexpected:
        _fail(f"app.tgz contains unexpected files: {unexpected}")
    _assert_clean_names(app_names, "app.tgz")

    for name in OUTER_FILES - {"app.tgz", "manifest"}:
        if outer[name].data != _source_bytes(name):
            _fail(f"FPK content differs from package source: {name}")
    for name in STATIC_APP_FILES:
        expected = _source_bytes(name, in_app=True) if not name.startswith("config/") else _source_bytes(name)
        if app[name].data != expected:
            _fail(f"app.tgz content differs from package source: {name}")

    generated_manifest = _read_manifest(outer["manifest"].data)
    expected_manifest_keys = set(source_manifest) | {"checksum"}
    if set(generated_manifest) != expected_manifest_keys:
        _fail("generated manifest contains an unexpected field set")
    for key, value in source_manifest.items():
        if key == "platform":
            continue
        if generated_manifest.get(key) != value:
            _fail(f"generated manifest changed {key}")
    if generated_manifest.get("platform") != architecture_metadata["manifest"]:
        _fail(f"generated manifest platform does not match {architecture}")
    generated_checksum = generated_manifest.get("checksum", "")
    if not re.fullmatch(r"[0-9a-f]{32}", generated_checksum):
        _fail("generated manifest checksum is missing or invalid")
    expected_app_checksum = hashlib.md5(outer["app.tgz"].data, usedforsecurity=False).hexdigest()
    if generated_checksum != expected_app_checksum:
        _fail("generated manifest checksum does not match app.tgz")
    if source_manifest.get("service_port") != "8787":
        _fail("fnOS package must publish the standard Nexus port 8787")

    combined_text: dict[str, str] = {}
    for name, entry in outer.items():
        if name == "app.tgz" or PurePosixPath(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        combined_text[f"FPK/{name}"] = _assert_clean_text(
            f"FPK/{name}", entry.data, allow_crlf=name == "manifest"
        )
    for name in STATIC_APP_FILES:
        if PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES:
            combined_text[f"app.tgz/{name}"] = _assert_clean_text(f"app.tgz/{name}", app[name].data)

    for name in OUTER_FILES:
        if name.startswith("cmd/") and outer[name].mode & 0o111 == 0:
            _fail(f"lifecycle script is not executable: {name}")

    resource = json.loads(combined_text["FPK/config/resource"])
    if resource != {}:
        _fail("fnOS native package must use an empty resource declaration")
    privilege = json.loads(combined_text["FPK/config/privilege"])
    if privilege.get("defaults", {}).get("run-as") != "package":
        _fail("fnOS lifecycle must run as the package user")

    scripts = "\n".join(text for name, text in combined_text.items() if name.startswith("FPK/cmd/"))
    forbidden_script_patterns = [
        r"\bdocker\b",
        r"\b(?:curl|wget|git)\b",
        r"https?://[A-Za-z0-9]",
        r"/dev/tcp",
        r"\bnc\s",
    ]
    for pattern in forbidden_script_patterns:
        if re.search(pattern, scripts, re.IGNORECASE):
            _fail("lifecycle scripts contain a container or remote network operation")

    install_init = combined_text["FPK/cmd/install_init"]
    upgrade_init = combined_text["FPK/cmd/upgrade_init"]
    if "validate_packaged_runtime" not in install_init or "validate_packaged_runtime" not in upgrade_init:
        _fail("install and upgrade lifecycle scripts do not verify the packaged native runtime")
    config_callback = combined_text["FPK/cmd/config_callback"]
    if '"$SCRIPT_DIR/main" stop' not in config_callback or '"$SCRIPT_DIR/main" start' not in config_callback:
        _fail("configuration callback does not restart the native Gateway")
    main_script = combined_text["FPK/cmd/main"]
    for fragment in (
        '$TRIM_APPDEST/runtime',
        "/proc/$pid/exe",
        "/proc/$pid/stat",
        'kill -TERM "$pid"',
        'kill -KILL "$pid"',
        "NEXUS_FNOS_DATA_DIR",
        "NEXUS_DEPLOYMENT_MODE",
        "SSL_CERT_FILE",
    ):
        if fragment not in main_script:
            _fail(f"native process manager is missing required safety behavior: {fragment}")

    hermes_word = "her" + "mes"
    forbidden_lifecycle_patterns = [
        rf"systemctl\s+(?:start|stop|restart).*{hermes_word}",
        rf"(?:start|stop|restart|kill).*{hermes_word}",
        rf"/opt/{hermes_word}",
        rf"/var/lib/{hermes_word}",
    ]
    for pattern in forbidden_lifecycle_patterns:
        if re.search(pattern, scripts, re.IGNORECASE):
            _fail("lifecycle scripts attempt to manage Hermes")

    launcher = LAUNCHER_SOURCE.read_text(encoding="utf-8")
    for fragment in (
        "hashlib.scrypt",
        "secrets.token_urlsafe",
        'os.getenv("NEXUS_FNOS_DATA_DIR"',
        'os.getenv("NEXUS_CONFIG_FILE"',
        'os.getenv("NEXUS_CREDENTIALS_FILE"',
        "from nexus_gateway.__main__ import main as gateway_main",
    ):
        if fragment not in launcher:
            _fail(f"fnOS launcher is missing required behavior: {fragment}")
    if "execvp" in launcher or "subprocess" in launcher:
        _fail("fnOS launcher must invoke the Gateway in-process")

    _verify_runtime(app, architecture_metadata)

    ui = json.loads(combined_text["app.tgz/ui/config"])
    entry = ui[".url"][source_manifest["desktop_applaunchname"]]
    if entry.get("type") != "url" or entry.get("protocol") != "http" or entry.get("port") != source_manifest["service_port"]:
        _fail("desktop entry does not match the HTTP service manifest")

    expected_sizes = {
        "FPK/ICON.PNG": (64, 64),
        "FPK/ICON_256.PNG": (256, 256),
        "app.tgz/ui/images/icon_64.png": (64, 64),
        "app.tgz/ui/images/icon_256.png": (256, 256),
    }
    binary_lookup = {
        **{f"FPK/{key}": value.data for key, value in outer.items()},
        **{f"app.tgz/{key}": value.data for key, value in app.items()},
    }
    for name, size in expected_sizes.items():
        if _png_size(binary_lookup[name], name) != size:
            _fail(f"{name} has the wrong dimensions")

    license_text = combined_text["FPK/LICENSE"]
    if "Apache License" not in license_text or (ROOT / "NOTICE").read_text(encoding="utf-8").strip() not in license_text:
        _fail("FPK LICENSE does not include the repository license and NOTICE")

    digest = hashlib.sha256(fpk_path.read_bytes()).hexdigest()
    checksum_file = _resolve_checksum_file(fpk_path, sha256_path)
    if checksum_file is not None:
        _verify_checksum_file(fpk_path, digest, checksum_file)

    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify a self-contained native Nexus fnOS FPK without extracting it")
    parser.add_argument("fpk", type=Path)
    parser.add_argument("--sha256-file", type=Path, help="SHA256SUMS.txt or a legacy single-artifact checksum file")
    args = parser.parse_args()
    try:
        digest = verify_package(args.fpk.resolve(), args.sha256_file.resolve() if args.sha256_file else None)
    except (VerificationError, json.JSONDecodeError, KeyError, OSError) as exc:
        print(f"fnOS package verification failed: {exc}", file=sys.stderr)
        return 1
    print(f"fnOS package verification passed: {args.fpk} ({digest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
