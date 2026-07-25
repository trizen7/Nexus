from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
GATEWAY_DIR = ROOT / "gateway"
LOCAL_DIR = ROOT / ".local-test"
VENV_DIR = LOCAL_DIR / "venv"
DATA_DIR = LOCAL_DIR / "data"
LOG_DIR = LOCAL_DIR / "logs"
ACCESS_FILE = LOCAL_DIR / "access.json"
PROCESS_FILE = LOCAL_DIR / "process.json"
RUNTIME_FILE = LOCAL_DIR / "runtime.json"
DEPENDENCY_STATE_FILE = LOCAL_DIR / "dependency-state.json"
DEPENDENCY_LOCK_FILE = LOCAL_DIR / "requirements.lock.txt"
PIP_CACHE_DIR = LOCAL_DIR / "cache" / "pip"
GATEWAY_LOG_FILE = LOG_DIR / "gateway.log"
REQUIREMENTS_FILES = (
    GATEWAY_DIR / "requirements.txt",
    GATEWAY_DIR / "requirements-dev.txt",
)
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 18787
COMMANDS = (
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
)
VENV_COMMANDS = {"setup", "start", "restart", "smoke", "upgrade", "verify"}
SECRET_ENV_KEYS = {
    "NEXUS_USERNAME",
    "NEXUS_PASSWORD",
    "NEXUS_SESSION_SECRET",
    "HERMES_API_URL",
    "HERMES_API_TOKEN",
    "NEXUS_LOCAL_HERMES_TOKEN",
}
MAX_HTTP_BODY_BYTES = 4 * 1024 * 1024


class LocalTestError(RuntimeError):
    """A safe, user-facing local test environment error."""


@dataclass(frozen=True)
class HermesConnection:
    url: str
    token: str
    config_path: Path | None


@dataclass(frozen=True)
class HttpResult:
    status: int
    payload: Any
    truncated: bool = False


@dataclass(frozen=True)
class ServiceProbe:
    ok: bool
    status: str
    version: str
    http_status: int | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def managed_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _is_external_dependency_python(path: Path) -> bool:
    try:
        parts = path.resolve().parts
    except OSError:
        parts = path.parts
    return any(part.casefold() == "hermes" for part in parts)


def _assert_safe_bootstrap_python() -> None:
    if _is_external_dependency_python(Path(sys.executable)):
        raise LocalTestError(
            "拒绝使用 Hermes 虚拟环境运行 Nexus 工具；请设置 NEXUS_PYTHON 指向独立的系统 Python"
        )


def gateway_host() -> str:
    return os.getenv("NEXUS_LOCAL_TEST_HOST", DEFAULT_GATEWAY_HOST).strip() or DEFAULT_GATEWAY_HOST


def gateway_port() -> int:
    raw = os.getenv("NEXUS_LOCAL_TEST_PORT", str(DEFAULT_GATEWAY_PORT)).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise LocalTestError("NEXUS_LOCAL_TEST_PORT 必须是有效端口号") from exc
    if not 1 <= port <= 65535:
        raise LocalTestError("NEXUS_LOCAL_TEST_PORT 必须在 1 到 65535 之间")
    return port


def gateway_url() -> str:
    host = gateway_host()
    url_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{url_host}:{gateway_port()}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Nexus 非 Docker 本地测试环境",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "常用流程:\n"
            "  local-test setup      首次搭建并执行冒烟测试\n"
            "  local-test upgrade    同步迭代、重启并执行冒烟测试\n"
            "  local-test verify     执行完整非 Docker 回归门禁\n"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    descriptions = {
        "setup": "创建或修复本地测试环境，启动后执行冒烟测试",
        "start": "按当前源码、依赖和 Nexus 上游连接副本启动；变化时自动重启",
        "stop": "仅停止本地测试 Gateway，不停止 Hermes",
        "restart": "只读获取 Hermes 连接信息并重启本地测试 Gateway",
        "status": "显示本地测试环境、Nexus 和 Hermes 状态",
        "smoke": "确保环境为当前迭代并验证登录和 Hermes 会话代理",
        "upgrade": "强制同步 Nexus 依赖和上游连接副本，重启并执行冒烟测试",
        "reset": "清除本地测试数据和日志，保留隔离虚拟环境",
        "credentials": "显式显示本地测试账号（会输出本地测试密码）",
        "verify": "执行 Gateway、网页和 Android 的完整非 Docker 回归测试",
    }
    for command in COMMANDS:
        subparsers.add_parser(command, help=descriptions[command], description=descriptions[command])
    return parser


