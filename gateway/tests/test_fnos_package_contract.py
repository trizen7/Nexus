from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import platform as host_platform
import re
import shutil
import struct
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "fnos" / "nexus-gateway"
LAUNCHER = ROOT / "gateway" / "nexus_gateway" / "fnos_launcher.py"
VERIFIER = ROOT / "scripts" / "verify_fnos_package.py"
NORMALIZER = ROOT / "scripts" / "normalize_fnos_package.py"


@dataclass(frozen=True)
class TarEntry:
    data: bytes = b""
    mode: int = 0o644
    link_target: str | None = None
    is_directory: bool = False


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_launcher() -> ModuleType:
    return _load_module(LAUNCHER, "nexus_fnos_launcher_test")


def _load_verifier() -> ModuleType:
    return _load_module(VERIFIER, "nexus_fnos_verifier_test")


def _load_normalizer() -> ModuleType:
    return _load_module(NORMALIZER, "nexus_fnos_normalizer_test")


def _manifest() -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in (PACKAGE / "manifest").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _png_size(path: Path) -> tuple[int, int]:
    raw = path.read_bytes()
    assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    return struct.unpack(">II", raw[16:24])


def _point_launcher_at(module: ModuleType, data_dir: Path) -> None:
    module.DATA_DIR = data_dir
    module.SETUP_DIR = data_dir / ".fnos-setup"
    module.ACCOUNT_PATH = data_dir / "account.json"
    module.CONFIG_PATH = data_dir / "config.json"


def _write_setup(data_dir: Path, mode: str, **fields: str) -> None:
    setup = data_dir / ".fnos-setup"
    setup.mkdir(parents=True)
    (setup / "mode").write_text(mode, encoding="utf-8")
    for name, value in fields.items():
        (setup / name).write_text(value, encoding="utf-8")


def _minimal_elf(machine: int) -> bytes:
    data = bytearray(64)
    data[:4] = b"\x7fELF"
    data[4] = 2
    data[5] = 1
    data[6] = 1
    struct.pack_into("<H", data, 16, 2)
    struct.pack_into("<H", data, 18, machine)
    struct.pack_into("<I", data, 20, 1)
    return bytes(data)


def _tar_bytes(entries: dict[str, TarEntry], *, gzip: bool) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if gzip else "w") as archive:
        for name in sorted(entries):
            entry = entries[name]
            info = tarfile.TarInfo(name)
            info.mode = entry.mode
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            if entry.is_directory:
                info.type = tarfile.DIRTYPE
                info.size = 0
                archive.addfile(info)
            elif entry.link_target is not None:
                info.type = tarfile.SYMTYPE
                info.linkname = entry.link_target
                info.size = 0
                archive.addfile(info)
            else:
                info.size = len(entry.data)
                archive.addfile(info, io.BytesIO(entry.data))
    return buffer.getvalue()


def _archive_members(payload: bytes, *, gzip: bool = False) -> dict[str, tarfile.TarInfo]:
    mode = "r:gz" if gzip else "r:*"
    with tarfile.open(fileobj=io.BytesIO(payload), mode=mode) as archive:
        return {member.name.rstrip("/"): member for member in archive.getmembers()}


def _archive_file(payload: bytes, name: str, *, gzip: bool = False) -> bytes:
    mode = "r:gz" if gzip else "r:*"
    with tarfile.open(fileobj=io.BytesIO(payload), mode=mode) as archive:
        member = archive.getmember(name)
        stream = archive.extractfile(member)
        assert stream is not None
        return stream.read()


def _generated_manifest(architecture: str, app_payload: bytes) -> bytes:
    source = (PACKAGE / "manifest").read_text(encoding="utf-8")
    platform = {"amd64": "x86", "arm64": "arm"}[architecture]
    generated = re.sub(r"(?m)^platform=all$", f"platform={platform}", source)
    if not generated.endswith("\n"):
        generated += "\n"
    checksum = hashlib.md5(app_payload, usedforsecurity=False).hexdigest()
    generated += f"checksum={checksum}\n"
    return generated.encode("utf-8")


