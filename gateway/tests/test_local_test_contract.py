from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import zipfile
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
    assert "function Archive-ExistingLog" in script
    assert "Move-ManagedItem $fullPath $archivePath" in script
    assert "function Write-ManagedTextFile" in script
    assert "[System.IO.File]::Replace($temporary, $safe, $backup, $true)" in script
    assert "$stdoutLog = Assert-ManagedPath $stdoutLog" in script
    assert "$stderrLog = Assert-ManagedPath $stderrLog" in script
    assert "Archive-ExistingLog $stdoutLog" in script
    assert "Archive-ExistingLog $stderrLog" in script


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
    assert '& $bootstrapPython -m venv $managedVenvDir' in product
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


def test_credentials_command_never_prints_password(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    module = load_local_test_module()
    access_file = tmp_path / "access.json"
    access_file.write_text(json.dumps({
        "username": "test-user",
        "password": "do-not-print-this-password",
    }), encoding="utf-8")
    monkeypatch.setattr(module, "ACCESS_FILE", access_file)
    monkeypatch.setattr(module, "gateway_url", lambda: "http://127.0.0.1:18787")

    assert module.command_credentials() == 0
    captured = capsys.readouterr()
    assert "test-user" in captured.out
    assert "do-not-print-this-password" not in captured.out
    assert "do-not-print-this-password" not in captured.err


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


def test_verify_gate_never_invokes_docker(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = load_local_test_module()
    connection = module.HermesConnection("http://127.0.0.1:8642", "test-token", None)
    commands: list[list[str]] = []
    monkeypatch.setattr(module, "_prepare_connection_and_data", lambda: connection)
    monkeypatch.setattr(module, "start_gateway", lambda _connection: False)
    monkeypatch.setattr(module, "smoke_test", lambda: None)
    monkeypatch.setattr(module.shutil, "which", lambda name: "node" if name == "node" else None)
    monkeypatch.setattr(module, "_run_gate", lambda _label, command, _cwd: commands.append(list(command)))
    source_apk = tmp_path / "build" / "app-debug.apk"
    delivered_apk = tmp_path / "product" / "app-debug.apk"
    source_apk.parent.mkdir()
    source_apk.write_bytes(b"debug-apk")
    monkeypatch.setattr(module, "DEBUG_APK_SOURCE", source_apk)
    monkeypatch.setattr(module, "DEBUG_APK_DELIVERY", delivered_apk)

    assert module.command_verify() == 0
    assert delivered_apk.read_bytes() == b"debug-apk"
    flattened = " ".join(part for command in commands for part in command).lower()
    assert "pytest" in flattened
    assert "web_contract_test.js" in flattened
    assert "gradlew" in flattened
    assert "docker" not in flattened


def test_debug_apk_delivery_atomically_replaces_existing_file(tmp_path: Path):
    module = load_local_test_module()
    source = tmp_path / "source.apk"
    destination = tmp_path / "成品" / "test" / "app-debug.apk"
    source.write_bytes(b"new-apk")
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old-apk")

    module._deliver_debug_apk(source, destination)

    assert destination.read_bytes() == b"new-apk"
    assert not list(destination.parent.glob("*.tmp"))


def test_product_upgrade_requires_one_exact_checksum_entry():
    script = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    verify = script.split("function Verify-Artifact", 1)[1].split(
        "function Ensure-Dependencies", 1
    )[0]

    assert 'if (-not (Test-Path -LiteralPath $sumFile -PathType Leaf))' in verify
    assert "SHA256SUMS.txt is required" in verify
    assert "contains a malformed entry" in verify
    assert "$matchingEntries.Count -ne 1" in verify
    assert "$actual -ne [string]$matchingEntries[0]" in verify
    assert "Select-Object -First 1" not in verify
    assert "-ceq $ExpectedName" in verify


@pytest.mark.parametrize(
    ("checksum_contents", "should_pass"),
    (
        ("valid", True),
        (None, False),
        ("duplicate", False),
        ("malformed", False),
        ("mismatch", False),
    ),
)
def test_product_artifact_checksum_validation_executes_strictly(
    tmp_path: Path, checksum_contents: str | None, should_pass: bool
):
    artifact = tmp_path / "Nexus-Gateway-1.2.3.zip"
    artifact.write_bytes(b"nexus-gateway-test-artifact")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if checksum_contents == "valid":
        contents = f"{digest}  {artifact.name}\n"
    elif checksum_contents == "duplicate":
        contents = f"{digest}  {artifact.name}\n{digest} *{artifact.name}\n"
    elif checksum_contents == "malformed":
        contents = f"not-a-sha256  {artifact.name}\n"
    elif checksum_contents == "mismatch":
        contents = f"{'0' * 64}  {artifact.name}\n"
    else:
        contents = None
    if contents is not None:
        (tmp_path / "SHA256SUMS.txt").write_text(contents, encoding="utf-8")

    source = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    verify = "function Verify-Artifact" + source.split(
        "function Verify-Artifact", 1
    )[1].split("function Ensure-Dependencies", 1)[0]
    harness = tmp_path / "verify-artifact.ps1"
    harness.write_text(
        "param([string]$ArtifactPath)\n"
        + verify
        + "\n$artifact = Get-Item -LiteralPath $ArtifactPath\n"
        + "$null = Verify-Artifact $artifact\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ArtifactPath",
            str(artifact),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert (result.returncode == 0) is should_pass, result.stderr


def test_product_prepare_expands_only_the_managed_verified_copy(tmp_path: Path):
    source_artifact = tmp_path / "product" / "Nexus-Gateway-1.2.3.zip"
    source_artifact.parent.mkdir()
    with zipfile.ZipFile(source_artifact, "w") as archive:
        archive.writestr("gateway/requirements.txt", "aiohttp==3.12.15\n")
        archive.writestr("gateway/nexus_gateway/__main__.py", "pass\n")
        archive.writestr("gateway/payload.txt", "verified-copy")
    (source_artifact.parent / "SHA256SUMS.txt").write_text(
        f"{hashlib.sha256(source_artifact.read_bytes()).hexdigest()}  {source_artifact.name}\n",
        encoding="utf-8",
    )

    source = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    managed_helpers = "function Assert-ManagedPath" + source.split(
        "function Assert-ManagedPath", 1
    )[1].split("function Get-VenvPython", 1)[0]
    prepare = "function Prepare-LatestArtifact" + source.split(
        "function Prepare-LatestArtifact", 1
    )[1].split("function Write-DeploymentState", 1)[0]
    harness = tmp_path / "prepare-managed-copy.ps1"
    harness.write_text(
        "param([string]$ManagedRoot, [string]$OriginalArtifact)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = [System.IO.Path]::GetFullPath($ManagedRoot).TrimEnd('\\')\n"
        + managed_helpers
        + "function Get-LatestArtifact { return Get-Item -LiteralPath $OriginalArtifact }\n"
        "function Verify-Artifact($Artifact, [string]$ChecksumFile, [string]$ExpectedName) {\n"
        "  $managedArtifactRoot = Assert-ManagedPath (Join-Path $Root 'artifact.next')\n"
        "  if (-not $Artifact.FullName.StartsWith($managedArtifactRoot + '\\')) { throw 'unmanaged artifact was verified' }\n"
        "  [System.IO.File]::WriteAllText($OriginalArtifact, 'source-changed-after-copy')\n"
        "  return 'verified-hash'\n"
        "}\n"
        + prepare
        + "\n$prepared = Prepare-LatestArtifact\n"
        "$payload = Join-Path $prepared.Staging 'gateway\\payload.txt'\n"
        "if ((Get-Content -LiteralPath $payload -Raw).Trim() -ne 'verified-copy') { throw 'managed copy was not expanded' }\n"
        "if (Test-Path -LiteralPath (Join-Path $Root 'artifact.next')) { throw 'artifact copy was not cleaned' }\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ManagedRoot",
            str(tmp_path / "managed"),
            "-OriginalArtifact",
            str(source_artifact),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_product_upgrade_retains_all_old_state_until_readiness_passes():
    script = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    transaction = script.split("function Get-UpgradeTransaction", 1)[1].split(
        "function Get-SetupStatus", 1
    )[0]
    upgrade = script.split("function Invoke-TransactionalUpgrade", 1)[1].split(
        "function Show-Status", 1
    )[0]

    for path in (
        'Join-Path $transactionRoot "app"',
        'Join-Path $transactionRoot "venv"',
        'Join-Path $transactionState "dependencies.json"',
        'Join-Path $transactionState "requirements.lock.txt"',
        'Join-Path $transactionState "deployment.json"',
    ):
        assert f"Assert-ManagedPath ({path})" in transaction

    assert upgrade.index("Begin-UpgradeTransaction") < upgrade.index(
        "Ensure-Dependencies"
    )
    assert upgrade.index("Ensure-Dependencies") < upgrade.index(
        "Start-Gateway -RequireExistingDeployment"
    )
    assert upgrade.index("Start-Gateway -RequireExistingDeployment") < upgrade.index(
        "Commit-UpgradeTransaction"
    )
    restore = transaction.split("function Restore-UpgradeTransaction", 1)[1].split(
        "function Commit-UpgradeTransaction", 1
    )[0]
    assert "Copy-ManagedItem $item.Backup $item.Current" in restore
    assert "Move-ManagedItem $item.Backup $item.Current" not in restore


def test_product_upgrade_failure_restores_and_restarts_previous_gateway():
    script = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    upgrade = script.split("function Invoke-TransactionalUpgrade", 1)[1].split(
        "function Show-Status", 1
    )[0]
    failure_path = upgrade.split("} catch {", 1)[1]

    assert "Stop-Gateway -Quiet" in failure_path
    assert "Restore-UpgradeTransaction $transaction" in failure_path
    assert "Start-Gateway -RequireExistingDeployment" in failure_path
    assert failure_path.index("Stop-Gateway -Quiet") < failure_path.index(
        "Restore-UpgradeTransaction $transaction"
    )
    assert failure_path.index("Restore-UpgradeTransaction $transaction") < failure_path.index(
        "Start-Gateway -RequireExistingDeployment"
    )
    restore_block = failure_path.split("if ($transactionStarted) {", 1)[1].split(
        "$oldEntryPoint", 1
    )[0]
    assert "Start-Gateway -RequireExistingDeployment" not in restore_block
    assert '"upgrade" { Invoke-TransactionalUpgrade }' in script


def test_product_upgrade_transaction_restores_app_venv_and_state(tmp_path: Path):
    source = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    managed_helpers = "function Assert-ManagedPath" + source.split(
        "function Assert-ManagedPath", 1
    )[1].split("function Get-VenvPython", 1)[0]
    transaction_functions = "function Get-UpgradeTransaction" + source.split(
        "function Get-UpgradeTransaction", 1
    )[1].split("function Get-SetupStatus", 1)[0]
    harness = tmp_path / "transaction-round-trip.ps1"
    harness.write_text(
        "param([string]$ManagedRoot)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = [System.IO.Path]::GetFullPath($ManagedRoot).TrimEnd('\\')\n"
        "$AppDir = Join-Path $Root 'app'\n"
        "$VenvDir = Join-Path $Root 'venv'\n"
        "$StateDir = Join-Path $Root 'state'\n"
        "$DeploymentFile = Join-Path $StateDir 'deployment.json'\n"
        + managed_helpers
        + transaction_functions
        + "\n$null = New-Item -ItemType Directory -Path $AppDir -Force\n"
        "$null = New-Item -ItemType Directory -Path $VenvDir -Force\n"
        "$null = New-Item -ItemType Directory -Path $StateDir -Force\n"
        "Set-Content -LiteralPath (Join-Path $AppDir 'marker') -Value 'old-app'\n"
        "Set-Content -LiteralPath (Join-Path $VenvDir 'marker') -Value 'old-venv'\n"
        "Set-Content -LiteralPath (Join-Path $StateDir 'dependencies.json') -Value 'old-dependencies'\n"
        "Set-Content -LiteralPath (Join-Path $StateDir 'requirements.lock.txt') -Value 'old-lock'\n"
        "Set-Content -LiteralPath $DeploymentFile -Value 'old-deployment'\n"
        "$transaction = Get-UpgradeTransaction\n"
        "Begin-UpgradeTransaction $transaction\n"
        "if (Test-Path -LiteralPath $AppDir) { throw 'old app was not moved' }\n"
        "if (Test-Path -LiteralPath $VenvDir) { throw 'old venv was not moved' }\n"
        "$null = New-Item -ItemType Directory -Path $AppDir -Force\n"
        "$null = New-Item -ItemType Directory -Path $VenvDir -Force\n"
        "Set-Content -LiteralPath (Join-Path $AppDir 'marker') -Value 'new-app'\n"
        "Set-Content -LiteralPath (Join-Path $VenvDir 'marker') -Value 'new-venv'\n"
        "Set-Content -LiteralPath (Join-Path $StateDir 'dependencies.json') -Value 'new-dependencies'\n"
        "Set-Content -LiteralPath (Join-Path $StateDir 'requirements.lock.txt') -Value 'new-lock'\n"
        "Set-Content -LiteralPath $DeploymentFile -Value 'new-deployment'\n"
        "$script:copyCount = 0\n"
        "function Copy-ManagedItem([string]$Source, [string]$Destination) {\n"
        "  $script:copyCount += 1\n"
        "  if ($script:copyCount -eq 2) { throw 'injected restore failure' }\n"
        "  $safeSource = Assert-ManagedPath $Source\n"
        "  $safeDestination = Assert-ManagedPath $Destination\n"
        "  Copy-Item -LiteralPath $safeSource -Destination $safeDestination -Recurse -Force\n"
        "}\n"
        "$restoreFailed = $false\n"
        "try { Restore-UpgradeTransaction $transaction } catch { $restoreFailed = $true }\n"
        "if (-not $restoreFailed) { throw 'restore failure was not injected' }\n"
        "if ((Get-Content (Join-Path $transaction.App 'marker')) -ne 'old-app') { throw 'app backup was consumed' }\n"
        "if ((Get-Content (Join-Path $transaction.Venv 'marker')) -ne 'old-venv') { throw 'venv backup was consumed' }\n"
        "if ((Get-Content $transaction.Dependencies) -ne 'old-dependencies') { throw 'dependency backup was consumed' }\n"
        "if ((Get-Content $transaction.Lock) -ne 'old-lock') { throw 'lock backup was consumed' }\n"
        "if ((Get-Content $transaction.Deployment) -ne 'old-deployment') { throw 'deployment backup was consumed' }\n"
        "function Copy-ManagedItem([string]$Source, [string]$Destination) {\n"
        "  $safeSource = Assert-ManagedPath $Source\n"
        "  $safeDestination = Assert-ManagedPath $Destination\n"
        "  Copy-Item -LiteralPath $safeSource -Destination $safeDestination -Recurse -Force\n"
        "}\n"
        "Restore-UpgradeTransaction $transaction\n"
        "if ((Get-Content (Join-Path $AppDir 'marker')) -ne 'old-app') { throw 'app restore failed' }\n"
        "if ((Get-Content (Join-Path $VenvDir 'marker')) -ne 'old-venv') { throw 'venv restore failed' }\n"
        "if ((Get-Content (Join-Path $StateDir 'dependencies.json')) -ne 'old-dependencies') { throw 'dependency state restore failed' }\n"
        "if ((Get-Content (Join-Path $StateDir 'requirements.lock.txt')) -ne 'old-lock') { throw 'lock restore failed' }\n"
        "if ((Get-Content $DeploymentFile) -ne 'old-deployment') { throw 'deployment restore failed' }\n"
        "if (Test-Path -LiteralPath $transaction.Root) { throw 'transaction cleanup failed' }\n",
        encoding="utf-8-sig",
    )
    managed_root = tmp_path / "managed"
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ManagedRoot",
            str(managed_root),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction contract")
def test_product_managed_path_rejects_junction_ancestor(tmp_path: Path):
    source = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    managed_helpers = "function Assert-ManagedPath" + source.split(
        "function Assert-ManagedPath", 1
    )[1].split("function Get-VenvPython", 1)[0]
    harness = tmp_path / "reject-junction.ps1"
    harness.write_text(
        "param([string]$ManagedRoot, [string]$OutsideRoot)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = [System.IO.Path]::GetFullPath($ManagedRoot).TrimEnd('\\')\n"
        + managed_helpers
        + "$null = New-Item -ItemType Directory -Path $Root -Force\n"
        "$null = New-Item -ItemType Directory -Path $OutsideRoot -Force\n"
        "$outsideFile = Join-Path $OutsideRoot 'dependencies.json'\n"
        "Set-Content -LiteralPath $outsideFile -Value 'preserve-me'\n"
        "$null = New-Item -ItemType Junction -Path (Join-Path $Root 'state') -Target $OutsideRoot\n"
        "$rejected = $false\n"
        "try { Remove-ManagedItem (Join-Path $Root 'state\\dependencies.json') } catch { $rejected = $_.Exception.Message -like '*reparse point*' }\n"
        "if (-not $rejected) { throw 'junction path was not rejected' }\n"
        "if ((Get-Content -LiteralPath $outsideFile) -ne 'preserve-me') { throw 'outside file was modified' }\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ManagedRoot",
            str(tmp_path / "managed"),
            "-OutsideRoot",
            str(tmp_path / "outside"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction and hardlink contract")
def test_product_write_helpers_reject_runtime_redirection_and_hardlinks(tmp_path: Path):
    source = (
        ROOT / "scripts" / "product-test-environment" / "manage.ps1"
    ).read_text(encoding="utf-8-sig")
    managed_helpers = "function Assert-ManagedPath" + source.split(
        "function Assert-ManagedPath", 1
    )[1].split("function Get-VenvPython", 1)[0]
    harness = tmp_path / "reject-managed-writes.ps1"
    harness.write_text(
        "param([string]$ManagedRoot, [string]$OutsideRoot)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "$Root = [System.IO.Path]::GetFullPath($ManagedRoot).TrimEnd('\\')\n"
        + managed_helpers
        + "$null = New-Item -ItemType Directory -Path $Root -Force\n"
        "$null = New-Item -ItemType Directory -Path $OutsideRoot -Force\n"
        "$normalFile = Join-Path $Root 'normal\\state.json'\n"
        "Write-ManagedTextFile $normalFile 'first'\n"
        "Write-ManagedTextFile $normalFile 'second'\n"
        "if ((Get-Content -LiteralPath $normalFile -Raw) -ne 'second') { throw 'managed atomic replacement failed' }\n"
        "Remove-ManagedTree (Join-Path $Root 'normal')\n"
        "$outsideFile = Join-Path $OutsideRoot 'external.json'\n"
        "Set-Content -LiteralPath $outsideFile -Value 'preserve-me' -NoNewline\n"
        "$hardlink = Join-Path $Root 'process.json'\n"
        "$null = New-Item -ItemType HardLink -Path $hardlink -Target $outsideFile\n"
        "$hardlinkRejected = $false\n"
        "try { Write-ManagedTextFile $hardlink 'changed' } catch { $hardlinkRejected = $_.Exception.Message -like '*hard-linked*' }\n"
        "if (-not $hardlinkRejected) { throw 'hardlink target was not rejected' }\n"
        "Remove-Item -LiteralPath $hardlink -Force\n"
        "foreach ($name in @('state', 'data', 'logs')) {\n"
        "  $junction = Join-Path $Root $name\n"
        "  $null = New-Item -ItemType Junction -Path $junction -Target $OutsideRoot\n"
        "  $rejected = $false\n"
        "  try { Write-ManagedTextFile (Join-Path $junction ($name + '.json')) 'changed' } catch { $rejected = $_.Exception.Message -like '*reparse point*' }\n"
        "  if (-not $rejected) { throw ($name + ' junction was not rejected') }\n"
        "  [System.IO.Directory]::Delete($junction)\n"
        "}\n"
        "if ((Get-Content -LiteralPath $outsideFile -Raw) -ne 'preserve-me') { throw 'outside file was modified' }\n",
        encoding="utf-8-sig",
    )
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ManagedRoot",
            str(tmp_path / "managed-writes"),
            "-OutsideRoot",
            str(tmp_path / "outside-writes"),
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_product_upgrade_documentation_declares_checksum_and_rollback_contract():
    documents = (
        (ROOT / "docs" / "local-test-environment.md").read_text(encoding="utf-8"),
        (
            ROOT / "scripts" / "product-test-environment" / "使用说明.txt"
        ).read_text(encoding="utf-8-sig"),
    )
    for document in documents:
        assert "SHA256SUMS.txt" in document
        assert "upgrade.previous" in document or "恢复旧状态" in document
        assert "就绪检查" in document
        assert "Hermes" in document


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