def _is_managed_python() -> bool:
    candidate = managed_python()
    if not candidate.is_file():
        return False
    try:
        return Path(sys.executable).resolve() == candidate.resolve()
    except OSError:
        return False


def _requirements_digest() -> str:
    digest = hashlib.sha256()
    for path in REQUIREMENTS_FILES:
        if not path.is_file():
            raise LocalTestError(f"缺少依赖文件: {path}")
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    payload = json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        temporary.unlink(missing_ok=True)


def _dependency_imports_work(python: Path) -> bool:
    if not python.is_file():
        return False
    result = subprocess.run(
        [str(python), "-c", "import aiohttp, pytest, yaml"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _ensure_venv_and_dependencies(*, force_sync: bool) -> None:
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    python = managed_python()
    expected_digest = _requirements_digest()
    state = _read_json(DEPENDENCY_STATE_FILE) or {}
    needs_rebuild = (
        force_sync
        or state.get("requirements_sha256") != expected_digest
        or not _dependency_imports_work(python)
    )
    if not needs_rebuild:
        return
    if _is_managed_python():
        raise LocalTestError("当前进程正在使用受管虚拟环境。请通过 local-test.cmd 或 local-test.ps1 从独立系统 Python 重新启动")

    stop_gateway(quiet=True)
    _safe_remove_tree(VENV_DIR)
    PIP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pip_env = os.environ.copy()
    pip_env["PIP_CACHE_DIR"] = str(PIP_CACHE_DIR)
    pip_env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    pip_env["PIP_NO_INPUT"] = "1"

    print("[local-test] 创建隔离 Python 虚拟环境…", flush=True)
    create_result = subprocess.run(
        [sys.executable, "-m", "venv", str(VENV_DIR)],
        cwd=ROOT,
        env=pip_env,
        check=False,
    )
    if create_result.returncode != 0 or not python.is_file():
        raise LocalTestError("无法创建 .local-test/venv")

    print("[local-test] 使用 pip 安装固定版本 Gateway 开发依赖…", flush=True)
    install_result = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--requirement",
            str(GATEWAY_DIR / "requirements-dev.txt"),
        ],
        cwd=ROOT,
        env=pip_env,
        check=False,
    )
    if install_result.returncode != 0 or not _dependency_imports_work(python):
        raise LocalTestError("本地测试依赖安装失败")
    freeze_result = subprocess.run(
        [str(python), "-m", "pip", "freeze", "--all"],
        cwd=ROOT,
        env=pip_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if freeze_result.returncode != 0:
        raise LocalTestError("无法记录本地测试依赖版本")
    DEPENDENCY_LOCK_FILE.write_text(freeze_result.stdout, encoding="utf-8", newline="\n")
    _write_private_json(DEPENDENCY_STATE_FILE, {
        "schema_version": 1,
        "requirements_sha256": expected_digest,
        "synced_at": utc_now(),
    })


def _prepare_python_runtime(command: str) -> int | None:
    if command not in VENV_COMMANDS:
        return None
    _ensure_venv_and_dependencies(force_sync=command == "upgrade")
    if _is_managed_python():
        return None
    env = os.environ.copy()
    env["NEXUS_LOCAL_TEST_REEXEC"] = "1"
    result = subprocess.run(
        [str(managed_python()), str(Path(__file__).resolve()), *sys.argv[1:]],
        cwd=ROOT,
        env=env,
        check=False,
    )
    return result.returncode


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _nested(mapping: Mapping[str, Any], *keys: str) -> Any:
    current: Any = mapping
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_nonempty(*values: Any) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _normalize_hermes_url(raw_url: str) -> str:
    value = raw_url.strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LocalTestError("Hermes API 地址必须是有效的 http:// 或 https:// 地址")
    if parsed.username or parsed.password:
        raise LocalTestError("Hermes API 地址中不能包含账号或密码")
    return value


def _connection_url_from_host(host: str, port: Any) -> str:
    normalized_host = host.strip() or "127.0.0.1"
    if normalized_host.startswith(("http://", "https://")):
        parsed = urllib.parse.urlsplit(normalized_host)
        if parsed.port is not None:
            return _normalize_hermes_url(normalized_host)
        normalized_host = parsed.hostname or "127.0.0.1"
        scheme = parsed.scheme
    else:
        scheme = "http"
    if normalized_host in {"0.0.0.0", "::", "[::]", "*"}:
        normalized_host = "127.0.0.1"
    try:
        normalized_port = int(str(port).strip() or "8642")
    except ValueError as exc:
        raise LocalTestError("Hermes API Server 端口无效") from exc
    if not 1 <= normalized_port <= 65535:
        raise LocalTestError("Hermes API Server 端口必须在 1 到 65535 之间")
    if ":" in normalized_host and not normalized_host.startswith("["):
        normalized_host = f"[{normalized_host}]"
    return f"{scheme}://{normalized_host}:{normalized_port}"


def hermes_connection_from_mapping(
    config: Mapping[str, Any],
    *,
    environment: Mapping[str, str] | None = None,
    config_path: Path | None = None,
) -> HermesConnection:
    env = environment if environment is not None else os.environ
    api_server = _nested(config, "platforms", "api_server")
    if not isinstance(api_server, Mapping):
        api_server = {}
    extra = api_server.get("extra")
    if not isinstance(extra, Mapping):
        extra = {}

    explicit_url = _first_nonempty(
        env.get("NEXUS_LOCAL_HERMES_URL"),
        env.get("HERMES_API_URL"),
        config.get("API_SERVER_URL"),
        api_server.get("url"),
        extra.get("url"),
    )
    token = _first_nonempty(
        env.get("NEXUS_LOCAL_HERMES_TOKEN"),
        env.get("HERMES_API_TOKEN"),
        # Hermes Desktop persists environment-style values at the root and
        # may leave an older generated value under platforms.api_server.extra.
        config.get("API_SERVER_KEY"),
        config.get("HERMES_API_TOKEN"),
        extra.get("key"),
        extra.get("api_key"),
        extra.get("token"),
        api_server.get("key"),
        api_server.get("api_key"),
    )
    enabled = _as_bool(
        api_server.get("enabled") if "enabled" in api_server else config.get("API_SERVER_ENABLED"),
        default=True,
    )
    if not enabled and not explicit_url:
        raise LocalTestError("Hermes API Server 在配置中未启用")

    if explicit_url:
        url = _normalize_hermes_url(explicit_url)
    else:
        host = _first_nonempty(
            extra.get("host"),
            api_server.get("host"),
            config.get("API_SERVER_HOST"),
            "127.0.0.1",
        )
        port = _first_nonempty(
            extra.get("port"),
            api_server.get("port"),
            config.get("API_SERVER_PORT"),
            "8642",
        )
        url = _connection_url_from_host(host, port)
    if not token:
        raise LocalTestError("Hermes 配置中未找到 API Server Key")
    return HermesConnection(url=url, token=token, config_path=config_path)


def _hermes_config_candidates(environment: Mapping[str, str]) -> list[Path]:
    # These paths are discovery inputs only. Nexus must never create, edit, move,
    # or delete any Hermes file.
    candidates: list[Path] = []
    override = _first_nonempty(
        environment.get("NEXUS_LOCAL_HERMES_CONFIG"),
        environment.get("HERMES_CONFIG_FILE"),
    )
    if override:
        candidates.append(Path(override).expanduser())
    local_app_data = environment.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        candidates.append(Path(local_app_data) / "Hermes" / "config.yaml")
        candidates.append(Path(local_app_data) / "Hermes" / "config.yml")
    home = Path.home()
    candidates.extend((
        home / ".hermes" / "config.yaml",
        home / ".config" / "hermes" / "config.yaml",
        home / "Library" / "Application Support" / "Hermes" / "config.yaml",
    ))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            key = str(candidate.absolute()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def discover_hermes_connection(environment: Mapping[str, str] | None = None) -> HermesConnection:
    """Read Hermes connection details without writing to or managing Hermes."""
    env = environment if environment is not None else os.environ
    explicit_url = _first_nonempty(env.get("NEXUS_LOCAL_HERMES_URL"), env.get("HERMES_API_URL"))
    explicit_token = _first_nonempty(env.get("NEXUS_LOCAL_HERMES_TOKEN"), env.get("HERMES_API_TOKEN"))
    candidates = _hermes_config_candidates(env)
    config_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    config: Mapping[str, Any] = {}
    if config_path is not None:
        try:
            import yaml

            loaded = yaml.safe_load(config_path.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, Mapping):
                config = loaded
        except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError):
            raise LocalTestError(f"Hermes 配置无法读取或解析: {config_path}") from None
    elif not (explicit_url and explicit_token):
        checked = candidates[0] if candidates else Path("config.yaml")
        raise LocalTestError(
            f"未找到 Hermes 配置: {checked}。可设置 NEXUS_LOCAL_HERMES_CONFIG 指定路径"
        )
    return hermes_connection_from_mapping(config, environment=env, config_path=config_path)


def public_hermes_info(connection: HermesConnection) -> dict[str, str | None]:
    return {
        "url": connection.url,
        "config_path": str(connection.config_path) if connection.config_path else None,
    }



def _direct_url_opener() -> urllib.request.OpenerDirector:
    # Local management must bypass ambient HTTP(S) proxy settings so loopback probes
    # always reach the isolated Gateway directly.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_json(
    method: str,
    url: str,
    *,
    payload: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float = 5.0,
) -> HttpResult:
    body = None
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    if payload is not None:
        body = json.dumps(dict(payload), ensure_ascii=False).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=request_headers, method=method)
    try:
        response = _direct_url_opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_HTTP_BODY_BYTES + 1)
        truncated = len(raw) > MAX_HTTP_BODY_BYTES
        raw = raw[:MAX_HTTP_BODY_BYTES]
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeError, json.JSONDecodeError, ValueError):
            parsed = None
        return HttpResult(status=exc.code, payload=parsed, truncated=truncated)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise LocalTestError(f"无法连接服务: {urllib.parse.urlsplit(url).netloc}") from exc
    with response:
        raw = response.read(MAX_HTTP_BODY_BYTES + 1)
        truncated = len(raw) > MAX_HTTP_BODY_BYTES
        raw = raw[:MAX_HTTP_BODY_BYTES]
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeError, json.JSONDecodeError, ValueError):
            parsed = None
        return HttpResult(status=response.status, payload=parsed, truncated=truncated)


