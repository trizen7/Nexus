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