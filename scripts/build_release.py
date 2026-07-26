#!/usr/bin/env python3
"""Validate Nexus versions and create deterministic public release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_FILES = (
    "compose.yaml",
    "LICENSE",
    "NOTICE",
    "PRIVACY.md",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "TRADEMARKS.md",
    "docs/docker-deployment.md",
    "docs/local-test-environment.md",
    "gateway/.dockerignore",
    "gateway/.env.example",
    "gateway/Dockerfile",
    "gateway/requirements.txt",
    "gateway/start_gateway.py",
    "gateway/nexus_gateway/app.py",
    "gateway/nexus_gateway/__init__.py",
    "gateway/nexus_gateway/__main__.py",
    "gateway/nexus_gateway/web/app.js",
    "gateway/nexus_gateway/web/index.html",
    "gateway/nexus_gateway/web/styles.css",
)
FINGERPRINT_RE = re.compile(r"(?:certificate SHA-256 digest|SHA256):\s*([0-9A-Fa-f:]{64,95})", re.I)


def read_text(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def match_one(pattern: str, text: str, label: str) -> str:
    match = re.search(pattern, text, re.M)
    if not match:
        raise RuntimeError(f"could not read {label}")
    return match.group(1)


def normalize_fingerprint(value: str) -> str:
    normalized = value.replace(":", "").strip().lower()
    if normalized and not re.fullmatch(r"[0-9a-f]{64}", normalized):
        raise RuntimeError("Android certificate SHA-256 must contain exactly 64 hexadecimal characters")
    return normalized


def release_metadata() -> tuple[str, int]:
    gateway_version = match_one(
        r'__version__\s*=\s*"([^"]+)"',
        read_text("gateway/nexus_gateway/__init__.py"),
        "Gateway version",
    )
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?", gateway_version):
        raise RuntimeError(f"Gateway version is not SemVer: {gateway_version}")
    gradle = read_text("android/app/build.gradle.kts")
    android_version = match_one(r'versionName\s*=\s*"([^"]+)"', gradle, "Android versionName")
    version_code = int(match_one(r"versionCode\s*=\s*(\d+)", gradle, "Android versionCode"))
    expected_fragments = {
        "README.md": f"当前版本：{gateway_version}",
        "compose.yaml": f"nexus-mobile-gateway:{gateway_version}",
        "gateway/tests/test_docker_contract.py": f'nexus-mobile-gateway:{gateway_version}',
    }
    if android_version != gateway_version:
        raise RuntimeError(f"Android version {android_version} does not match Gateway version {gateway_version}")
    for relative, fragment in expected_fragments.items():
        if fragment not in read_text(relative):
            raise RuntimeError(f"{relative} does not reference release version {gateway_version}")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    if tag and tag.startswith("v") and tag != f"v{gateway_version}":
        raise RuntimeError(f"tag {tag} does not match version {gateway_version}")
    return gateway_version, version_code


def safe_output_path(value: str) -> Path:
    output = Path(value).resolve()
    try:
        output.relative_to(REPO_ROOT)
    except ValueError as exc:
        raise RuntimeError("release output must stay inside the Nexus repository") from exc
    if output == REPO_ROOT:
        raise RuntimeError("release output cannot be the repository root")
    return output


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum_manifest(artifacts: list[Path], target: Path) -> None:
    names = [path.name for path in artifacts]
    if len(names) != len(set(names)):
        raise RuntimeError("release artifact names must be unique")
    checksum_lines = [
        f"{sha256(path)}  {path.name}"
        for path in sorted(artifacts, key=lambda item: item.name)
    ]
    target.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n")


def canonical_gateway_bytes(source: Path) -> bytes:
    """Return platform-independent bytes for text-only Gateway release files."""
    return source.read_bytes().replace(b"\r\n", b"\n")


def deterministic_zip(target: Path) -> None:
    # Stored entries avoid zlib-version differences; all release inputs are text files.
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
        for relative in GATEWAY_FILES:
            source = REPO_ROOT / relative
            if not source.is_file():
                raise RuntimeError(f"missing Gateway release file: {relative}")
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, canonical_gateway_bytes(source))


def find_apksigner() -> Path:
    sdk_root = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    candidates: list[Path] = []
    if sdk_root:
        build_tools = Path(sdk_root) / "build-tools"
        if build_tools.is_dir():
            candidates.extend(sorted(build_tools.glob("*/apksigner*"), reverse=True))
    executable = next((item for item in candidates if item.name in {"apksigner", "apksigner.bat"}), None)
    if executable is None:
        raise RuntimeError("apksigner was not found; set ANDROID_SDK_ROOT before building a public release")
    return executable


def parse_fingerprint(output: str, label: str) -> str:
    match = FINGERPRINT_RE.search(output)
    if not match:
        raise RuntimeError(f"could not read {label} certificate SHA-256")
    return normalize_fingerprint(match.group(1))


def verify_apk(apk: Path) -> str:
    result = subprocess.run(
        [str(find_apksigner()), "verify", "--verbose", "--print-certs", str(apk)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_fingerprint(result.stdout + "\n" + result.stderr, "APK signer")


def copy_required(source: str | None, target: Path, label: str) -> Path:
    if not source:
        raise RuntimeError(f"{label} path is required")
    path = Path(source).resolve()
    if not path.is_file():
        raise RuntimeError(f"{label} does not exist: {path}")
    shutil.copy2(path, target)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(REPO_ROOT / "dist"))
    parser.add_argument("--apk")
    parser.add_argument("--require-android", action="store_true")
    parser.add_argument("--verify-signatures", action="store_true")
    parser.add_argument("--certificate-sha256", default="")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    version, version_code = release_metadata()
    print(f"release_version={version}")
    print(f"android_version_code={version_code}")
    if args.validate_only:
        return 0

    output = safe_output_path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    gateway_zip = output / f"Nexus-Gateway-{version}.zip"
    apk_target = output / f"Nexus-Android-{version}-release.apk"
    checksums = output / "SHA256SUMS.txt"
    for stale in (gateway_zip, apk_target, checksums):
        stale.unlink(missing_ok=True)

    deterministic_zip(gateway_zip)
    artifacts = [gateway_zip]
    if args.require_android or args.apk:
        artifacts.append(copy_required(args.apk, apk_target, "release APK"))
    for legacy_checksum in output.glob(f"Nexus-fnOS-{version}-fnos*.fpk.sha256"):
        legacy_checksum.unlink()
    artifacts.extend(
        path for path in sorted(output.glob(f"Nexus-fnOS-{version}-fnos*.fpk"))
        if path.is_file()
    )

    expected_fingerprint = normalize_fingerprint(args.certificate_sha256)
    if args.verify_signatures:
        if not apk_target.is_file():
            raise RuntimeError("signature verification requires the release APK")
        apk_fingerprint = verify_apk(apk_target)
        if expected_fingerprint and apk_fingerprint != expected_fingerprint:
            raise RuntimeError("Android release certificate does not match the configured fingerprint")

    write_checksum_manifest(artifacts, checksums)

    print(f"output={output}")
    for artifact in artifacts:
        print(f"artifact={artifact.name}")
    print(f"artifact={checksums.name}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"release build failed: {exc}", file=sys.stderr)
        sys.exit(1)