def probe_service(url: str) -> ServiceProbe:
    try:
        result = _http_json("GET", f"{url.rstrip('/')}/health", timeout=4.0)
    except LocalTestError:
        return ServiceProbe(False, "unreachable", "", None)
    payload = result.payload if isinstance(result.payload, Mapping) else {}
    status = str(payload.get("status") or "unknown")
    version = _first_nonempty(payload.get("version"), _nested(payload, "upstream", "version"))
    return ServiceProbe(
        ok=result.status == 200 and status == "ok",
        status=status,
        version=version,
        http_status=result.status,
    )


def _load_or_create_access() -> dict[str, str]:
    access = _read_json(ACCESS_FILE) or {}
    username = str(access.get("username") or "").strip()
    password = str(access.get("password") or "")
    if len(username) >= 3 and len(password) >= 8:
        return {"username": username, "password": password}
    created = {
        "schema_version": 1,
        "username": "local-admin",
        "password": secrets.token_urlsafe(24),
        "created_at": utc_now(),
    }
    _write_private_json(ACCESS_FILE, created)
    return {"username": created["username"], "password": created["password"]}


def _account_is_usable(account: Mapping[str, Any] | None, username: str) -> bool:
    if not account:
        return False
    return bool(
        str(account.get("username") or "") == username
        and str(account.get("password_salt") or "")
        and str(account.get("password_hash") or "")
    )


