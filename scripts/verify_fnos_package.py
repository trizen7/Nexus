from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import struct
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SOURCE = ROOT / "fnos" / "nexus-gateway"

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
    "docker/docker-compose.yaml",
    "docker/fnos_entrypoint.py",
    "ui/config",
    "ui/images/icon_64.png",
    "ui/images/icon_256.png",
    "config/privilege",
    "config/resource",
}
GENERATED_IMAGE_FILES = {
    "docker/nexus-gateway-image.tar.gz",
    "docker/nexus-gateway-image.sha256",
    "docker/nexus-gateway-image.platform",
}
APP_FILES = STATIC_APP_FILES | GENERATED_IMAGE_FILES
FORBIDDEN_NAMES = {".ds_store", "account.json", "config.json", "bootstrap.token", ".env"}
TEXT_SUFFIXES = {"", ".json", ".py", ".sh", ".yaml", ".yml", ".txt", ".sha256", ".platform"}
IMAGE_ARCHIVE_NAME = "nexus-gateway-image.tar.gz"
IMAGE_CHECKSUM_NAME = "nexus-gateway-image.sha256"
IMAGE_PLATFORM_NAME = "nexus-gateway-image.platform"
ARCHITECTURES = {
    "amd64": {"manifest": "x86", "docker": "amd64", "platform": "linux/amd64"},
    "arm64": {"manifest": "arm", "docker": "arm64", "platform": "linux/arm64"},
}


class VerificationError(RuntimeError):
    pass


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


def _safe_archive_files(archive: tarfile.TarFile, label: str) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
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
        stream = archive.extractfile(member)
        if stream is None:
            _fail(f"{label} file cannot be read: {name}")
        files[name] = stream.read()
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


def _safe_docker_member_name(name: str, label: str) -> str:
    normalized = name.rstrip("/")
    path = PurePosixPath(normalized)
    if not normalized or path.is_absolute() or ".." in path.parts or "\\" in normalized:
        _fail(f"Docker image archive contains an unsafe {label}: {name!r}")
    return normalized


def _read_small_tar_member(archive: tarfile.TarFile, member: tarfile.TarInfo, label: str, limit: int = 2 * 1024 * 1024) -> bytes:
    if member.size > limit:
        _fail(f"Docker image archive {label} is unexpectedly large")
    stream = archive.extractfile(member)
    if stream is None:
        _fail(f"Docker image archive {label} cannot be read")
    return stream.read()