def _runtime_entries(
    architecture: str,
    *,
    platform: str | None = None,
    elf_machine: int | None = None,
    executable_mode: int = 0o755,
    missing: set[str] | None = None,
    bad_checksum_for: str | None = None,
) -> dict[str, TarEntry]:
    expected_platform = {"amd64": "linux/amd64", "arm64": "linux/arm64"}[architecture]
    expected_machine = {"amd64": 62, "arm64": 183}[architecture]
    files: dict[str, TarEntry] = {
        "runtime/runtime.platform": TarEntry(f"{platform or expected_platform}\n".encode("ascii")),
        "runtime/ca-certificates.crt": TarEntry(
            b"-----BEGIN CERTIFICATE-----\n" + b"A" * 1100 + b"\n-----END CERTIFICATE-----\n"
        ),
        "runtime/nexus-gateway/nexus-gateway": TarEntry(
            _minimal_elf(elf_machine if elf_machine is not None else expected_machine), executable_mode
        ),
        "runtime/nexus-gateway/_internal/nexus_gateway/web/index.html": TarEntry(
            (ROOT / "gateway" / "nexus_gateway" / "web" / "index.html").read_bytes()
        ),
        "runtime/nexus-gateway/_internal/nexus_gateway/web/app.js": TarEntry(
            (ROOT / "gateway" / "nexus_gateway" / "web" / "app.js").read_bytes()
        ),
        "runtime/nexus-gateway/_internal/nexus_gateway/web/styles.css": TarEntry(
            (ROOT / "gateway" / "nexus_gateway" / "web" / "styles.css").read_bytes()
        ),
    }
    for name in missing or set():
        files.pop(name, None)

    checksum_lines: list[str] = []
    for name in sorted(files):
        relative = name.removeprefix("runtime/")
        digest = hashlib.sha256(files[name].data).hexdigest()
        if relative == bad_checksum_for:
            digest = "0" * 64
        checksum_lines.append(f"{digest}  {relative}")
    files["runtime/runtime.sha256"] = TarEntry(("\n".join(checksum_lines) + "\n").encode("ascii"))
    return files


def _build_synthetic_fpk(
    directory: Path,
    architecture: str,
    *,
    platform: str | None = None,
    elf_machine: int | None = None,
    executable_mode: int = 0o755,
    missing_runtime: set[str] | None = None,
    bad_checksum_for: str | None = None,
    extra_app: dict[str, TarEntry] | None = None,
    app_symlink: tuple[str, str] | None = None,
) -> Path:
    verifier = _load_verifier()
    app_entries: dict[str, TarEntry] = {}
    for name in verifier.STATIC_APP_FILES:
        source = PACKAGE / (name if name.startswith("ui/") else "")
        if name.startswith("ui/"):
            source = PACKAGE / "app" / name
        else:
            source = PACKAGE / name
        app_entries[name] = TarEntry(source.read_bytes())
    app_entries.update(
        _runtime_entries(
            architecture,
            platform=platform,
            elf_machine=elf_machine,
            executable_mode=executable_mode,
            missing=missing_runtime,
            bad_checksum_for=bad_checksum_for,
        )
    )
    if extra_app:
        app_entries.update(extra_app)
    if app_symlink is not None:
        name, target = app_symlink
        app_entries[name] = TarEntry(link_target=target)

    app_payload = _tar_bytes(app_entries, gzip=True)
    outer_entries: dict[str, TarEntry] = {}
    for name in verifier.OUTER_FILES:
        if name == "app.tgz":
            outer_entries[name] = TarEntry(app_payload)
        elif name == "manifest":
            outer_entries[name] = TarEntry(_generated_manifest(architecture, app_payload))
        else:
            mode = 0o755 if name.startswith("cmd/") else 0o644
            outer_entries[name] = TarEntry((PACKAGE / name).read_bytes(), mode)

    version = _manifest()["version"]
    fpk = directory / f"Nexus-fnOS-{version}-{architecture}.fpk"
    fpk.write_bytes(_tar_bytes(outer_entries, gzip=True))
    return fpk