def _ensure_local_configuration(connection: HermesConnection) -> tuple[dict[str, str], bool]:
    # This writes only Nexus-owned files under .local-test/data. The supplied
    # Hermes connection is a copied value and is never written back to Hermes.
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "media").mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    access = _load_or_create_access()

    account_path = DATA_DIR / "account.json"
    account = _read_json(account_path)
    if not _account_is_usable(account, access["username"]):
        sys.path.insert(0, str(GATEWAY_DIR))
        try:
            from nexus_gateway.app import _hash_password
        except ImportError as exc:
            raise LocalTestError("无法加载 Nexus Gateway 认证组件") from exc
        password_salt, password_hash = _hash_password(access["password"])
        _write_private_json(account_path, {
            "username": access["username"],
            "password_scheme": "scrypt",
            "password_salt": password_salt,
            "password_hash": password_hash,
            "revision": 1,
        })

    config_path = DATA_DIR / "config.json"
    previous_config = _read_json(config_path) or {}
    session_secret = str(previous_config.get("session_secret") or "")
    if len(session_secret) < 16:
        session_secret = secrets.token_urlsafe(48)
    desired_config = {
        "hermes_api_url": connection.url.rstrip("/"),
        "hermes_api_token": connection.token,
        "session_secret": session_secret,
    }
    config_changed = any(previous_config.get(key) != value for key, value in desired_config.items())
    if config_changed or not config_path.is_file():
        _write_private_json(config_path, desired_config)
    (DATA_DIR / "bootstrap.token").unlink(missing_ok=True)
    return access, config_changed


