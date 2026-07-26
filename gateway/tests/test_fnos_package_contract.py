import hashlib
import importlib.util
import json
import re
import struct
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "fnos" / "nexus-gateway"
ENTRYPOINT = PACKAGE / "app" / "docker" / "fnos_entrypoint.py"


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


def _load_entrypoint():
    spec = importlib.util.spec_from_file_location("nexus_fnos_entrypoint", ENTRYPOINT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _point_entrypoint_at(module, data_dir: Path) -> None:
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


def test_fnos_manifest_and_desktop_entry_are_consistent() -> None:
    manifest = _manifest()
    gateway_version = re.search(
        r'__version__\s*=\s*"([^"]+)"',
        (ROOT / "gateway" / "nexus_gateway" / "__init__.py").read_text(encoding="utf-8"),
    ).group(1)

    assert manifest["appname"] == "nexus-gateway"
    assert manifest["version"] == f"{gateway_version}-fnos1"
    assert manifest["source"] == "thirdparty"
    assert manifest["platform"] == "all"
    assert manifest["desktop_uidir"] == "ui"
    assert manifest["desktop_applaunchname"] == "nexus-gateway.main"
    assert manifest["service_port"] == "8787"
    assert manifest["checkport"] == "true"
    assert manifest["ctl_stop"] == "true"
    assert manifest["disable_authorization_path"] == "true"
    assert "changelog" not in manifest

    ui = json.loads((PACKAGE / "app" / "ui" / "config").read_text(encoding="utf-8"))
    entry = ui[".url"][manifest["desktop_applaunchname"]]
    assert entry["type"] == "url"
    assert entry["protocol"] == "http"
    assert entry["port"] == manifest["service_port"]
    assert entry["url"] == "/"


def test_fnos_compose_uses_public_versioned_image_and_private_package_data() -> None:
    compose = yaml.safe_load((PACKAGE / "app" / "docker" / "docker-compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["nexus-gateway"]
    gateway_version = re.search(
        r'__version__\s*=\s*"([^"]+)"',
        (ROOT / "gateway" / "nexus_gateway" / "__init__.py").read_text(encoding="utf-8"),
    ).group(1)

    assert service["image"] == f"ghcr.io/trizen7/nexus-gateway:{gateway_version}"
    assert "build" not in service
    assert service["container_name"] == "nexus-gateway-fnos"
    assert service["user"] == "${TRIM_UID}:${TRIM_GID}"
    assert service["ports"] == ["${TRIM_SERVICE_PORT}:8787"]
    assert service["volumes"][0] == "${TRIM_PKGVAR}:/data"
    assert all("shares" not in volume.lower() for volume in service["volumes"])
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert service["extra_hosts"] == ["host.docker.internal:host-gateway"]
    healthcheck_command = " ".join(service["healthcheck"]["test"])
    assert "/api/setup/status" in healthcheck_command
    assert "initialized" in healthcheck_command
    assert service["healthcheck"]["interval"] == "30s"
    assert service["healthcheck"]["timeout"] == "5s"
    assert service["healthcheck"]["retries"] == 5
    assert service["healthcheck"]["start_period"] == "20s"
    assert service["entrypoint"] == ["python", "/opt/nexus/fnos_entrypoint.py"]
    assert service["command"][:3] == ["python", "-m", "nexus_gateway"]
    assert "NEXUS_PASSWORD" not in service["environment"]
    assert "HERMES_API_TOKEN" not in service["environment"]
    assert "NEXUS_SESSION_SECRET" not in service["environment"]

    resource = json.loads((PACKAGE / "config" / "resource").read_text(encoding="utf-8"))
    assert resource == {
        "docker-project": {
            "projects": [{"name": "nexus-gateway", "path": "docker"}],
        }
    }


def test_fnos_wizards_mark_secrets_as_password_and_embed_no_credentials() -> None:
    install = json.loads((PACKAGE / "wizard" / "install").read_text(encoding="utf-8"))
    config = json.loads((PACKAGE / "wizard" / "config").read_text(encoding="utf-8"))
    install_items = [item for step in install for item in step["items"] if "field" in item]
    config_items = [item for step in config for item in step["items"] if "field" in item]
    install_by_field = {item["field"]: item for item in install_items}
    config_by_field = {item["field"]: item for item in config_items}

    assert install_by_field["wizard_nexus_password"]["type"] == "password"
    assert install_by_field["wizard_hermes_api_token"]["type"] == "password"
    assert config_by_field["wizard_nexus_password"]["type"] == "password"
    assert config_by_field["wizard_hermes_api_token"]["type"] == "password"
    assert "initValue" not in install_by_field["wizard_nexus_password"]
    assert "initValue" not in install_by_field["wizard_hermes_api_token"]
    assert "initValue" not in config_by_field["wizard_nexus_password"]
    assert "initValue" not in config_by_field["wizard_hermes_api_token"]


def test_fnos_icons_have_required_dimensions_and_size() -> None:
    expected = {
        PACKAGE / "ICON.PNG": (64, 64),
        PACKAGE / "ICON_256.PNG": (256, 256),
        PACKAGE / "app" / "ui" / "images" / "icon_64.png": (64, 64),
        PACKAGE / "app" / "ui" / "images" / "icon_256.png": (256, 256),
    }
    for path, size in expected.items():
        assert _png_size(path) == size
        assert path.stat().st_size <= 1024 * 1024


def test_fnos_package_tree_contains_no_macos_metadata_and_text_is_clean() -> None:
    assert not list(PACKAGE.rglob(".DS_Store"))
    assert not list(PACKAGE.rglob("__pycache__"))
    assert not list(PACKAGE.rglob("*.pyc"))
    packaged_license = (PACKAGE / "LICENSE").read_text(encoding="utf-8")
    assert packaged_license.startswith((ROOT / "LICENSE").read_text(encoding="utf-8").rstrip())
    assert packaged_license.endswith((ROOT / "NOTICE").read_text(encoding="utf-8").strip() + "\n")

    text_paths = [path for path in PACKAGE.rglob("*") if path.is_file() and path.suffix.lower() != ".png"]
    text_paths.append(ROOT / "docs" / "fnos-deployment.md")
    for path in text_paths:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert b"\r\n" not in raw, path
        content = raw.decode("utf-8")
        assert "\x0c" not in content, path
        assert "\t" not in content, path
        assert "???" not in content, path


def test_fnos_lifecycle_scripts_only_manage_nexus_owned_paths_and_container() -> None:
    scripts = "\n".join(path.read_text(encoding="utf-8") for path in (PACKAGE / "cmd").iterdir() if path.is_file())
    forbidden = [
        r"systemctl\s+(?:start|stop|restart).*hermes",
        r"docker\s+(?:start|stop|restart|rm).*hermes",
        r"pkill.*hermes",
        r"/opt/hermes",
        r"/var/lib/hermes",
    ]
    for pattern in forbidden:
        assert re.search(pattern, scripts, re.IGNORECASE) is None
    assert 'TRIM_PKGVAR' in scripts
    install_callback = (PACKAGE / "cmd" / "install_callback").read_text(encoding="utf-8")
    config_callback = (PACKAGE / "cmd" / "config_callback").read_text(encoding="utf-8")
    for callback in (install_callback, config_callback):
        assert "docker inspect nexus-gateway-fnos" in callback
        assert "docker restart nexus-gateway-fnos" in callback
    setup_common = (PACKAGE / "cmd" / "setup_common.sh").read_text(encoding="utf-8")
    assert "host.docker.internal" in setup_common
    assert "127.*" in setup_common


def test_fnos_entrypoint_initializes_gateway_without_plaintext_password(tmp_path: Path) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
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
    assert config["hermes_api_url"] == "http://host.docker.internal:8000"
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
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://127.1.2.3:8000",
        "http://0.0.0.0:8000",
        "http://[::1]:8000",
        "http://[::]:8000",
    ],
)
def test_fnos_entrypoint_rejects_invalid_hermes_urls(tmp_path: Path, invalid_url: str) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
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
        ("http://localhost:8000", "http://host.docker.internal:8000"),
        ("http://127.0.0.1:8000/api", "http://host.docker.internal:8000/api"),
        ("https://[::1]:8443", "https://host.docker.internal:8443"),
        ("http://0.0.0.0:9000", "http://host.docker.internal:9000"),
    ],
)
def test_fnos_entrypoint_migrates_legacy_container_local_hermes_urls(
    tmp_path: Path,
    legacy_url: str,
    expected_url: str,
) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
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
def test_fnos_entrypoint_applies_partial_configuration_without_erasing_other_values(
    tmp_path: Path,
    changed_field: str,
) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
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
def test_fnos_entrypoint_rejects_invalid_account_revision(tmp_path: Path, invalid_revision: object) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
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