def _rewrite_with_windows_fnpack_permissions(fpk: Path) -> None:
    outer_payload = fpk.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(outer_payload), mode="r:*") as outer_archive:
        outer_files = {
            member.name: outer_archive.extractfile(member).read()
            for member in outer_archive.getmembers()
            if member.isfile()
        }

    app_payload = outer_files["app.tgz"]
    with tarfile.open(fileobj=io.BytesIO(app_payload), mode="r:gz") as app_archive:
        app_files = {
            member.name: app_archive.extractfile(member).read()
            for member in app_archive.getmembers()
            if member.isfile()
        }

    app_entries = {name: TarEntry(data=data, mode=0o666) for name, data in app_files.items()}
    app_directories = {
        str(parent)
        for name in app_files
        for parent in Path(name).parents
        if str(parent) != "."
    }
    app_entries.update(
        {name.replace("\\", "/"): TarEntry(mode=0o777, is_directory=True) for name in app_directories}
    )
    outer_files["app.tgz"] = _tar_bytes(app_entries, gzip=True)

    outer_entries = {name: TarEntry(data=data, mode=0o666) for name, data in outer_files.items()}
    outer_entries.update(
        {name: TarEntry(mode=0o777, is_directory=True) for name in ("cmd", "config", "wizard")}
    )
    fpk.write_bytes(_tar_bytes(outer_entries, gzip=True))


def test_fnos_manifest_desktop_and_icons_are_consistent() -> None:
    manifest = _manifest()
    gateway_version = re.search(
        r'__version__\s*=\s*"([^"]+)"',
        (ROOT / "gateway" / "nexus_gateway" / "__init__.py").read_text(encoding="utf-8"),
    ).group(1)

    assert gateway_version == "0.1.8"
    assert manifest["appname"] == "nexus-gateway"
    assert manifest["version"] == "0.1.8"
    assert manifest["version"] == gateway_version
    assert manifest["source"] == "thirdparty"
    assert manifest["platform"] == "all"
    assert manifest["desktop_uidir"] == "ui"
    assert manifest["desktop_applaunchname"] == "nexus-gateway.main"
    assert manifest["service_port"] == "8787"
    assert manifest["checkport"] == "true"
    assert manifest["ctl_stop"] == "true"
    assert manifest["disable_authorization_path"] == "true"
    assert "changelog" not in manifest
    assert "maintainer_url" not in manifest
    assert "distributor_url" not in manifest

    ui = json.loads((PACKAGE / "app" / "ui" / "config").read_text(encoding="utf-8"))
    entry = ui[".url"][manifest["desktop_applaunchname"]]
    assert entry["type"] == "url"
    assert entry["protocol"] == "http"
    assert entry["port"] == "8787"
    assert entry["url"] == "/"
    assert _png_size(PACKAGE / "ICON.PNG") == (64, 64)
    assert _png_size(PACKAGE / "ICON_256.PNG") == (256, 256)
    assert _png_size(PACKAGE / "app" / "ui" / "images" / "icon_64.png") == (64, 64)
    assert _png_size(PACKAGE / "app" / "ui" / "images" / "icon_256.png") == (256, 256)


def test_fnos_source_is_native_offline_and_contains_no_generated_runtime() -> None:
    assert json.loads((PACKAGE / "config" / "resource").read_text(encoding="utf-8")) == {}
    privilege = json.loads((PACKAGE / "config" / "privilege").read_text(encoding="utf-8"))
    assert privilege["defaults"]["run-as"] == "package"
    assert not (PACKAGE / "app" / "docker").exists()
    assert not (PACKAGE / "app" / "runtime").exists()

    lifecycle = "\n".join(path.read_text(encoding="utf-8") for path in (PACKAGE / "cmd").iterdir() if path.is_file())
    for pattern in (r"\bdocker\b", r"\b(?:curl|wget|git)\b", r"https?://[A-Za-z0-9]", r"/dev/tcp", r"\bnc\s"):
        assert re.search(pattern, lifecycle, re.IGNORECASE) is None
    lowered = lifecycle.lower()
    assert "systemctl start hermes" not in lowered
    assert "systemctl stop hermes" not in lowered
    assert "/opt/hermes" not in lowered
    assert "/var/lib/hermes" not in lowered


