import importlib.util
import hashlib
import sys
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_RELEASE_PATH = REPOSITORY_ROOT / "scripts" / "build_release.py"
SECRET_SCAN_PATH = REPOSITORY_ROOT / "scripts" / "scan_repository_secrets.py"
VERSION = "0.1.17"
FNOS_VERSION = VERSION


def load_build_release():
    spec = importlib.util.spec_from_file_location("nexus_build_release", BUILD_RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_secret_scan():
    spec = importlib.util.spec_from_file_location("nexus_secret_scan", SECRET_SCAN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_sherpa_runtime_is_downloaded_from_official_versioned_release_with_fixed_digest() -> None:
    gradle = (REPOSITORY_ROOT / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
    assert "https://github.com/k2-fsa/sherpa-onnx/releases/download/v$sherpaOnnxVersion/" in gradle
    assert "6419CD8BC983E0C4FAB06067F0FE0313FDC0F7103818AC1E7A08D50787B7A82B" in gradle
    assert 'implementation(files(sherpaOnnxAar))' in gradle
    assert 'implementation(files("libs/sherpa-onnx-1.13.5.aar"))' not in gradle
    assert 'abiFilters += "arm64-v8a"' in gradle
    assert 'from(rootProject.projectDir.parentFile.resolve("THIRD_PARTY_NOTICES.md"))' in gradle
    assert 'rename { "APACHE-2.0-LICENSE.txt" }' in gradle
    assert 'assets.srcDir(generatedLegalAssets)' in gradle


def test_secret_scan_only_allows_the_verified_sherpa_large_dependency(monkeypatch) -> None:
    scanner = load_secret_scan()
    payload = b"verified-large-dependency"
    path = "android/app/libs/sherpa-onnx-1.13.5.aar"
    monkeypatch.setattr(scanner, "MAX_BLOB_BYTES", 1)
    monkeypatch.setattr(scanner, "VERIFIED_LARGE_DEPENDENCIES", {path: hashlib.sha256(payload).hexdigest()})

    assert scanner.scan_blob("worktree", path, payload) == []
    findings = scanner.scan_blob("worktree", path, payload + b"tampered")
    assert [finding.rule for finding in findings] == ["verified-dependency-digest-mismatch"]


def test_nexus_gradle_caches_are_ignored() -> None:
    ignore = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "android/.gradle-native/" in ignore
    assert "android/.gradle-user-home/" in ignore
    assert "android/app/libs/sherpa-onnx-1.13.5.aar" in ignore


def test_gateway_release_zip_is_cross_platform_byte_stable(tmp_path: Path) -> None:
    build_release = load_build_release()
    assert "gateway/nexus_gateway/admin_page.py" not in build_release.GATEWAY_FILES
    assert "gateway/nexus_gateway/cloud_asr.py" in build_release.GATEWAY_FILES
    assert "gateway/nexus_gateway/openai_asr.py" in build_release.GATEWAY_FILES
    assert not (REPOSITORY_ROOT / "gateway" / "nexus_gateway" / "admin_page.py").exists()
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    build_release.deterministic_zip(first)
    build_release.deterministic_zip(second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == list(build_release.GATEWAY_FILES)
        for relative in build_release.GATEWAY_FILES:
            info = archive.getinfo(relative)
            expected = (REPOSITORY_ROOT / relative).read_bytes().replace(b"\r\n", b"\n")
            assert archive.read(relative) == expected
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.external_attr == 0o100644 << 16


def test_release_uses_one_checksum_manifest_for_android_gateway_and_fnos(tmp_path: Path) -> None:
    build_release = load_build_release()
    artifacts = [
        tmp_path / f"Nexus-Android-{VERSION}-release.apk",
        tmp_path / f"Nexus-Gateway-{VERSION}.zip",
        tmp_path / f"Nexus-fnOS-{FNOS_VERSION}-amd64.fpk",
        tmp_path / f"Nexus-fnOS-{FNOS_VERSION}-arm64.fpk",
    ]
    for index, artifact in enumerate(artifacts):
        artifact.write_bytes(f"artifact-{index}".encode("ascii"))

    checksums = tmp_path / "SHA256SUMS.txt"
    build_release.write_checksum_manifest(artifacts, checksums)

    lines = checksums.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4
    assert [line.split("  ", 1)[1] for line in lines] == sorted(path.name for path in artifacts)
    assert not list(tmp_path.glob("*.sha256"))


def test_complete_release_requires_only_the_five_supported_attachments(tmp_path: Path) -> None:
    build_release = load_build_release()
    expected = {
        f"Nexus-Android-{VERSION}-release.apk",
        f"Nexus-Gateway-{VERSION}.zip",
        f"Nexus-fnOS-{FNOS_VERSION}-amd64.fpk",
        f"Nexus-fnOS-{FNOS_VERSION}-arm64.fpk",
        "SHA256SUMS.txt",
    }
    assert build_release.expected_complete_release_names(VERSION) == expected

    for name in expected:
        (tmp_path / name).write_bytes(b"artifact")
    build_release.validate_complete_release(tmp_path, VERSION)

    unwanted = [
        f"Nexus-Android-{VERSION}.aab",
        f"Nexus-fnOS-{FNOS_VERSION}-amd64.fpk.sha256",
        "release-manifest.json",
        "renease-manifest.json",
        "THIRD_PARTY_NOTICES.md",
    ]
    for name in unwanted:
        (tmp_path / name).write_bytes(b"unwanted")
    build_release.clean_generated_release_files(tmp_path, expected)
    assert {path.name for path in tmp_path.iterdir()} == expected


def test_release_copy_accepts_an_apk_already_at_the_target(tmp_path: Path) -> None:
    build_release = load_build_release()
    apk = tmp_path / "Nexus-Android-test-release.apk"
    apk.write_bytes(b"signed-apk")

    assert build_release.copy_required(str(apk), apk, "release APK") == apk
    assert apk.read_bytes() == b"signed-apk"


def test_android_release_preserves_sherpa_jni_binary_names() -> None:
    rules = (REPOSITORY_ROOT / "android" / "app" / "proguard-rules.pro").read_text(encoding="utf-8")

    assert "-keep class com.k2fsa.sherpa.onnx.** { *; }" in rules