def _hash_paths(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _source_digest() -> str:
    paths: list[Path] = []
    allowed_suffixes = {".py", ".html", ".css", ".js", ".txt", ".ini"}
    for path in GATEWAY_DIR.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        if any(part in {"__pycache__", ".pytest_cache", ".venv", "data", "logs"} for part in path.parts):
            continue
        paths.append(path)
    for name in ("local_test.py", "local-test.ps1", "local-test.cmd"):
        path = ROOT / "scripts" / name
        if path.is_file():
            paths.append(path)
    return _hash_paths(paths)


def _file_digest(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short=12", "HEAD"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def _windows_process_identity(pid: int) -> dict[str, str] | None:
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)) or exit_code.value != still_active:
            return None
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        creation_id = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        executable = ""
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            executable = buffer.value
        return {"creation_id": creation_id, "executable": executable}
    finally:
        kernel32.CloseHandle(handle)


def _posix_process_identity(pid: int) -> dict[str, str] | None:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return None
    executable = ""
    creation_id = ""
    proc = Path("/proc") / str(pid)
    try:
        executable = str((proc / "exe").resolve())
    except OSError:
        pass
    try:
        stat_line = (proc / "stat").read_text(encoding="utf-8")
        fields_after_command = stat_line[stat_line.rfind(")") + 2:].split()
        creation_id = fields_after_command[19]
    except (OSError, IndexError, ValueError):
        pass
    return {"creation_id": creation_id, "executable": executable}


def _process_identity(pid: int) -> dict[str, str] | None:
    if pid <= 0:
        return None
    return _windows_process_identity(pid) if os.name == "nt" else _posix_process_identity(pid)


def _load_process_record() -> dict[str, Any] | None:
    return _read_json(PROCESS_FILE)


def _owned_running_process() -> tuple[dict[str, Any], dict[str, str]] | None:
    record = _load_process_record()
    if not record:
        return None
    try:
        pid = int(record.get("pid", 0))
    except (TypeError, ValueError):
        PROCESS_FILE.unlink(missing_ok=True)
        return None
    identity = _process_identity(pid)
    if identity is None:
        PROCESS_FILE.unlink(missing_ok=True)
        return None
    recorded_creation = str(record.get("creation_id") or "")
    if recorded_creation and identity.get("creation_id") and recorded_creation != identity["creation_id"]:
        PROCESS_FILE.unlink(missing_ok=True)
        return None
    return record, identity