def test_fnos_launcher_initializes_without_plaintext_password(tmp_path: Path) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    _write_setup(
        tmp_path,
        "install",
        username="admin",
        password="correct horse battery staple",
        hermes_api_url="http://host.docker.internal:8000/",
        hermes_api_token="test-token-not-a-real-secret",
    )

    module.apply_pending_setup()

    account = json.loads((tmp_path / "account.json").read_text(encoding="utf-8"))
    config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert account["username"] == "admin"
    assert "password" not in account
    assert account["password_scheme"] == "scrypt"
    candidate = hashlib.scrypt(
        b"correct horse battery staple",
        salt=bytes.fromhex(account["password_salt"]),
        n=2**14,
        r=8,
        p=1,
        dklen=32,
    ).hex()
    assert candidate == account["password_hash"]
    assert account["revision"] == 1
    assert config["hermes_api_url"] == "http://127.0.0.1:8000"
    assert config["hermes_api_token"] == "test-token-not-a-real-secret"
    assert len(config["session_secret"]) >= 16
    assert not (tmp_path / ".fnos-setup").exists()


@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://",
        "https:///missing-host",
        "ftp://hermes.example",
        "http://user:password@hermes.example",
        "http://hermes example:8000",
        "http://hermes.example:8000?debug=true",
        "http://hermes.example:8000#fragment",
        "http://hermes.example:70000",
        "http://0.0.0.0:8000",
        "http://[::]:8000",
    ],
)
def test_fnos_launcher_rejects_invalid_hermes_urls(tmp_path: Path, invalid_url: str) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    _write_setup(
        tmp_path,
        "install",
        username="admin",
        password="correct horse battery staple",
        hermes_api_url=invalid_url,
        hermes_api_token="test-token-not-a-real-secret",
    )

    with pytest.raises(module.SetupError, match="valid http:// or https:// address"):
        module.apply_pending_setup()


@pytest.mark.parametrize(
    ("legacy_url", "expected_url"),
    [
        ("http://host.docker.internal:8000", "http://127.0.0.1:8000"),
        ("http://localhost:8000", "http://127.0.0.1:8000"),
        ("http://127.1.2.3:8000/api", "http://127.0.0.1:8000/api"),
        ("https://[::1]:8443", "https://127.0.0.1:8443"),
    ],
)
def test_fnos_launcher_migrates_same_nas_hermes_urls(
    tmp_path: Path,
    legacy_url: str,
    expected_url: str,
) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    original = {
        "hermes_api_url": legacy_url,
        "hermes_api_token": "legacy-test-token",
        "session_secret": "legacy-session-secret-value",
    }
    (tmp_path / "config.json").write_text(json.dumps(original), encoding="utf-8")

    module.apply_pending_setup()

    migrated = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert migrated["hermes_api_url"] == expected_url
    assert migrated["hermes_api_token"] == original["hermes_api_token"]
    assert migrated["session_secret"] == original["session_secret"]


