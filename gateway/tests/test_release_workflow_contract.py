from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
CONTAINER_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "container.yml"


def test_release_gateway_python_checks_run_from_gateway_package_root() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    expected_step = """      - name: Test Gateway Python
        working-directory: gateway
        run: |
          python -m pytest tests -q
          python -m compileall -q nexus_gateway
"""
    assert expected_step in workflow
    assert "python -m pytest gateway/tests -q" not in workflow


def test_release_builds_native_fnos_packages_and_uploads_only_supported_assets() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "gateway/FnOS.Dockerfile" in workflow
    assert '--platform "linux/$architecture"' in workflow
    assert '--output "type=local,dest=$RUNNER_TEMP/nexus-runtime-$architecture"' in workflow
    assert "-Platform amd64" in workflow
    assert "-Platform arm64" in workflow
    assert "-RuntimeDirectoryPath" in workflow
    assert "--require-fnos" in workflow
    assert "dist/*" not in workflow
    assert "Nexus-fnOS-${{ steps.release-metadata.outputs.fnos_version }}-amd64.fpk" in workflow
    assert "Nexus-fnOS-${{ steps.release-metadata.outputs.fnos_version }}-arm64.fpk" in workflow
    assert "Nexus-Android-*.aab" not in workflow
    assert "release-manifest.json" not in workflow
    assert "renease-manifest.json" not in workflow
    assert '--notes ""' in workflow

    for forbidden in (
        "type=docker",
        "gzip --no-name",
        "ImageArchivePath",
        "nexus-gateway-fnos:$VERSION",
        "docker load",
        "docker save",
    ):
        assert forbidden not in workflow


def test_all_workflows_are_valid_yaml_and_ci_keeps_platform_jobs_separate() -> None:
    parsed = {
        path.name: yaml.safe_load(path.read_text(encoding="utf-8"))
        for path in (CI_WORKFLOW, CONTAINER_WORKFLOW, RELEASE_WORKFLOW)
    }
    assert set(parsed[CI_WORKFLOW.name]["jobs"]) == {
        "gateway",
        "fnos-native-package",
        "android",
    }
    gateway_steps = parsed[CI_WORKFLOW.name]["jobs"]["gateway"]["steps"]
    gateway_commands = "\n".join(str(step.get("run", "")) for step in gateway_steps)
    assert "scripts/scan_repository_secrets.py" in gateway_commands
    assert "scripts/build_release.py --validate-only" in gateway_commands
    assert "python -m pip_audit -r requirements.txt" in gateway_commands
    assert "fnos-package" in parsed[CONTAINER_WORKFLOW.name]["jobs"]
    assert "release" in parsed[RELEASE_WORKFLOW.name]["jobs"]


def test_fnos_workflows_build_runtime_without_device_time_network_dependencies() -> None:
    for path in (CI_WORKFLOW, CONTAINER_WORKFLOW, RELEASE_WORKFLOW):
        workflow = path.read_text(encoding="utf-8")
        assert "gateway/FnOS.Dockerfile" in workflow
        assert 'type=local,dest=$RUNNER_TEMP/nexus-runtime-$architecture' in workflow
        assert "-RuntimeDirectoryPath" in workflow
        assert "ImageArchivePath" not in workflow
        assert "docker load" not in workflow.lower()
        assert "docker save" not in workflow.lower()
