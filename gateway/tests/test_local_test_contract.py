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
