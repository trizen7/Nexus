from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_TEST_SCRIPT = ROOT / "scripts" / "local_test.py"
MANAGE_SCRIPT = ROOT / "scripts" / "product-test-environment" / "manage.ps1"

HERMES_STORAGE_MARKERS = (
    re.compile(r"(?i)\.hermes(?:[\\/]|[\"'])"),
    re.compile(r"(?i)hermes-agent(?:[\\/]|[\"'])"),
    re.compile(r"(?i)appdata[\\/]local[\\/]hermes(?:[\\/]|$)"),
)
PATH_MUTATION_METHODS = {
    "chmod",
    "hardlink_to",
    "lchmod",
    "link_to",
    "mkdir",
    "rename",
    "replace",
    "rmdir",
    "symlink_to",
    "touch",
    "unlink",
    "write_bytes",
    "write_text",
}


def _function_node(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"Missing function: {name}")


def _source_files() -> list[Path]:
    roots = (
        ROOT / "android" / "app" / "src",
        ROOT / "gateway" / "nexus_gateway",
        ROOT / "scripts",
    )
    suffixes = {".bat", ".cmd", ".java", ".js", ".kt", ".kts", ".ps1", ".py", ".sh", ".xml"}
    files: list[Path] = []
    for source_root in roots:
        for path in source_root.rglob("*"):
            if path.is_file() and path.suffix.lower() in suffixes and "__pycache__" not in path.parts:
                files.append(path)
    return files


def test_repository_instructions_make_hermes_original_and_read_only():
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    required_rules = (
        "禁止修改任何 Hermes 文件",
        "禁止管理 Hermes 安装和进程",
        "只允许通过原版 Hermes HTTP API 集成",
        "连接信息只读",
        "所有 Nexus 写入必须限制在 Nexus 自有目录",
        "测试不得操控真实 Hermes",
    )
    for rule in required_rules:
        assert rule in instructions


def test_hermes_connection_discovery_is_read_only():
    source = LOCAL_TEST_SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    candidates = _function_node(tree, "_hermes_config_candidates")
    discovery = _function_node(tree, "discover_hermes_connection")

    discovery_source = ast.get_source_segment(source, discovery) or ""
    assert "config_path.read_text" in discovery_source
    assert "without writing to or managing Hermes" in discovery_source

    for function in (candidates, discovery):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in PATH_MUTATION_METHODS


def test_only_read_only_discovery_mentions_hermes_storage_paths():
    local_source = LOCAL_TEST_SCRIPT.read_text(encoding="utf-8")
    local_tree = ast.parse(local_source)
    candidates = _function_node(local_tree, "_hermes_config_candidates")
    allowed_lines = set(range(candidates.lineno, (candidates.end_lineno or candidates.lineno) + 1))

    violations: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8-sig")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not any(marker.search(line) for marker in HERMES_STORAGE_MARKERS):
                continue
            if path == LOCAL_TEST_SCRIPT and line_number in allowed_lines:
                continue
            violations.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    assert not violations, "Hermes storage path referenced outside read-only discovery:" + chr(10) + chr(10).join(violations)


def test_management_tools_never_install_update_or_control_hermes():
    command_with_hermes = re.compile(
        r"(?i)(?:\b(?:start-process|stop-process|taskkill|kill|pkill|pip|uv|winget|choco|brew|apt|get-process)\b.*\bhermes(?:-agent)?\b"
        r"|\bhermes(?:-agent)?\b.*\b(?:install|update|upgrade|downgrade|uninstall|start|stop|restart|kill)\b)"
    )
    violations: list[str] = []
    for path in _source_files():
        if path.suffix.lower() not in {".bat", ".cmd", ".ps1", ".py", ".sh"}:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
            if command_with_hermes.search(line):
                violations.append(f"{path.relative_to(ROOT)}:{line_number}: {line.strip()}")
    assert not violations, "Hermes installation/process command found:" + chr(10) + chr(10).join(violations)


def test_nexus_cleanup_and_deployment_are_confined_to_nexus_directories():
    local_source = LOCAL_TEST_SCRIPT.read_text(encoding="utf-8")
    manage_source = MANAGE_SCRIPT.read_text(encoding="utf-8-sig")

    assert "def _safe_remove_tree" in local_source
    assert "拒绝清理本地测试目录之外的路径" in local_source
    assert "function Assert-ManagedPath" in manage_source
    assert "Refusing to modify a path outside the deployed test environment" in manage_source
    assert "Start-Process -FilePath $python" in manage_source
    assert '& $bootstrapPython -m venv $VenvDir' in manage_source
    assert "Get-Command uv" not in manage_source
    assert "hermes-agent" not in manage_source.casefold()
