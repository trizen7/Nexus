from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "local_test.py"


def load_local_test_module():
    spec = importlib.util.spec_from_file_location("nexus_local_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_local_test_runtime_is_git_ignored():
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".local-test/" in patterns


def test_product_test_controller_has_valid_paths_and_safe_process_recovery():
    script_path = ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    payload = script_path.read_bytes()
    script = payload.decode("utf-8")

    assert b"\r" not in payload.replace(b"\r\n", b"")
    assert r'"gateway\nexus_gateway\__main__.py"' in script
    assert r'"gateway\requirements.txt"' in script
    assert "ParentProcessId = {0}" in script
    assert "$ProcessRecoveryWindowSeconds = 10" in script
    assert "Multiple possible Nexus Gateway child processes" in script
    assert "Wait-ForManagedPortsClosed" in script
    assert '$Port = 18787' in script
    assert '$BaseUrl = "http://${HealthAddress}:$Port"' in script
    assert '$ManagedPorts = @(18787, 18788)' in script
    assert '-RemoteAddress LocalSubnet' in script
    assert '--tls-dir' not in script
    assert 'NEXUS_TLS_DIR' not in script
    assert 'Invoke-HttpsJson' not in script
    assert 'cryptography' not in script
    assert 'PYTHONNOUSERSITE = "1"' in script
    assert '"PYTHONPATH", "PYTHONHOME"' in script
    assert 'Get-ChildItem -LiteralPath $ProductRoot -Filter "Nexus-Gateway-*.zip" -File -Recurse' in script
    assert '$sumFile = Join-Path $Artifact.DirectoryName "SHA256SUMS.txt"' in script


def test_windows_release_script_preserves_the_chinese_product_path():
    script_path = ROOT / "scripts" / "build-android-release.ps1"
    payload = script_path.read_bytes()

    # Windows PowerShell 5.1 treats BOM-less scripts as the active ANSI code page.
    # Keep a UTF-8 BOM so the default product/v<version> output path is not mojibaked.
    assert payload.startswith(b"\xef\xbb\xbf")
    script = payload.decode("utf-8-sig")
    assert '("成品\\v" + $Version)' in script


def test_nexus_tools_do_not_bootstrap_from_the_hermes_virtual_environment():
    module = load_local_test_module()
    assert module._is_external_dependency_python(Path("C:/vendor/hermes/venv/python.exe"))
    assert not module._is_external_dependency_python(Path("C:/Python312/python.exe"))

    powershell = (ROOT / "scripts" / "local-test.ps1").read_text(encoding="utf-8-sig")
    release = (ROOT / "scripts" / "build-android-release.ps1").read_text(encoding="utf-8-sig")
    product = (ROOT / "scripts" / "product-test-environment" / "manage.ps1").read_text(encoding="utf-8-sig")
    cmd = (ROOT / "scripts" / "local-test.cmd").read_text(encoding="ascii")

    for script in (powershell, release, product):
        assert '$_ -ieq "hermes"' in script
        assert "NEXUS_PYTHON" in script
    assert '& $bootstrapPython -m venv $VenvDir' in product
    assert 'Get-Command uv' not in product
    assert 'Assert-ManagedPath (Join-Path $Root "cache\\pip")' in product
    assert module.PIP_CACHE_DIR == module.LOCAL_DIR / "cache" / "pip"
    local_source = (ROOT / "scripts" / "local_test.py").read_text(encoding="utf-8")
    assert 'pip_env["PIP_CACHE_DIR"] = str(PIP_CACHE_DIR)' in local_source
    assert 'shutil.which("uv")' not in local_source
    assert "local-test.ps1" in cmd
    assert "PYTHON=python" not in cmd


def test_local_test_cli_exposes_full_lifecycle():
    module = load_local_test_module()
    expected = {
        "setup",
        "start",
        "stop",
        "restart",
        "status",
        "smoke",
        "upgrade",
        "reset",
        "credentials",
        "verify",
    }
    assert set(module.COMMANDS) == expected
    parser = module.build_parser()
    for command in expected:
        assert parser.parse_args([command]).command == command


def test_local_test_defaults_to_http_origin_without_tls_state():
    module = load_local_test_module()
    assert module.DEFAULT_GATEWAY_PORT == 18787
    assert module.gateway_url() == "http://127.0.0.1:18787"
    assert not hasattr(module, "TLS_DIR")
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "--tls-dir" not in source
    assert "NEXUS_TLS_DIR" not in source
    assert "cryptography" not in source


def test_local_management_opener_bypasses_proxies_without_disabling_tls_verification():
    module = load_local_test_module()
    opener = module._direct_url_opener()
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    # An explicitly empty ProxyHandler suppresses urllib's environment-derived
    # proxy handler. CPython intentionally omits that empty handler from the
    # final opener list, so verify both the construction contract and result.
    assert "build_opener(urllib.request.ProxyHandler({}))" in source
    assert not any(
        isinstance(handler, module.urllib.request.ProxyHandler)
        for handler in opener.handlers
    )
    assert "_create_unverified_context" not in source


def test_hermes_desktop_root_key_wins_over_stale_nested_key(tmp_path: Path):
    module = load_local_test_module()
    connection = module.hermes_connection_from_mapping(
        {
            "API_SERVER_KEY": "current-root-test-key",
            "platforms": {
                "api_server": {
                    "enabled": True,
                    "extra": {
                        "host": "0.0.0.0",
                        "port": 8642,
                        "key": "stale-nested-test-key",
                    },
                },
            },
        },
        environment={},
        config_path=tmp_path / "config.yaml",
    )
    assert connection.url == "http://127.0.0.1:8642"
    assert connection.token == "current-root-test-key"


def test_explicit_hermes_overrides_are_supported_and_public_info_is_redacted():
    module = load_local_test_module()
    connection = module.hermes_connection_from_mapping(
        {},
        environment={
            "NEXUS_LOCAL_HERMES_URL": "http://localhost:9000/",
            "NEXUS_LOCAL_HERMES_TOKEN": "override-test-token",
        },
    )
    assert connection.url == "http://localhost:9000"
    assert connection.token == "override-test-token"
    public = module.public_hermes_info(connection)
    assert "token" not in public
    assert "override-test-token" not in json.dumps(public)


def test_running_process_currency_includes_git_commit(monkeypatch: pytest.MonkeyPatch):
    module = load_local_test_module()
    fingerprints = {
        "source_sha256": "source",
        "requirements_sha256": "requirements",
        "config_sha256": "config",
    }
    monkeypatch.setattr(module, "_launch_fingerprints", lambda: fingerprints)
    monkeypatch.setattr(module, "_git_commit", lambda: "current-commit")
    assert not module._running_process_is_current({**fingerprints, "git_commit": "previous-commit"})
    assert module._running_process_is_current({**fingerprints, "git_commit": "current-commit"})


def test_cleanup_guard_rejects_paths_outside_local_test_directory():
    module = load_local_test_module()
    with pytest.raises(module.LocalTestError):
        module._safe_remove_tree(ROOT / "gateway")


def test_verify_gate_never_invokes_docker(monkeypatch: pytest.MonkeyPatch):
    module = load_local_test_module()
    connection = module.HermesConnection("http://127.0.0.1:8642", "test-token", None)
    commands: list[list[str]] = []
    monkeypatch.setattr(module, "_prepare_connection_and_data", lambda: connection)
    monkeypatch.setattr(module, "start_gateway", lambda _connection: False)
    monkeypatch.setattr(module, "smoke_test", lambda: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr(module, "_run_gate", lambda _label, command, _cwd: commands.append(list(command)))

    assert module.command_verify() == 0
    flattened = " ".join(part for command in commands for part in command).lower()
    assert "pytest" in flattened
    assert "web_contract_test.js" in flattened
    assert "gradlew" in flattened
    assert "docker" not in flattened


def test_documentation_declares_iteration_upgrade_and_secret_boundary():
    document = (ROOT / "docs" / "local-test-environment.md").read_text(encoding="utf-8")
    for command in ("setup", "upgrade", "verify", "smoke", "reset"):
        assert f"local-test.cmd {command}" in document
    assert ".local-test/" in document
    assert "不会输出" in document
    assert "不调用 Docker" in document
    assert "HTTP 源站" in document
    assert "反向代理" in document
    assert "不要把" in document and "直接暴露到公网" in document
