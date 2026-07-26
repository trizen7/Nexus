from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"


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


def test_release_builds_two_self_contained_fnos_packages_and_uploads_only_supported_assets() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert '--platform "linux/$architecture"' in workflow
    assert '--tag "nexus-gateway-fnos:$VERSION"' in workflow
    assert '--output "type=docker,dest=$RUNNER_TEMP/nexus-gateway-$architecture.tar"' in workflow
    assert 'gzip --no-name --best "$RUNNER_TEMP/nexus-gateway-$architecture.tar"' in workflow
    assert "-Platform amd64" in workflow
    assert "-Platform arm64" in workflow
    assert "--require-fnos" in workflow
    assert "dist/*" not in workflow
    assert "Nexus-fnOS-${{ steps.release-metadata.outputs.fnos_version }}-amd64.fpk" in workflow
    assert "Nexus-fnOS-${{ steps.release-metadata.outputs.fnos_version }}-arm64.fpk" in workflow
    assert "Nexus-Android-*.aab" not in workflow
    assert "release-manifest.json" not in workflow
    assert "renease-manifest.json" not in workflow
    assert '--notes ""' in workflow