def _verify_docker_archive(data: bytes, expected_image: str, expected_architecture: str) -> None:
    if not data.startswith(b"\x1f\x8b"):
        _fail("packaged Docker image archive is not gzip-compressed")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            for member in archive.getmembers():
                name = _safe_docker_member_name(member.name, "path")
                if member.issym() or member.islnk() or member.isdev():
                    _fail(f"Docker image archive contains a link or device: {name}")
                if member.isdir():
                    continue
                if not member.isfile():
                    _fail(f"Docker image archive contains an unsupported entry: {name}")
                if name in members:
                    _fail(f"Docker image archive contains a duplicate file: {name}")
                members[name] = member

            manifest_member = members.get("manifest.json")
            if manifest_member is None:
                _fail("Docker image archive is missing manifest.json")
            manifest = json.loads(_read_small_tar_member(archive, manifest_member, "manifest.json"))
            if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
                _fail("Docker image archive must contain exactly one image")
            image_manifest = manifest[0]
            repo_tags = image_manifest.get("RepoTags")
            if repo_tags != [expected_image]:
                _fail(f"Docker image archive tag must be exactly {expected_image}")

            config_name = image_manifest.get("Config")
            if not isinstance(config_name, str):
                _fail("Docker image archive does not identify an image config")
            config_name = _safe_docker_member_name(config_name, "config path")
            config_member = members.get(config_name)
            if config_member is None:
                _fail("Docker image archive is missing its image config")
            config = json.loads(_read_small_tar_member(archive, config_member, "image config"))
            if not isinstance(config, dict):
                _fail("Docker image config is invalid")
            if config.get("os") != "linux" or config.get("architecture") != expected_architecture:
                _fail(f"Docker image must target linux/{expected_architecture}")

            layers = image_manifest.get("Layers")
            if not isinstance(layers, list) or not layers:
                _fail("Docker image archive does not contain image layers")
            for layer in layers:
                if not isinstance(layer, str):
                    _fail("Docker image archive contains an invalid layer path")
                layer_name = _safe_docker_member_name(layer, "layer path")
                if layer_name not in members:
                    _fail(f"Docker image archive is missing layer: {layer_name}")
    except (OSError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid packaged Docker image archive: {exc}") from exc


def _package_architecture(fpk_path: Path, package_version: str) -> tuple[str, dict[str, str]]:
    match = re.fullmatch(
        rf"Nexus-fnOS-{re.escape(package_version)}-(amd64|arm64)\.fpk",
        fpk_path.name,
    )
    if not match:
        _fail("fnOS package filename does not include a supported architecture")
    architecture = match.group(1)
    return architecture, ARCHITECTURES[architecture]


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
            outer = _safe_archive_files(outer_archive, "FPK")
    except (OSError, tarfile.TarError) as exc:
        raise VerificationError(f"invalid FPK archive: {exc}") from exc

    _assert_expected_files(set(outer), OUTER_FILES, "FPK")
    _assert_clean_names(set(outer), "FPK")

    try:
        with tarfile.open(fileobj=io.BytesIO(outer["app.tgz"]), mode="r:gz") as app_archive:
            app = _safe_archive_files(app_archive, "app.tgz")
    except tarfile.TarError as exc:
        raise VerificationError(f"invalid app.tgz archive: {exc}") from exc

    _assert_expected_files(set(app), APP_FILES, "app.tgz")
    _assert_clean_names(set(app), "app.tgz")

    for name in OUTER_FILES - {"app.tgz", "manifest"}:
        expected = _source_bytes(name)
        if outer[name] != expected:
            _fail(f"FPK content differs from package source: {name}")
    for name in STATIC_APP_FILES:
        expected = _source_bytes(name, in_app=True) if not name.startswith("config/") else _source_bytes(name)
        if app[name] != expected:
            _fail(f"app.tgz content differs from package source: {name}")

    generated_manifest = _read_manifest(outer["manifest"])
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
    if not re.fullmatch(r"[0-9a-f]{32}", generated_manifest.get("checksum", "")):
        _fail("generated manifest checksum is missing or invalid")
    if source_manifest.get("service_port") != "8787":
        _fail("fnOS package must publish the standard Nexus port 8787")

    combined_text: dict[str, str] = {}
    for name, data in outer.items():
        if name == "app.tgz" or PurePosixPath(name).suffix.lower() not in TEXT_SUFFIXES:
            continue
        combined_text[f"FPK/{name}"] = _assert_clean_text(
            f"FPK/{name}", data, allow_crlf=name == "manifest"
        )
    for name, data in app.items():
        if PurePosixPath(name).suffix.lower() in TEXT_SUFFIXES:
            combined_text[f"app.tgz/{name}"] = _assert_clean_text(f"app.tgz/{name}", data)

    expected_image = f"nexus-gateway-fnos:{gateway_version}"
    compose = combined_text["app.tgz/docker/docker-compose.yaml"]
    if not re.search(rf"(?m)^\s*image:\s*{re.escape(expected_image)}\s*$", compose):
        _fail(f"Compose does not use the packaged image {expected_image}")
    if not re.search(r"(?m)^\s*pull_policy:\s*never\s*$", compose):
        _fail("Compose does not disable remote image pulls")
    if re.search(r"(?i)ghcr\.io|github\.com", compose):
        _fail("Compose contains a GitHub or GHCR dependency")
    if re.search(r"(?m)^\s*(?:build|NEXUS_PASSWORD|HERMES_API_TOKEN|NEXUS_SESSION_SECRET)\s*:", compose):
        _fail("Compose contains a local build or plaintext secret environment field")
    if not re.search(r"(?m)^\s*network_mode:\s*host\s*$", compose):
        _fail("fnOS Compose must share the NAS host network")
    if re.search(r"(?m)^\s*(?:ports|extra_hosts):\s*$", compose):
        _fail("fnOS host networking must not include port mappings or bridge host aliases")
    if not re.search(r"(?m)^\s*NEXUS_DEPLOYMENT_MODE:\s*fnos-host\s*$", compose):
        _fail("fnOS Compose must identify its host-network deployment mode")
    if '"${TRIM_PKGVAR}:/data"' not in compose or "/opt/hermes" in compose.lower():
        _fail("Compose violates the Nexus-only data boundary")
    if "/api/setup/status" not in compose or "initialized" not in compose:
        _fail("Compose healthcheck does not verify that fnOS setup was consumed")

    checksum_text = combined_text[f"app.tgz/docker/{IMAGE_CHECKSUM_NAME}"]
    image_digest = hashlib.sha256(app[f"docker/{IMAGE_ARCHIVE_NAME}"]).hexdigest()
    expected_checksum_text = f"{image_digest}  {IMAGE_ARCHIVE_NAME}\n"
    if checksum_text != expected_checksum_text:
        _fail("packaged Docker image SHA-256 does not match its checksum file")
    platform_text = combined_text[f"app.tgz/docker/{IMAGE_PLATFORM_NAME}"]
    if platform_text != f"{architecture_metadata['platform']}\n":
        _fail("packaged Docker image platform marker does not match the FPK architecture")
    _verify_docker_archive(
        app[f"docker/{IMAGE_ARCHIVE_NAME}"],
        expected_image,
        architecture_metadata["docker"],
    )

    scripts = "\n".join(text for name, text in combined_text.items() if name.startswith("FPK/cmd/"))
    for callback_name in ("install_callback", "config_callback"):
        callback = combined_text[f"FPK/cmd/{callback_name}"]
        if "docker inspect nexus-gateway-fnos" not in callback or "docker restart nexus-gateway-fnos" not in callback:
            _fail(f"{callback_name} does not restart the Nexus container after saving configuration")
    install_init = combined_text["FPK/cmd/install_init"]
    upgrade_init = combined_text["FPK/cmd/upgrade_init"]
    setup_common = combined_text["FPK/cmd/setup_common.sh"]
    if "load_packaged_image" not in install_init or "load_packaged_image" not in upgrade_init:
        _fail("install and upgrade lifecycle scripts must load the packaged image")
    if "TRIM_PKGINST_TEMP_DIR" not in setup_common or 'docker load --input "$archive"' not in setup_common:
        _fail("packaged image loader does not use the fnOS staging directory")
    if expected_image not in setup_common:
        _fail("packaged image loader checks the wrong image tag")
    uninstall_callback = combined_text["FPK/cmd/uninstall_callback"]
    if f"docker image rm {expected_image}" not in uninstall_callback:
        _fail("uninstall callback does not limit cleanup to the Nexus-owned image")

    forbidden_network_patterns = [
        r"\bdocker\s+pull\b",
        r"\b(?:curl|wget)\b",
        r"https?://(?:www\.)?github\.com",
        r"https?://ghcr\.io",
    ]
    for pattern in forbidden_network_patterns:
        if re.search(pattern, scripts, re.IGNORECASE):
            _fail("lifecycle scripts contain a remote download operation")

    hermes_word = "her" + "mes"
    forbidden_lifecycle_patterns = [
        rf"systemctl\s+(?:start|stop|restart).*{hermes_word}",
        rf"docker\s+(?:start|stop|restart|rm).*{hermes_word}",
        rf"/opt/{hermes_word}",
        rf"/var/lib/{hermes_word}",
    ]
    for pattern in forbidden_lifecycle_patterns:
        if re.search(pattern, scripts, re.IGNORECASE):
            _fail("lifecycle scripts attempt to manage Hermes")

    entrypoint = combined_text["app.tgz/docker/fnos_entrypoint.py"]
    if "hashlib.scrypt" not in entrypoint or "secrets.token_urlsafe" not in entrypoint:
        _fail("fnOS entrypoint does not securely initialize Nexus credentials")

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
        **{f"FPK/{key}": value for key, value in outer.items()},
        **{f"app.tgz/{key}": value for key, value in app.items()},
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
    parser = argparse.ArgumentParser(description="Verify a self-contained Nexus fnOS FPK without extracting it")
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