@pytest.mark.parametrize("changed_field", ["username", "password", "hermes_api_url", "hermes_api_token"])
def test_fnos_launcher_applies_partial_configuration_without_erasing_values(
    tmp_path: Path,
    changed_field: str,
) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    _write_setup(
        tmp_path,
        "install",
        username="admin",
        password="initial-password",
        hermes_api_url="http://hermes.example:8000",
        hermes_api_token="initial-test-token",
    )
    module.apply_pending_setup()
    before_account = json.loads((tmp_path / "account.json").read_text(encoding="utf-8"))
    before_config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))

    replacements = {
        "username": "operator",
        "password": "replacement-password",
        "hermes_api_url": "https://hermes.example:8443/",
        "hermes_api_token": "replacement-test-token",
    }
    _write_setup(tmp_path, "config", **{changed_field: replacements[changed_field]})
    module.apply_pending_setup()

    after_account = json.loads((tmp_path / "account.json").read_text(encoding="utf-8"))
    after_config = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    if changed_field == "username":
        assert after_account["username"] == "operator"
        assert after_account["revision"] == before_account["revision"] + 1
    elif changed_field == "password":
        assert after_account["password_hash"] != before_account["password_hash"]
        assert after_account["revision"] == before_account["revision"] + 1
    else:
        assert after_account == before_account

    if changed_field == "hermes_api_url":
        assert after_config["hermes_api_url"] == "https://hermes.example:8443"
        assert after_config["hermes_api_token"] == before_config["hermes_api_token"]
    elif changed_field == "hermes_api_token":
        assert after_config["hermes_api_token"] == "replacement-test-token"
        assert after_config["hermes_api_url"] == before_config["hermes_api_url"]
    else:
        assert after_config == before_config


@pytest.mark.parametrize("invalid_revision", [True, 0, -1, 1.5, "1.5", "not-an-integer"])
def test_fnos_launcher_rejects_invalid_account_revision(tmp_path: Path, invalid_revision: object) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    _write_setup(
        tmp_path,
        "install",
        username="admin",
        password="initial-password",
        hermes_api_url="http://hermes.example:8000",
        hermes_api_token="initial-test-token",
    )
    module.apply_pending_setup()

    account_path = tmp_path / "account.json"
    account = json.loads(account_path.read_text(encoding="utf-8"))
    account["revision"] = invalid_revision
    account_path.write_text(json.dumps(account), encoding="utf-8")
    _write_setup(tmp_path, "config", username="operator")

    with pytest.raises(module.SetupError, match="invalid Nexus account revision"):
        module.apply_pending_setup()


def test_fnos_launcher_rejects_setup_symlink(tmp_path: Path) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (tmp_path / ".fnos-setup").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(module.SetupError, match="unsafe"):
        module.apply_pending_setup()


def test_fnos_launcher_rejects_nexus_paths_outside_data_directory(tmp_path: Path) -> None:
    module = _load_launcher()
    _point_launcher_at(module, tmp_path / "data")
    module.CONFIG_PATH = tmp_path / "outside-config.json"
    with pytest.raises(module.SetupError, match="outside the Nexus data directory"):
        module.apply_pending_setup()


def test_fnos_launcher_invokes_gateway_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_launcher()
    calls: list[str] = []
    monkeypatch.setattr(module, "apply_pending_setup", lambda: calls.append("setup"))
    import nexus_gateway.__main__ as gateway_main_module

    monkeypatch.setattr(gateway_main_module, "main", lambda: calls.append("gateway"))
    module.main()
    assert calls == ["setup", "gateway"]
    source = LAUNCHER.read_text(encoding="utf-8")
    assert "from nexus_gateway.__main__ import main as gateway_main" in source
    assert "subprocess" not in source
    assert "execvp" not in source