def test_fnos_entrypoint_rejects_setup_symlink(tmp_path: Path) -> None:
    module = _load_entrypoint()
    _point_entrypoint_at(module, tmp_path)
    target = tmp_path / "outside"
    target.mkdir()
    try:
        (tmp_path / ".fnos-setup").symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable")
    with pytest.raises(module.SetupError, match="unsafe"):
        module.apply_pending_setup()


def test_fnos_build_and_container_workflows_are_reproducible_contracts() -> None:
    script = (ROOT / "scripts" / "build_fnos_package.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "container.yml").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    verifier = (ROOT / "scripts" / "verify_fnos_package.py").read_text(encoding="utf-8")

    assert '$OfficialFnpackVersion = "1.2.3"' in script
    assert "d7af4bd716b009c58f5bcd931615f39db121e7d4b75dc759e575c4fb2879b6ee" in script
    assert "54b97fa7b70968c4d05c79840f5daeff508957d0bb2062fdb0376d00d9615c93" in script
    assert "Nexus-fnOS-$PackageVersion.fpk" in script
    assert "docker build" not in script.lower()
    assert "linux/amd64,linux/arm64" in workflow
    assert "docker/build-push-action@v6" in workflow
    assert "ghcr.io/trizen7/nexus-gateway" in workflow
    assert "fnpack-${FNPACK_VERSION}-linux-amd64" in workflow
    assert "sha256sum --check --strict" in workflow
    assert "verify_fnos_package.py" in workflow
    assert "verify_fnos_package.py" in release_workflow
    compile(verifier, "scripts/verify_fnos_package.py", "exec")

    for workflow_path in [
        ROOT / ".github" / "workflows" / "container.yml",
        ROOT / ".github" / "workflows" / "release.yml",
    ]:
        parsed = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        for job in parsed["jobs"].values():
            for step in job.get("steps", []):
                if step.get("shell") == "python":
                    compile(step["run"], f"{workflow_path}:{step.get('name', 'python step')}", "exec")
