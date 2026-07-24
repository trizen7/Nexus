from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
GATEWAY = ROOT / "gateway"


def test_dockerfile_runs_gateway_as_non_root_with_healthcheck():
    dockerfile = (GATEWAY / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM python:3.11-slim" in dockerfile
    assert "addgroup --system --gid 10001 nexus" in dockerfile
    assert "adduser --system --uid 10001" in dockerfile
    assert "USER nexus" in dockerfile
    assert "HEALTHCHECK" in dockerfile
    assert '"python", "-m", "nexus_gateway"' in dockerfile
    assert "0.0.0.0" in dockerfile
    assert "http://127.0.0.1:8787/health" in dockerfile
    assert "--tls-dir" not in dockerfile
    assert "NEXUS_TLS" not in dockerfile
    assert "_create_unverified_context" not in dockerfile


def test_compose_persists_data_and_does_not_embed_secrets():
    compose = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["nexus-gateway"]

    assert service["restart"] == "unless-stopped"
    assert service["image"] == "nexus-mobile-gateway:0.0.11"
    assert service["ports"] == ["8787:8787"]
    assert service["volumes"] == ["./data:/data"]
    assert "env_file" not in service
    assert service["environment"]["NEXUS_CONFIG_FILE"] == "/data/config.json"
    assert service["environment"]["NEXUS_MEDIA_DIR"] == "/data/media"
    assert service["environment"]["NEXUS_CREDENTIALS_FILE"] == "/data/account.json"
    assert service["environment"]["NEXUS_BOOTSTRAP_TOKEN_FILE"] == "/data/bootstrap.token"
    assert "NEXUS_MAX_TOTAL_STORAGE_BYTES" in service["environment"]
    assert "NEXUS_MIN_FREE_DISK_BYTES" in service["environment"]
    assert "NEXUS_PASSWORD" not in service["environment"]
    assert "HERMES_API_TOKEN" not in service["environment"]
    assert "NEXUS_TLS_DIR" not in service["environment"]
    assert "NEXUS_TLS_HOSTS" not in service["environment"]
    healthcheck = " ".join(service["healthcheck"]["test"])
    assert "http://127.0.0.1:8787/health" in healthcheck
    assert "https://" not in healthcheck
    assert service["logging"]["options"] == {"max-size": "10m", "max-file": "3"}


def test_root_runtime_data_is_ignored_by_git():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "data/" in gitignore
    assert "gateway/data/" in gitignore
