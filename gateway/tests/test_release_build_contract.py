import importlib.util
import zipfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BUILD_RELEASE_PATH = REPOSITORY_ROOT / "scripts" / "build_release.py"


def load_build_release():
    spec = importlib.util.spec_from_file_location("nexus_build_release", BUILD_RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_gateway_release_zip_is_cross_platform_byte_stable(tmp_path: Path) -> None:
    build_release = load_build_release()
    assert "gateway/nexus_gateway/admin_page.py" not in build_release.GATEWAY_FILES
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
        tmp_path / "Nexus-Android-0.1.4-release.apk",
        tmp_path / "Nexus-Gateway-0.1.4.zip",
        tmp_path / "Nexus-fnOS-0.1.4-fnos1.fpk",
    ]
    for index, artifact in enumerate(artifacts):
        artifact.write_bytes(f"artifact-{index}".encode("ascii"))

    checksums = tmp_path / "SHA256SUMS.txt"
    build_release.write_checksum_manifest(artifacts, checksums)

    lines = checksums.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    assert [line.split("  ", 1)[1] for line in lines] == sorted(path.name for path in artifacts)
    assert not list(tmp_path.glob("*.sha256"))