def test_fnos_lifecycle_validates_runtime_and_manages_only_native_gateway() -> None:
    setup_common = (PACKAGE / "cmd" / "setup_common.sh").read_text(encoding="utf-8")
    install_init = (PACKAGE / "cmd" / "install_init").read_text(encoding="utf-8")
    upgrade_init = (PACKAGE / "cmd" / "upgrade_init").read_text(encoding="utf-8")
    config_callback = (PACKAGE / "cmd" / "config_callback").read_text(encoding="utf-8")
    main = (PACKAGE / "cmd" / "main").read_text(encoding="utf-8")

    assert "validate_packaged_runtime" in install_init
    assert "validate_packaged_runtime" in upgrade_init
    assert "TRIM_PKGVAR" not in install_init + upgrade_init
    runtime_validation = re.search(
        r"(?ms)^validate_runtime_dir\(\) \{\n(.*?)^\}$",
        setup_common,
    )
    assert runtime_validation is not None
    validation_body = runtime_validation.group(1)
    for forbidden in (
        "TRIM_PKGVAR",
        "require_pkgvar",
        "VERIFY_TEMP_DIR",
        "mkdir",
        "rm -rf",
        "sort",
        "uniq",
        "cmp",
    ):
        assert forbidden not in validation_body
    assert '"$SCRIPT_DIR/main" stop' in config_callback
    assert '"$SCRIPT_DIR/main" start' in config_callback
    for fragment in (
        "runtime.platform",
        "runtime.sha256",
        "nexus-gateway/nexus-gateway",
        "validate_runtime_elf",
        "find \"$runtime_dir\" -type l",
        "SHA-256 verification",
    ):
        assert fragment in setup_common
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
        assert fragment in main
    assert "docker" not in (setup_common + main).lower()


def _write_shell_test_runtime(package_root: Path) -> None:
    machine = host_platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        runtime_platform = "linux/amd64"
        elf_machine = 62
    elif machine in {"aarch64", "arm64"}:
        runtime_platform = "linux/arm64"
        elf_machine = 183
    else:
        pytest.skip(f"unsupported shell-test architecture: {machine}")

    runtime = package_root / "runtime"
    executable = runtime / "nexus-gateway" / "nexus-gateway"
    executable.parent.mkdir(parents=True)
    (runtime / "runtime.platform").write_text(runtime_platform + "\n", encoding="utf-8")
    (runtime / "ca-certificates.crt").write_text(
        "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----\n",
        encoding="utf-8",
    )
    executable.write_bytes(_minimal_elf(elf_machine))
    executable.chmod(0o755)

    entries = []
    for target in sorted(path for path in runtime.rglob("*") if path.is_file()):
        relative = target.relative_to(runtime).as_posix()
        if relative != "runtime.sha256":
            entries.append(f"{hashlib.sha256(target.read_bytes()).hexdigest()}  {relative}")
    (runtime / "runtime.sha256").write_text("\n".join(entries) + "\n", encoding="utf-8")