def _wait_until_stopped(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _process_identity(pid) is None:
            return True
        time.sleep(0.2)
    return _process_identity(pid) is None


def stop_gateway(*, quiet: bool = False) -> bool:
    owned = _owned_running_process()
    if owned is None:
        PROCESS_FILE.unlink(missing_ok=True)
        if not quiet:
            print("[local-test] Gateway 已停止", flush=True)
        return False
    record, _identity = owned
    pid = int(record["pid"])
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if not _wait_until_stopped(pid, 5.0):
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not _wait_until_stopped(pid, 5.0):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if not _wait_until_stopped(pid, 5.0):
        raise LocalTestError("无法停止本地测试 Gateway；为安全起见未处理其他进程")
    PROCESS_FILE.unlink(missing_ok=True)
    if not quiet:
        print("[local-test] Gateway 已停止", flush=True)
    return True



def _port_is_open(host: str, port: int) -> bool:
    connect_host = "127.0.0.1" if host in {"0.0.0.0", "::", "*"} else host.strip("[]")
    try:
        with socket.create_connection((connect_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _launch_fingerprints() -> dict[str, str]:
    return {
        "source_sha256": _source_digest(),
        "requirements_sha256": _requirements_digest(),
        "config_sha256": _file_digest(DATA_DIR / "config.json"),
    }


def _running_process_is_current(record: Mapping[str, Any]) -> bool:
    fingerprints = _launch_fingerprints()
    return (
        record.get("git_commit") == _git_commit()
        and all(record.get(key) == value for key, value in fingerprints.items())
    )


def _wait_for_gateway_ready(pid: int, timeout: float = 25.0) -> ServiceProbe:
    deadline = time.monotonic() + timeout
    last_probe = ServiceProbe(False, "starting", "", None)
    while time.monotonic() < deadline:
        if _process_identity(pid) is None:
            raise LocalTestError(f"Gateway 启动后退出，请检查日志: {GATEWAY_LOG_FILE}")
        last_probe = probe_service(gateway_url())
        if last_probe.ok:
            return last_probe
        if last_probe.status == "setup_required":
            raise LocalTestError("Gateway 本地配置未完成初始化")
        time.sleep(0.4)
    raise LocalTestError(
        f"Gateway 未在限定时间内就绪（最后状态: {last_probe.status}），请检查日志: {GATEWAY_LOG_FILE}"
    )


def _write_runtime_snapshot(connection: HermesConnection, gateway_probe: ServiceProbe) -> None:
    hermes_probe = probe_service(connection.url)
    _write_private_json(RUNTIME_FILE, {
        "schema_version": 1,
        "updated_at": utc_now(),
        "git_commit": _git_commit(),
        "source_sha256": _source_digest(),
        "requirements_sha256": _requirements_digest(),
        "gateway_url": gateway_url(),
        "gateway_version": gateway_probe.version,
        "hermes_url": connection.url,
        "hermes_version": hermes_probe.version,
        "hermes_config_path": str(connection.config_path) if connection.config_path else None,
    })


def start_gateway(connection: HermesConnection, *, force_restart: bool = False) -> bool:
    owned = _owned_running_process()
    if owned is not None:
        record, _identity = owned
        current = _running_process_is_current(record)
        healthy = probe_service(gateway_url()).ok
        if not force_restart and current and healthy:
            print(f"[local-test] Gateway 已在运行: {gateway_url()}", flush=True)
            return False
        print("[local-test] 检测到迭代或配置变化，重启 Gateway…", flush=True)
        stop_gateway(quiet=True)

    host = gateway_host()
    port = gateway_port()
    if _port_is_open(host, port):
        raise LocalTestError(f"本地测试端口 {port} 已被其他进程占用")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with GATEWAY_LOG_FILE.open("a", encoding="utf-8", newline="\n") as log:
        log.write(f"\n=== Nexus local test start {utc_now()} commit={_git_commit()} ===\n")
    log_handle = GATEWAY_LOG_FILE.open("ab", buffering=0)
    command = [
        str(managed_python()),
        "-m",
        "nexus_gateway",
        "--host",
        host,
        "--port",
        str(port),
    ]
    env = os.environ.copy()
    for key in SECRET_ENV_KEYS:
        env.pop(key, None)
    env.update({
        "PYTHONUTF8": "1",
        "PYTHONUNBUFFERED": "1",
        "NEXUS_GATEWAY_HOST": host,
        "NEXUS_GATEWAY_PORT": str(port),
        "NEXUS_CREDENTIALS_FILE": str(DATA_DIR / "account.json"),
        "NEXUS_CONFIG_FILE": str(DATA_DIR / "config.json"),
        "NEXUS_BOOTSTRAP_TOKEN_FILE": str(DATA_DIR / "bootstrap.token"),
        "NEXUS_MEDIA_DIR": str(DATA_DIR / "media"),
    })
    popen_kwargs: dict[str, Any] = {
        "cwd": GATEWAY_DIR,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        popen_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(command, **popen_kwargs)
    finally:
        log_handle.close()

    identity = None
    for _ in range(20):
        identity = _process_identity(process.pid)
        if identity is not None:
            break
        time.sleep(0.05)
    if identity is None:
        raise LocalTestError(f"Gateway 进程未能启动，请检查日志: {GATEWAY_LOG_FILE}")
    record: dict[str, Any] = {
        "schema_version": 1,
        "pid": process.pid,
        "creation_id": identity.get("creation_id", ""),
        "executable": identity.get("executable", ""),
        "started_at": utc_now(),
        "gateway_url": gateway_url(),
        "git_commit": _git_commit(),
        **_launch_fingerprints(),
    }
    _write_private_json(PROCESS_FILE, record)
    try:
        probe = _wait_for_gateway_ready(process.pid)
    except LocalTestError:
        stop_gateway(quiet=True)
        raise
    _write_runtime_snapshot(connection, probe)
    print(f"[local-test] Gateway 已启动: {gateway_url()}", flush=True)
    return True


def smoke_test() -> None:
    access = _read_json(ACCESS_FILE) or {}
    username = str(access.get("username") or "")
    password = str(access.get("password") or "")
    if not username or not password:
        raise LocalTestError("本地测试账号缺失，请先运行 setup")
    health = probe_service(gateway_url())
    if not health.ok:
        raise LocalTestError(f"Nexus 健康检查失败: {health.status}")
    login = _http_json(
        "POST",
        f"{gateway_url()}/api/auth/login",
        payload={"username": username, "password": password},
        timeout=8.0,
    )
    login_payload = login.payload if isinstance(login.payload, Mapping) else {}
    access_token = str(login_payload.get("access_token") or "")
    if login.status != 200 or not access_token:
        raise LocalTestError(f"Nexus 本地测试账号登录失败（HTTP {login.status}）")
    sessions = _http_json(
        "GET",
        f"{gateway_url()}/api/sessions",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15.0,
    )
    if not 200 <= sessions.status < 300:
        raise LocalTestError(f"Nexus 到 Hermes 的会话代理失败（HTTP {sessions.status}）")
    print("[local-test] 冒烟测试通过：健康检查、登录认证、Hermes 会话代理均正常", flush=True)


def _prepare_connection_and_data() -> HermesConnection:
    connection = discover_hermes_connection()
    hermes_probe = probe_service(connection.url)
    if not hermes_probe.ok:
        raise LocalTestError(f"Hermes API Server 当前不可用: {connection.url}")
    _, changed = _ensure_local_configuration(connection)
    source = str(connection.config_path) if connection.config_path else "环境变量"
    print(f"[local-test] Hermes 已发现: {connection.url}（配置来源: {source}）", flush=True)
    if changed:
        print("[local-test] 已更新 Nexus 自有目录中的 Hermes 连接信息副本（未写入 Hermes，密钥未输出）", flush=True)
    return connection


def command_setup() -> int:
    connection = _prepare_connection_and_data()
    start_gateway(connection)
    smoke_test()
    print("[local-test] 本地测试环境已就绪。需要查看测试账号时运行 credentials", flush=True)
    return 0


def command_start() -> int:
    connection = _prepare_connection_and_data()
    start_gateway(connection)
    return 0


def command_restart() -> int:
    connection = _prepare_connection_and_data()
    stop_gateway(quiet=True)
    start_gateway(connection, force_restart=True)
    return 0


def command_smoke() -> int:
    connection = _prepare_connection_and_data()
    start_gateway(connection)
    smoke_test()
    return 0


def command_upgrade() -> int:
    connection = _prepare_connection_and_data()
    stop_gateway(quiet=True)
    start_gateway(connection, force_restart=True)
    smoke_test()
    print("[local-test] 本地测试环境已升级到当前迭代", flush=True)
    return 0


def _service_label(probe: ServiceProbe) -> str:
    suffix = f" v{probe.version}" if probe.version else ""
    if probe.ok:
        return f"正常{suffix}"
    if probe.http_status is None:
        return "不可连接"
    return f"异常（{probe.status}, HTTP {probe.http_status}）{suffix}"


def command_status() -> int:
    account_ready = (DATA_DIR / "account.json").is_file() and (DATA_DIR / "config.json").is_file()
    owned = _owned_running_process()
    gateway_probe = probe_service(gateway_url()) if owned else ServiceProbe(False, "stopped", "", None)
    saved_config = _read_json(DATA_DIR / "config.json") or {}
    hermes_url = str(saved_config.get("hermes_api_url") or "")
    hermes_probe = probe_service(hermes_url) if hermes_url else ServiceProbe(False, "not_configured", "", None)
    runtime = _read_json(RUNTIME_FILE) or {}

    print(f"本地目录: {LOCAL_DIR}")
    print(f"环境数据: {'已就绪' if account_ready else '未初始化'}")
    print(f"Gateway 进程: {'运行中' if owned else '已停止'}")
    print(f"Nexus: {_service_label(gateway_probe)}")
    print(f"Hermes: {_service_label(hermes_probe)}")
    print(f"测试地址: {gateway_url()}")
    if runtime.get("git_commit"):
        print(f"环境迭代: {runtime['git_commit']}")
    return 0 if account_ready and owned and gateway_probe.ok and hermes_probe.ok else 1


def _safe_remove_tree(path: Path) -> None:
    local_root = LOCAL_DIR.resolve()
    target = path.resolve()
    if target == local_root or local_root not in target.parents:
        raise LocalTestError(f"拒绝清理本地测试目录之外的路径: {target}")
    if path.exists():
        shutil.rmtree(path)


def command_reset() -> int:
    stop_gateway(quiet=True)
    _safe_remove_tree(DATA_DIR)
    _safe_remove_tree(LOG_DIR)
    for path in (ACCESS_FILE, PROCESS_FILE, RUNTIME_FILE):
        path.unlink(missing_ok=True)
    print("[local-test] 已清除测试数据、账号和日志；隔离虚拟环境已保留", flush=True)
    print("[local-test] 运行 setup 可按当前迭代重新搭建", flush=True)
    return 0


def command_credentials() -> int:
    access = _read_json(ACCESS_FILE) or {}
    username = str(access.get("username") or "")
    password = str(access.get("password") or "")
    if not username or not password:
        raise LocalTestError("本地测试账号尚未创建，请先运行 setup")
    print("警告：以下仅为本机测试账号，请勿复制到 Issue、日志或提交中。")
    print(f"地址: {gateway_url()}")
    print(f"账号: {username}")
    print(f"密码: {password}")
    return 0


def _run_gate(label: str, command: Sequence[str], cwd: Path) -> None:
    print(f"[local-test] {label}…", flush=True)
    result = subprocess.run(list(command), cwd=cwd, check=False)
    if result.returncode != 0:
        raise LocalTestError(f"{label}失败（退出码 {result.returncode}）")


def command_verify() -> int:
    connection = _prepare_connection_and_data()
    start_gateway(connection)
    smoke_test()
    _run_gate(
        "Gateway 单元与契约测试",
        [str(managed_python()), "-m", "pytest", "tests", "-q"],
        GATEWAY_DIR,
    )
    node = shutil.which("node")
    if not node:
        raise LocalTestError("未找到 Node.js，无法执行网页契约测试")
    _run_gate("网页契约测试", [node, "tests/web_contract_test.js"], GATEWAY_DIR)
    gradle = ROOT / "android" / ("gradlew.bat" if os.name == "nt" else "gradlew")
    if not gradle.is_file():
        raise LocalTestError("未找到 Android Gradle Wrapper")
    _run_gate(
        "Android 单元测试、Lint 与 Debug 构建",
        [str(gradle), "testDebugUnitTest", "lintDebug", "assembleDebug", "--no-daemon"],
        ROOT / "android",
    )
    print("[local-test] 完整非 Docker 回归门禁通过", flush=True)
    return 0


def dispatch(command: str) -> int:
    handlers = {
        "setup": command_setup,
        "start": command_start,
        "stop": lambda: (stop_gateway(), 0)[1],
        "restart": command_restart,
        "status": command_status,
        "smoke": command_smoke,
        "upgrade": command_upgrade,
        "reset": command_reset,
        "credentials": command_credentials,
        "verify": command_verify,
    }
    return handlers[command]()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        _assert_safe_bootstrap_python()
        bootstrap_result = _prepare_python_runtime(args.command)
        if bootstrap_result is not None:
            return bootstrap_result
        return dispatch(args.command)
    except LocalTestError as exc:
        print(f"[local-test] 错误: {exc}", file=sys.stderr, flush=True)
        return 1
    except KeyboardInterrupt:
        print("[local-test] 已取消", file=sys.stderr, flush=True)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