@pytest.mark.skipif(sys.platform != "linux" or shutil.which("bash") is None, reason="requires Linux bash")
@pytest.mark.parametrize("command_name", ["install_init", "upgrade_init"])
def test_fnos_preflight_does_not_require_or_write_pkgvar(tmp_path: Path, command_name: str) -> None:
    package_root = tmp_path / "package"
    _write_shell_test_runtime(package_root)

    env = os.environ.copy()
    env.update(
        {
            "TRIM_PKGINST_TEMP_DIR": str(package_root),
            "TRIM_PKGVAR": f"/proc/nexus-preflight-must-not-write-{os.getpid()}",
            "wizard_nexus_username": "admin",
            "wizard_nexus_password": "correct horse battery staple",
            "wizard_hermes_api_url": "http://127.0.0.1:8000/",
            "wizard_hermes_api_token": "test-key-not-a-secret",
        }
    )
    result = subprocess.run(
        ["bash", str(PACKAGE / "cmd" / command_name)],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not Path(env["TRIM_PKGVAR"]).exists()


def test_fnos_build_and_workflows_export_native_runtime_directories() -> None:
    build_script = (ROOT / "scripts" / "build_fnos_package.ps1").read_text(encoding="utf-8")
    dockerfile = (ROOT / "gateway" / "FnOS.Dockerfile").read_text(encoding="utf-8")
    spec = (ROOT / "gateway" / "nexus-gateway-fnos.spec").read_text(encoding="utf-8")
    workflows = [
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "container.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ]

    assert "RuntimeDirectoryPath" in build_script
    assert "ImageArchivePath" not in build_script
    assert "app/docker" in build_script
    assert "must not contain a container project" in build_script
    assert "runtime.platform" in build_script
    assert "runtime.sha256" in build_script
    assert "Assert-NoLinks" in build_script
    assert "Assert-RuntimeLayout" in build_script
    assert "Get-ContainedRelativePath" in build_script
    assert "[System.IO.Path]::GetRelativePath" not in build_script
    assert "normalize_fnos_package.py" in build_script
    assert "PythonPath" in build_script
    release_script = (ROOT / "scripts" / "build-android-release.ps1").read_text(encoding="utf-8")
    assert "PythonPath = $pythonCommand" in release_script
    assert "binutils" in dockerfile
    assert "FROM scratch AS runtime" in dockerfile
    assert "cp -aL" in dockerfile
    assert "find /runtime -type l" in dockerfile
    assert "--help" in dockerfile
    assert "fnos_launcher.py" in spec

    for path in workflows:
        workflow = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(workflow)
        assert isinstance(parsed["jobs"], dict)
        assert "gateway/FnOS.Dockerfile" in workflow
        assert 'type=local,dest=$RUNNER_TEMP/nexus-runtime-$architecture' in workflow
        assert "-RuntimeDirectoryPath" in workflow
        assert "type=docker" not in workflow
        assert "ImageArchivePath" not in workflow
        assert "docker save" not in workflow.lower()


def test_fnos_source_tree_ignores_generated_native_runtime() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/fnos/nexus-gateway/app/runtime/" in ignore
    assert "/gateway/build/" in ignore
    assert all("nexus-gateway-image" not in line for line in ignore)


def test_fnos_normalizer_repairs_windows_fnpack_permissions_and_checksum(tmp_path: Path) -> None:
    normalizer = _load_normalizer()
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64", executable_mode=0o666)
    _rewrite_with_windows_fnpack_permissions(fpk)

    with pytest.raises(verifier.VerificationError, match="unsafe permissions"):
        verifier.verify_package(fpk)

    normalizer.normalize_package(fpk)

    assert fpk.read_bytes().startswith(b"\x1f\x8b")
    assert verifier.verify_package(fpk) == hashlib.sha256(fpk.read_bytes()).hexdigest()

    outer_payload = fpk.read_bytes()
    outer = _archive_members(outer_payload)
    assert (outer["cmd"].mode & 0o7777) == 0o755
    assert (outer["config"].mode & 0o7777) == 0o755
    assert (outer["cmd/main"].mode & 0o7777) == 0o755
    assert (outer["manifest"].mode & 0o7777) == 0o644
    assert (outer["app.tgz"].mode & 0o7777) == 0o644

    app_payload = _archive_file(outer_payload, "app.tgz")
    manifest = _archive_file(outer_payload, "manifest").decode("utf-8")
    expected_checksum = hashlib.md5(app_payload, usedforsecurity=False).hexdigest()
    assert re.search(rf"(?m)^checksum\s*=\s*{expected_checksum}$", manifest)

    app = _archive_members(app_payload, gzip=True)
    assert (app["runtime"].mode & 0o7777) == 0o755
    assert (app["runtime/nexus-gateway/nexus-gateway"].mode & 0o7777) == 0o755
    assert (app["runtime/ca-certificates.crt"].mode & 0o7777) == 0o644
    assert (app["ui/config"].mode & 0o7777) == 0o644


def test_fnos_normalizer_rejects_path_traversal(tmp_path: Path) -> None:
    normalizer = _load_normalizer()
    fpk = tmp_path / "unsafe.fpk"
    fpk.write_bytes(
        _tar_bytes(
            {
                "../manifest": TarEntry(b"unsafe"),
                "app.tgz": TarEntry(_tar_bytes({"file": TarEntry(b"data")}, gzip=True)),
            },
            gzip=True,
        )
    )

    with pytest.raises(normalizer.NormalizationError, match="unsafe path"):
        normalizer.normalize_package(fpk)


def test_fnos_normalizer_rejects_symlink_in_app_archive(tmp_path: Path) -> None:
    normalizer = _load_normalizer()
    app_payload = _tar_bytes(
        {
            "unsafe-link": TarEntry(link_target="/etc/passwd"),
        },
        gzip=True,
    )
    manifest = b"appname = nexus-gateway\nchecksum = " + (b"0" * 32) + b"\n"
    fpk = tmp_path / "unsafe.fpk"
    fpk.write_bytes(
        _tar_bytes(
            {
                "app.tgz": TarEntry(app_payload),
                "manifest": TarEntry(manifest),
            },
            gzip=True,
        )
    )

    with pytest.raises(normalizer.NormalizationError, match="unsupported link or device"):
        normalizer.normalize_package(fpk)


@pytest.mark.parametrize("architecture", ["amd64", "arm64"])
def test_fnos_verifier_accepts_self_contained_native_packages(tmp_path: Path, architecture: str) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, architecture)
    digest = hashlib.sha256(fpk.read_bytes()).hexdigest()
    checksum = tmp_path / "SHA256SUMS.txt"
    checksum.write_text(
        "0" * 64 + "  Nexus-Android-0.1.8-release.apk\n" + f"{digest}  {fpk.name}\n",
        encoding="utf-8",
    )

    assert verifier.verify_package(fpk, checksum) == digest


def test_fnos_verifier_rejects_stale_manifest_checksum(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64")
    outer_payload = fpk.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(outer_payload), mode="r:*") as archive:
        entries = {
            member.name: TarEntry(
                data=archive.extractfile(member).read(),
                mode=member.mode,
            )
            for member in archive.getmembers()
            if member.isfile()
        }
    entries["app.tgz"] = TarEntry(entries["app.tgz"].data + b"stale", mode=entries["app.tgz"].mode)
    fpk.write_bytes(_tar_bytes(entries, gzip=True))

    with pytest.raises(verifier.VerificationError, match="does not match app.tgz"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_bad_runtime_checksum(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64", bad_checksum_for="ca-certificates.crt")
    with pytest.raises(verifier.VerificationError, match="runtime checksum mismatch"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_missing_runtime_file(tmp_path: Path) -> None:
    verifier = _load_verifier()
    missing = {"runtime/nexus-gateway/_internal/nexus_gateway/web/app.js"}
    fpk = _build_synthetic_fpk(tmp_path, "amd64", missing_runtime=missing)
    with pytest.raises(verifier.VerificationError, match="native runtime is incomplete"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_unexpected_app_file(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(
        tmp_path,
        "amd64",
        extra_app={"unexpected.txt": TarEntry(b"not allowed")},
    )
    with pytest.raises(verifier.VerificationError, match="unexpected files"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_runtime_symlink(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(
        tmp_path,
        "amd64",
        app_symlink=("runtime/nexus-gateway/unsafe-link", "/etc/passwd"),
    )
    with pytest.raises(verifier.VerificationError, match="link or device"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_non_executable_gateway(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64", executable_mode=0o644)
    with pytest.raises(verifier.VerificationError, match="not executable"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_wrong_elf_architecture(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64", elf_machine=183)
    with pytest.raises(verifier.VerificationError, match="ELF architecture"):
        verifier.verify_package(fpk)


def test_fnos_verifier_rejects_wrong_runtime_platform(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64", platform="linux/arm64")
    with pytest.raises(verifier.VerificationError, match="platform marker"):
        verifier.verify_package(fpk)


def test_fnos_verifier_requires_unified_checksum_entry(tmp_path: Path) -> None:
    verifier = _load_verifier()
    fpk = _build_synthetic_fpk(tmp_path, "amd64")
    checksum = tmp_path / "SHA256SUMS.txt"
    checksum.write_text("0" * 64 + "  Nexus-Android-0.1.8-release.apk\n", encoding="utf-8")
    with pytest.raises(verifier.VerificationError, match="does not contain"):
        verifier.verify_package(fpk, checksum)
