from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, web

from . import __version__

CHUNK_SIZE = 256 * 1024
IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
TEXT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
    "application/xml",
    "text/xml",
}
SAFE_FILE_NAME = re.compile(r"[^\w.()\-\u4e00-\u9fff]+", re.UNICODE)
USERNAME_KEY = web.AppKey("username", str)
PASSWORD_KEY = web.AppKey("password", str)
CREDENTIALS_PATH_KEY = web.AppKey("credentials_path", Path)
CONFIG_PATH_KEY = web.AppKey("config_path", Path)
BOOTSTRAP_TOKEN_PATH_KEY = web.AppKey("bootstrap_token_path", Path)
GATEWAY_CONFIG_KEY = web.AppKey("gateway_config", object)
AUTH_STATE_KEY = web.AppKey("auth_state", object)
MEDIA_STORE_KEY = web.AppKey("media_store", object)
HTTP_SESSION_KEY = web.AppKey("http_session", ClientSession)
RUN_TRACKER_KEY = web.AppKey("run_tracker", object)
TRANSCRIBE_AUDIO_KEY = web.AppKey("transcribe_audio", object)
REQUEST_ATTACHMENT_IDS_KEY = web.RequestKey("nexus_attachment_ids", list)
LOGIN_RATE_LIMITER_KEY = web.AppKey("login_rate_limiter", object)
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
PUBLIC_PATHS = {
    "/",
    "/health",
    "/api/setup/status",
    "/api/setup",
    "/api/auth/login",
    "/assets/styles.css",
    "/assets/app.js",
}
WEB_ROOT = Path(__file__).with_name("web")


@dataclass
class AuthState:
    username: str
    password_salt: str
    password_hash: str
    legacy_password: str = ""
    revision: int = 1


@dataclass
class GatewayConfig:
    initialized: bool
    setup_available: bool
    session_secret: str
    upstream_url: str
    upstream_token: str


class RunTracker:
    """Own mobile chat runs outside Hermes so upstream stays unmodified."""

    SNAPSHOT_LIMIT = 256 * 1024

    def __init__(self, state_file: Path | None = None) -> None:
        self.state_file = Path(state_file).resolve() if state_file else None
        self.statuses: dict[str, dict[str, Any]] = self._load()
        self.tasks: dict[str, asyncio.Task] = {}
        self.subscribers: dict[str, set[asyncio.Queue]] = {}
        self.buffers: dict[str, str] = {}
        self.last_persisted: dict[str, float] = {}
        changed = False
        for session_id, status in self.statuses.items():
            if status.get("active") or status.get("status") == "running":
                status.update(
                    status="interrupted",
                    active=False,
                    phase="interrupted",
                    message="Nexus 移动网关重启，无法确认原任务是否仍在运行",
                    updated_at=time.time(),
                )
                changed = True
        if changed:
            self._save()

    def _load(self) -> dict[str, dict[str, Any]]:
        if self.state_file is None or not self.state_file.is_file():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        if self.state_file is None:
            return
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        temp = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        temp.write_text(json.dumps(self.statuses, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.state_file)

    def status(self, session_id: str) -> dict[str, Any]:
        return dict(self.statuses.get(session_id) or {
            "session_id": session_id,
            "run_id": None,
            "status": "idle",
            "active": False,
            "phase": "idle",
            "snapshot": "",
            "updated_at": time.time(),
        })

    def start(self, session_id: str, run_id: str) -> None:
        self.statuses[session_id] = {
            "session_id": session_id,
            "run_id": run_id,
            "status": "running",
            "active": True,
            "phase": "thinking",
            "snapshot": "",
            "tool_name": None,
            "updated_at": time.time(),
        }
        self.buffers[session_id] = ""
        self._save()

    def finish(self, session_id: str, status: str, message: str | None = None) -> None:
        current = self.statuses.setdefault(session_id, {"session_id": session_id})
        current.update(
            status=status,
            active=False,
            phase=status,
            message=message,
            updated_at=time.time(),
        )
        self._save()

    def consume_sse(self, session_id: str, chunk: bytes) -> None:
        text = self.buffers.get(session_id, "") + chunk.decode("utf-8", errors="replace")
        blocks = re.split(r"\r?\n\r?\n", text)
        self.buffers[session_id] = blocks.pop() if blocks else ""
        current = self.statuses.get(session_id)
        if current is None:
            return
        changed = False
        for block in blocks:
            event_name = ""
            data_lines = []
            for line in block.splitlines():
                if line.startswith("event:"):
                    event_name = line.partition(":")[2].strip()
                elif line.startswith("data:"):
                    data_lines.append(line.partition(":")[2].strip())
            try:
                payload = json.loads("\n".join(data_lines) or "{}")
            except json.JSONDecodeError:
                payload = {}
            if event_name == "assistant.delta":
                delta = str(payload.get("delta", ""))
                current["snapshot"] = (str(current.get("snapshot", "")) + delta)[-self.SNAPSHOT_LIMIT:]
                current["phase"] = "generating"
                changed = True
            elif event_name in {"tool.started", "tool.progress"}:
                current["phase"] = "tool"
                current["tool_name"] = payload.get("tool_name") or payload.get("tool")
                changed = True
            elif event_name in {"tool.completed", "tool.failed"}:
                current["phase"] = "thinking"
                current["tool_name"] = None
                changed = True
            elif event_name == "run.started":
                current["phase"] = "thinking"
                changed = True
            elif event_name in {"run.completed", "assistant.completed", "done"}:
                current["phase"] = "completed"
                changed = True
            elif event_name == "error":
                current["phase"] = "failed"
                current["message"] = payload.get("message")
                changed = True
        if changed:
            now = time.time()
            current["updated_at"] = now
            # Persist at most twice per second during rapid assistant deltas.
            # Terminal events call finish() and always flush immediately.
            if now - self.last_persisted.get(session_id, 0.0) >= 0.5:
                self.last_persisted[session_id] = now
                self._save()

    def publish(self, session_id: str, event: bytes) -> None:
        for queue in tuple(self.subscribers.get(session_id, set())):
            queue.put_nowait(event)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self.subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        queues = self.subscribers.get(session_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self.subscribers.pop(session_id, None)


@dataclass(frozen=True)
class StoredFile:
    id: str
    name: str
    mime_type: str
    size: int
    sha256: str
    created_at: float
    server_path: str
    category: str = "files"
    date: str = ""

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("server_path", None)
        data["date"] = self.date or time.strftime("%Y-%m-%d", time.localtime(self.created_at))
        data["download_url"] = f"/api/files/{self.id}"
        return data


class MediaStore:
    def __init__(
        self,
        root: Path,
        max_upload_bytes: int,
        max_total_storage_bytes: int = 10 * 1024 * 1024 * 1024,
        min_free_disk_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_upload_bytes = max(1, max_upload_bytes)
        self.max_total_storage_bytes = max(0, max_total_storage_bytes)
        self.min_free_disk_bytes = max(0, min_free_disk_bytes)

    def _stored_bytes(self) -> int:
        return sum(item["size"] for item in self.list_files())

    @staticmethod
    def _storage_error(code: str, message: str) -> web.HTTPInsufficientStorage:
        return web.HTTPInsufficientStorage(
            text=json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False),
            content_type="application/json",
        )

    def _check_capacity(self, existing_bytes: int, incoming_bytes: int) -> None:
        if self.max_total_storage_bytes and existing_bytes + incoming_bytes > self.max_total_storage_bytes:
            raise self._storage_error("storage_quota_exceeded", "存储空间配额不足，请删除旧文件后重试")
        if shutil.disk_usage(self.root).free - incoming_bytes < self.min_free_disk_bytes:
            raise self._storage_error("disk_space_low", "磁盘可用空间不足，请清理空间后重试")

    def _metadata_path(self, file_id: str, directory: Path | None = None) -> Path:
        return (directory or self.root) / f"{file_id}.json"

    def _metadata_candidates(self, file_id: str):
        yield self._metadata_path(file_id)
        yield from self.root.glob(f"*/*/{file_id}.json")

    def _read_metadata(self, file_id: str) -> StoredFile | None:
        if not _valid_file_id(file_id):
            return None
        for metadata in self._metadata_candidates(file_id):
            if not metadata.is_file():
                continue
            try:
                return StoredFile(**json.loads(metadata.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def get(self, file_id: str) -> tuple[StoredFile, Path] | None:
        item = self._read_metadata(file_id)
        if item is None:
            return None
        path = Path(item.server_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return item, path

    def list_files(self, category: str | None = None) -> list[dict[str, Any]]:
        items = []
        metadata_paths = list(self.root.glob("*.json")) + list(self.root.glob("*/*/*.json"))
        for metadata in metadata_paths:
            if metadata.name == "session_media.json":
                continue
            try:
                item = StoredFile(**json.loads(metadata.read_text(encoding="utf-8")))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if category is not None and item.category != category:
                continue
            if self.get(item.id) is not None:
                items.append(item.public_dict())
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    async def save(self, field: Any, category: str | None = None) -> StoredFile:
        original_name = Path(field.filename or "附件").name
        clean_name = SAFE_FILE_NAME.sub("_", original_name).strip("._") or "附件"
        mime_type = (field.headers.get("Content-Type") or mimetypes.guess_type(clean_name)[0] or "application/octet-stream").split(";", 1)[0]
        resolved_category = category or ("audio" if mime_type.startswith("audio/") else "files")
        date = time.strftime("%Y-%m-%d", time.localtime())
        category_dir = "语音" if resolved_category == "audio" else "文件"
        target_dir = self.root / category_dir / date
        target_dir.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        suffix = Path(clean_name).suffix[:16]
        final_path = target_dir / f"{file_id}{suffix}"
        temp_path = target_dir / f".{file_id}.upload"
        digest = hashlib.sha256()
        size = 0
        existing_bytes = self._stored_bytes()
        self._check_capacity(existing_bytes, 0)
        try:
            with temp_path.open("wb") as handle:
                while True:
                    chunk = await field.read_chunk(size=CHUNK_SIZE)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise web.HTTPRequestEntityTooLarge(
                            max_size=self.max_upload_bytes,
                            actual_size=size,
                            text=json.dumps({"error": {"code": "file_too_large", "message": "文件超过上传大小限制"}}, ensure_ascii=False),
                            content_type="application/json",
                        )
                    self._check_capacity(existing_bytes, size)
                    digest.update(chunk)
                    handle.write(chunk)
            temp_path.replace(final_path)
            item = StoredFile(
                id=file_id,
                name=clean_name,
                mime_type=mime_type,
                size=size,
                sha256=digest.hexdigest(),
                created_at=time.time(),
                server_path=str(final_path),
                category=resolved_category,
                date=date,
            )
            self._metadata_path(file_id, target_dir).write_text(json.dumps(asdict(item), ensure_ascii=False, indent=2), encoding="utf-8")
            return item
        except Exception:
            temp_path.unlink(missing_ok=True)
            final_path.unlink(missing_ok=True)
            raise

    def delete(self, file_id: str) -> bool:
        found = self.get(file_id)
        if found is None:
            return False
        _, path = found
        path.unlink(missing_ok=True)
        for metadata in self._metadata_candidates(file_id):
            metadata.unlink(missing_ok=True)
        return True

    def record_session_media(
        self,
        session_id: str,
        file_ids: list[str],
        marker: str = "",
        attachment_kinds: dict[str, str] | None = None,
    ) -> None:
        items = []
        for file_id in file_ids:
            found = self.get(file_id)
            if found is not None:
                items.append(found[0])
        if not items:
            return
        index_path = self.root / "session_media.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            index = {}
        index.setdefault(session_id, []).append({
            "marker": marker,
            "file_ids": [item.id for item in items],
            "attachment_kinds": attachment_kinds or {},
        })
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    def discard_last_session_media(self, session_id: str, file_ids: list[str]) -> None:
        index_path = self.root / "session_media.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return
        entries = index.get(session_id)
        expected = [str(file_id) for file_id in file_ids]
        if not isinstance(entries, list):
            return
        for position in range(len(entries) - 1, -1, -1):
            entry = entries[position]
            current = [str(file_id) for file_id in entry.get("file_ids", [])]
            if current == expected:
                entries.pop(position)
                break
        if entries:
            index[session_id] = entries
        else:
            index.pop(session_id, None)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _visible_message_text(content: Any) -> str:
        if isinstance(content, str):
            parts = [content]
        elif isinstance(content, list):
            parts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
            ]
        else:
            return ""
        visible = []
        for part in parts:
            cleaned = part.replace("\n[screenshot]", "").replace("[screenshot]", "").strip()
            lines = cleaned.splitlines()
            attachment_index = next(
                (index for index, line in enumerate(lines) if line.strip().startswith("附件：")),
                None,
            )
            if attachment_index is not None:
                lines = lines[:attachment_index]
            cleaned = "\n".join(lines).strip()
            if cleaned:
                visible.append(cleaned)
        return "\n".join(visible).strip()

    def enrich_session_messages(self, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        index_path = self.root / "session_media.json"
        try:
            raw_entries = json.loads(index_path.read_text(encoding="utf-8")).get(session_id, [])
        except (OSError, ValueError, json.JSONDecodeError):
            return payload
        entries = []
        for entry in raw_entries:
            if "file_ids" in entry:
                entries.append(entry)
            elif entry.get("file_id"):
                entries.append({"marker": "", "file_ids": [entry["file_id"]]})
        pending = list(entries)
        for message in payload.get("data", []):
            if message.get("role") != "user" or not pending:
                continue
            content = message.get("content")
            visible_content = self._visible_message_text(content)
            full_content = visible_content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            match_index = next(
                (
                    index for index, candidate in enumerate(pending)
                    if str(candidate.get("marker", "")) and str(candidate.get("marker", "")) in full_content
                ),
                None,
            )
            if match_index is None:
                match_index = next(
                    (index for index, candidate in enumerate(pending) if not str(candidate.get("marker", ""))),
                    None,
                )
            if match_index is None:
                continue
            entry = pending.pop(match_index)
            has_images = False
            attachment_kinds = {
                str(key): str(value) for key, value in entry.get("attachment_kinds", {}).items()
            }
            for file_id in entry.get("file_ids", []):
                found = self.get(str(file_id))
                if found is None:
                    continue
                item, _ = found
                media = {
                    "id": item.id,
                    "url": f"/api/files/{item.id}",
                    "name": item.name,
                    "mime_type": item.mime_type,
                    "size": item.size,
                }
                if item.mime_type in IMAGE_MIME_TYPES and attachment_kinds.get(item.id) != "file":
                    has_images = True
                    message.setdefault("nexus_images", []).append({key: media[key] for key in ("id", "url", "name")})
                else:
                    message.setdefault("nexus_files", []).append(media)
            if has_images or entry.get("file_ids"):
                message["content"] = visible_content
        return payload


@web.middleware
async def security_headers(request: web.Request, handler):
    try:
        response = await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        response = web.json_response(
            {"error": {"code": "gateway_error", "message": "上游服务暂时不可用"}},
            status=502,
        )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cache-Control", "no-store")
    return response


def _secure_text_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    resolved_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=resolved_salt, n=2**14, r=8, p=1, dklen=32)
    return resolved_salt.hex(), digest.hex()


def _verify_password(password: str, auth_state: AuthState) -> bool:
    if auth_state.password_salt and auth_state.password_hash:
        try:
            _, candidate = _hash_password(password, bytes.fromhex(auth_state.password_salt))
        except ValueError:
            return False
        return hmac.compare_digest(candidate, auth_state.password_hash)
    return bool(auth_state.legacy_password) and _secure_text_equal(password, auth_state.legacy_password)


class LoginRateLimiter:
    """Per-IP sliding window limiter for /api/auth/login."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = max(1, limit)
        self.window = max(0.1, window_seconds)
        self.attempts: dict[str, list[float]] = {}

    def allow(self, client_ip: str) -> tuple[bool, float]:
        now = time.time()
        bucket = self.attempts.get(client_ip, [])
        cutoff = now - self.window
        bucket[:] = [t for t in bucket if t > cutoff]
        if len(bucket) >= self.limit:
            retry_after = max(0.0, self.window - (now - bucket[0])) if bucket else self.window
            return False, retry_after
        bucket.append(now)
        self.attempts[client_ip] = bucket
        return True, 0.0

    def record_success(self, client_ip: str) -> None:
        self.attempts.pop(client_ip, None)


def _client_ip(request: web.Request) -> str:
    return request.remote or "unknown"


def _encode_token(username: str, secret: str, revision: int = 1, expires_at: int | None = None) -> str:
    payload = f"{username}:{revision}:{expires_at or int(time.time()) + TOKEN_TTL_SECONDS}"
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def _decode_token(token: str, secret: str) -> tuple[str, int] | None:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padding = "=" * (-len(encoded) % 4)
        username, revision, expires_at = base64.urlsafe_b64decode(encoded + padding).decode().rsplit(":", 2)
        return (username, int(revision)) if int(expires_at) > time.time() else None
    except (ValueError, UnicodeDecodeError):
        return None


@web.middleware
async def device_auth(request: web.Request, handler):
    if request.path in PUBLIC_PATHS:
        return await handler(request)
    if not request.app[GATEWAY_CONFIG_KEY].initialized:
        return web.json_response(
            {"error": {"code": "setup_required", "message": "请先完成 Nexus 初始化"}},
            status=503,
        )
    supplied = request.headers.get("Authorization", "")
    identity = _decode_token(supplied[7:], request.app[GATEWAY_CONFIG_KEY].session_secret) if supplied.startswith("Bearer ") else None
    auth_state = request.app[AUTH_STATE_KEY]
    if identity != (auth_state.username, auth_state.revision):
        return web.json_response({"error": {"code": "unauthorized", "message": "登录已失效，请重新登录"}}, status=401)
    return await handler(request)


async def _create_client_session(app: web.Application) -> None:
    app[HTTP_SESSION_KEY] = ClientSession(timeout=ClientTimeout(total=None, connect=30, sock_read=None))


async def _close_client_session(app: web.Application) -> None:
    session = app[HTTP_SESSION_KEY]
    if not session.closed:
        await session.close()


def _upstream_headers(app: web.Application, request: web.Request) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {app[GATEWAY_CONFIG_KEY].upstream_token}"}
    for key in ("Accept", "Content-Type", "Idempotency-Key", "X-Hermes-Session-Id", "X-Hermes-Session-Key"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    return headers


async def admin_page(_request: web.Request) -> web.StreamResponse:
    return web.FileResponse(WEB_ROOT / "index.html")


async def web_asset(request: web.Request) -> web.StreamResponse:
    asset = request.match_info["name"]
    if asset not in {"styles.css", "app.js"}:
        raise web.HTTPNotFound()
    response = web.FileResponse(WEB_ROOT / asset)
    response.content_type = "text/css" if asset.endswith(".css") else "text/javascript"
    return response


async def setup_status(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY]
    payload = {"initialized": config.initialized}
    if not config.initialized and not config.setup_available:
        payload["setup_available"] = False
    return web.json_response(payload)


async def login(request: web.Request) -> web.Response:
    if not request.app[GATEWAY_CONFIG_KEY].initialized:
        return web.json_response(
            {"error": {"code": "setup_required", "message": "请先完成 Nexus 初始化"}},
            status=503,
        )
    limiter = request.app[LOGIN_RATE_LIMITER_KEY]
    client_ip = _client_ip(request)
    allowed, retry_after = limiter.allow(client_ip)
    if not allowed:
        return web.json_response({
            "error": {
                "code": "login_throttled",
                "message": "登录尝试过于频繁，请稍后再试",
                "retry_after": round(retry_after, 2),
            }
        }, status=429)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求格式无效"}}, status=400)
    username = str(body.get("username", ""))
    password = str(body.get("password", ""))
    auth_state = request.app[AUTH_STATE_KEY]
    if not _secure_text_equal(username, auth_state.username) or not _verify_password(password, auth_state):
        return web.json_response({"error": {"code": "invalid_credentials", "message": "账号或密码错误"}}, status=401)
    limiter.record_success(client_ip)
    if auth_state.legacy_password:
        salt, password_hash = _hash_password(password)
        _write_credentials(request.app[CREDENTIALS_PATH_KEY], username, salt, password_hash, auth_state.revision)
        auth_state.password_salt = salt
        auth_state.password_hash = password_hash
        auth_state.legacy_password = ""
    return web.json_response({
        "access_token": _encode_token(username, request.app[GATEWAY_CONFIG_KEY].session_secret, auth_state.revision),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "username": username,
    })


def _write_credentials(path: Path, username: str, password_salt: str, password_hash: str, revision: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "username": username,
        "password_scheme": "scrypt",
        "password_salt": password_salt,
        "password_hash": password_hash,
        "revision": revision,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _write_config(path: Path, hermes_api_url: str, hermes_api_token: str, session_secret: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps({
        "hermes_api_url": hermes_api_url.rstrip("/"),
        "hermes_api_token": hermes_api_token,
        "session_secret": session_secret,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def setup(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY]
    if config.initialized:
        return web.json_response(
            {"error": {"code": "already_initialized", "message": "Nexus 已完成初始化"}},
            status=409,
        )
    if not config.setup_available:
        return web.json_response(
            {"error": {"code": "configuration_error", "message": "配置状态异常，请检查持久化数据"}},
            status=503,
        )
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求格式无效"}}, status=400)
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    hermes_api_url = str(body.get("hermes_api_url", "")).strip().rstrip("/")
    hermes_api_token = str(body.get("hermes_api_token", "")).strip()
    supplied_bootstrap_token = str(body.get("bootstrap_token", ""))
    token_path = request.app[BOOTSTRAP_TOKEN_PATH_KEY]
    try:
        expected_bootstrap_token = token_path.read_text(encoding="utf-8").strip()
    except OSError:
        expected_bootstrap_token = ""
    if not expected_bootstrap_token or not _secure_text_equal(supplied_bootstrap_token, expected_bootstrap_token):
        return web.json_response(
            {"error": {"code": "invalid_bootstrap_token", "message": "初始化令牌无效"}},
            status=403,
        )
    if len(username) < 3 or len(username) > 48:
        return web.json_response({"error": {"code": "invalid_username", "message": "账号长度应为 3–48 个字符"}}, status=400)
    if len(password) < 8:
        return web.json_response({"error": {"code": "weak_password", "message": "密码至少需要 8 个字符"}}, status=400)
    if not hermes_api_url.startswith(("http://", "https://")):
        return web.json_response({"error": {"code": "invalid_hermes_url", "message": "Hermes 地址必须使用 http:// 或 https://"}}, status=400)
    if not hermes_api_token:
        return web.json_response({"error": {"code": "missing_hermes_token", "message": "请填写 Hermes API Server Key"}}, status=400)
    session_secret = secrets.token_urlsafe(48)
    _write_config(request.app[CONFIG_PATH_KEY], hermes_api_url, hermes_api_token, session_secret)
    password_salt, password_hash = _hash_password(password)
    _write_credentials(request.app[CREDENTIALS_PATH_KEY], username, password_salt, password_hash, 1)
    config.session_secret = session_secret
    config.upstream_url = hermes_api_url
    config.upstream_token = hermes_api_token
    config.initialized = True
    auth_state = request.app[AUTH_STATE_KEY]
    auth_state.username = username
    auth_state.password_salt = password_salt
    auth_state.password_hash = password_hash
    auth_state.legacy_password = ""
    auth_state.revision = 1
    token_path.unlink(missing_ok=True)
    return web.json_response({"initialized": True, "username": username}, status=201)


async def change_account(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求格式无效"}}, status=400)
    current_password = str(body.get("current_password", ""))
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    auth_state = request.app[AUTH_STATE_KEY]
    if not _verify_password(current_password, auth_state):
        return web.json_response({"error": {"code": "invalid_current_password", "message": "当前密码错误"}}, status=401)
    if len(username) < 3 or len(username) > 48:
        return web.json_response({"error": {"code": "invalid_username", "message": "账号长度应为 3–48 个字符"}}, status=400)
    if len(password) < 8:
        return web.json_response({"error": {"code": "weak_password", "message": "新密码至少需要 8 个字符"}}, status=400)
    revision = auth_state.revision + 1
    password_salt, password_hash = _hash_password(password)
    _write_credentials(request.app[CREDENTIALS_PATH_KEY], username, password_salt, password_hash, revision)
    auth_state.username = username
    auth_state.password_salt = password_salt
    auth_state.password_hash = password_hash
    auth_state.legacy_password = ""
    auth_state.revision = revision
    return web.json_response({
        "username": username,
        "access_token": _encode_token(username, request.app[GATEWAY_CONFIG_KEY].session_secret, revision),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
    })


async def admin_files(request: web.Request) -> web.Response:
    return web.json_response({"object": "list", "data": request.app[MEDIA_STORE_KEY].list_files("files")})


async def admin_audio(request: web.Request) -> web.Response:
    return web.json_response({"object": "list", "data": request.app[MEDIA_STORE_KEY].list_files("audio")})


async def admin_overview(request: web.Request) -> web.Response:
    files = request.app[MEDIA_STORE_KEY].list_files("files")
    audio = request.app[MEDIA_STORE_KEY].list_files("audio")
    session_count = 0
    upstream_status = "degraded"
    try:
        session = request.app[HTTP_SESSION_KEY]
        async with session.get(
            f"{request.app[GATEWAY_CONFIG_KEY].upstream_url}/api/sessions",
            headers={"Authorization": f"Bearer {request.app[GATEWAY_CONFIG_KEY].upstream_token}"},
        ) as upstream:
            payload = await upstream.json(content_type=None)
            if upstream.status == 200:
                session_count = len(payload.get("data", []))
                upstream_status = "ok"
    except Exception:
        pass
    return web.json_response({
        "status": upstream_status,
        "session_count": session_count,
        "file_count": len(files),
        "file_bytes": sum(item["size"] for item in files),
        "audio_count": len(audio),
        "audio_bytes": sum(item["size"] for item in audio),
        "gateway_version": __version__,
    })


async def health(request: web.Request) -> web.Response:
    if not request.app[GATEWAY_CONFIG_KEY].initialized:
        return web.json_response({
            "status": "setup_required",
            "gateway": "nexus-mobile-gateway",
            "version": __version__,
            "initialized": False,
        })
    session = request.app[HTTP_SESSION_KEY]
    try:
        async with session.get(f"{request.app[GATEWAY_CONFIG_KEY].upstream_url}/health") as upstream:
            data = await upstream.json(content_type=None)
            status = "ok" if upstream.status == 200 and data.get("status") == "ok" else "degraded"
            return web.json_response({"status": status, "gateway": "nexus-mobile-gateway", "version": __version__, "upstream": data}, status=200 if status == "ok" else 503)
    except Exception as exc:
        return web.json_response({"status": "degraded", "gateway": "nexus-mobile-gateway", "version": __version__, "upstream": {"error": type(exc).__name__}}, status=503)


async def upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError):
        return web.json_response({"error": {"code": "multipart_required", "message": "请使用 multipart/form-data 上传文件"}}, status=400)
    async for field in reader:
        if field.name == "file" and field.filename:
            item = await request.app[MEDIA_STORE_KEY].save(field)
            return web.json_response({"object": "nexus.file", "file": item.public_dict()}, status=201)
    return web.json_response({"error": {"code": "file_required", "message": "缺少 file 字段"}}, status=400)


async def transcribe_upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError):
        return web.json_response({"error": {"code": "multipart_required", "message": "请上传录音文件"}}, status=400)
    async for field in reader:
        if field.name != "file" or not field.filename:
            continue
        item = await request.app[MEDIA_STORE_KEY].save(field, category="audio")
        if not item.mime_type.startswith("audio/"):
            request.app[MEDIA_STORE_KEY].delete(item.id)
            return web.json_response({"error": {"code": "audio_required", "message": "上传内容不是音频"}}, status=400)
        transcriber = request.app[TRANSCRIBE_AUDIO_KEY]
        result = await asyncio.to_thread(transcriber, item.server_path)
        if not result.get("success"):
            return web.json_response({
                "error": {"code": "transcription_failed", "message": "语音转写失败"},
                "file": item.public_dict(),
            }, status=503)
        return web.json_response({
            "object": "nexus.audio.transcription",
            "transcript": str(result.get("transcript", "")).strip(),
            "provider": result.get("provider"),
            "file": item.public_dict(),
        })
    return web.json_response({"error": {"code": "file_required", "message": "缺少录音文件"}}, status=400)


async def file_metadata(request: web.Request) -> web.Response:
    found = request.app[MEDIA_STORE_KEY].get(request.match_info["file_id"])
    if found is None:
        raise web.HTTPNotFound(text=json.dumps({"error": {"code": "file_not_found", "message": "文件不存在"}}, ensure_ascii=False), content_type="application/json")
    item, _ = found
    return web.json_response({"object": "nexus.file", "file": item.public_dict()})


async def download(request: web.Request) -> web.StreamResponse:
    found = request.app[MEDIA_STORE_KEY].get(request.match_info["file_id"])
    if found is None:
        raise web.HTTPNotFound(text=json.dumps({"error": {"code": "file_not_found", "message": "文件不存在"}}, ensure_ascii=False), content_type="application/json")
    item, path = found
    response = web.FileResponse(path)
    response.content_type = item.mime_type
    response.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(item.name)}"
    return response


async def delete_file(request: web.Request) -> web.Response:
    deleted = request.app[MEDIA_STORE_KEY].delete(request.match_info["file_id"])
    if not deleted:
        return web.json_response({"error": {"code": "file_not_found", "message": "文件不存在"}}, status=404)
    return web.json_response({"object": "nexus.file.deleted", "id": request.match_info["file_id"], "deleted": True})


def _attachment_part(item: StoredFile, path: Path, kind: str | None = None) -> dict[str, Any]:
    if item.mime_type in IMAGE_MIME_TYPES and kind != "file":
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{item.mime_type};base64,{encoded}"}}
    note = f"附件：{item.name}\n类型：{item.mime_type}\n大小：{item.size} 字节"
    if item.mime_type in TEXT_MIME_TYPES and item.size <= 512 * 1024:
        try:
            text = path.read_text(encoding="utf-8")
            note += f"\n\n附件内容：\n{text}"
        except UnicodeDecodeError:
            pass
    return {"type": "text", "text": note}


async def _prepare_chat_body(request: web.Request, body: dict[str, Any]) -> dict[str, Any]:
    attachment_ids = body.pop("attachment_ids", [])
    attachment_kinds = body.pop("attachment_kinds", {})
    if not isinstance(attachment_ids, list):
        raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "invalid_attachment_ids", "message": "attachment_ids 必须是数组"}}, ensure_ascii=False), content_type="application/json")
    if not attachment_ids:
        return body

    current_message = body.get("message", "")
    parts: list[dict[str, Any]] = []
    if isinstance(current_message, str) and current_message.strip():
        parts.append({"type": "text", "text": current_message})
    elif isinstance(current_message, list):
        parts.extend(current_message)
    normalized_ids = [str(file_id) for file_id in attachment_ids]
    request[REQUEST_ATTACHMENT_IDS_KEY] = normalized_ids
    marker = current_message.strip() if isinstance(current_message, str) else ""
    for file_id in normalized_ids:
        found = request.app[MEDIA_STORE_KEY].get(file_id)
        if found is None:
            raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "attachment_not_found", "message": f"附件不存在：{file_id}"}}, ensure_ascii=False), content_type="application/json")
        parts.append(_attachment_part(*found, kind=str(attachment_kinds.get(file_id, ""))))
    session_match = re.fullmatch(r"/api/sessions/([^/]+)/chat(?:/stream)?", request.path)
    if session_match:
        request.app[MEDIA_STORE_KEY].record_session_media(
            session_match.group(1),
            normalized_ids,
            marker,
            {str(key): str(value) for key, value in attachment_kinds.items()},
        )
    body["message"] = parts
    return body


def _is_internal_runtime_message(message: dict[str, Any]) -> bool:
    if str(message.get("role", "")).lower() != "user":
        return False
    content = message.get("content", "")
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
        )
    normalized = str(content).lstrip().lower()
    return normalized.startswith((
        "[context compaction",
        "[important: you are running as a scheduled cron job",
        "[system:",
        "[prior context",
    ))


def _public_session_history(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data")
    if isinstance(data, list):
        payload["data"] = [
            message
            for message in data
            if not isinstance(message, dict)
            or (
                str(message.get("role", "")).lower() != "tool"
                and not _is_internal_runtime_message(message)
            )
        ]
    payload.pop("pagination", None)
    return payload


def _paginate_session_history(payload: dict[str, Any], query: Any) -> dict[str, Any]:
    data = payload.get("data")
    if not isinstance(data, list) or isinstance(payload.get("pagination"), dict):
        return payload
    try:
        requested_limit = int(query.get("limit", 0))
        limit = min(100, requested_limit) if requested_limit > 0 else 0
    except (TypeError, ValueError):
        limit = 0
    try:
        offset = max(0, int(query.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    if not limit:
        return payload
    total = len(data)
    end = max(0, total - offset)
    start = max(0, end - limit)
    payload["data"] = data[start:end]
    payload["pagination"] = {
        "total": total,
        "offset": offset,
        "limit": limit,
        "has_more": start > 0,
    }
    return payload


async def proxy(request: web.Request) -> web.StreamResponse:
    is_message_history = request.method == "GET" and re.fullmatch(r"/api/sessions/[^/]+/messages", request.path)
    if request.method == "GET" and re.fullmatch(r"/api/sessions/[^/]+/run", request.path):
        session_id = request.path.split("/")[3]
        return web.json_response(request.app[RUN_TRACKER_KEY].status(session_id))
    if request.method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/run/stop", request.path):
        session_id = request.path.split("/")[3]
        tracker: RunTracker = request.app[RUN_TRACKER_KEY]
        task = tracker.tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            return web.json_response({"session_id": session_id, "stopped": True})
        return web.json_response({"session_id": session_id, "stopped": False})
    if request.method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/chat/stream", request.path):
        return await _tracked_session_stream(request)
    relative = request.path if is_message_history else request.path_qs
    body_bytes: bytes | None = None
    if request.method in {"POST", "PUT", "PATCH"}:
        if request.path.endswith("/chat") or request.path.endswith("/chat/stream"):
            try:
                body = await request.json()
            except (json.JSONDecodeError, ValueError):
                return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
            body = await _prepare_chat_body(request, body)
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = await request.read()

    session = request.app[HTTP_SESSION_KEY]
    upstream = await session.request(
        request.method,
        f"{request.app[GATEWAY_CONFIG_KEY].upstream_url}{relative}",
        headers=_upstream_headers(request.app, request),
        data=body_bytes,
        allow_redirects=False,
    )
    excluded = {"content-length", "transfer-encoding", "connection", "content-encoding"}
    headers = {key: value for key, value in upstream.headers.items() if key.lower() not in excluded}
    if is_message_history:
        try:
            payload = await upstream.json(content_type=None)
        finally:
            upstream.release()
        session_id = request.path.split("/")[3]
        payload = _public_session_history(payload)
        payload = request.app[MEDIA_STORE_KEY].enrich_session_messages(session_id, payload)
        payload = _paginate_session_history(payload, request.query)
        headers.pop("Content-Type", None)
        return web.json_response(payload, status=upstream.status, headers=headers)

    response = web.StreamResponse(status=upstream.status, reason=upstream.reason, headers=headers)
    await response.prepare(request)
    try:
        async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
            await response.write(chunk)
    except ConnectionResetError:
        pass
    finally:
        upstream.release()
    try:
        await response.write_eof()
    except ConnectionResetError:
        pass
    return response


async def _tracked_session_stream(request: web.Request) -> web.StreamResponse:
    session_id = request.path.split("/")[3]
    tracker: RunTracker = request.app[RUN_TRACKER_KEY]
    current = tracker.tasks.get(session_id)
    if current is not None and not current.done():
        return web.json_response({"error": {"code": "run_active", "message": "该会话仍在处理中"}}, status=409)
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
    body = await _prepare_chat_body(request, body)
    body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
    queue = tracker.subscribe(session_id)
    run_id = f"run_{uuid.uuid4().hex}"
    tracker.start(session_id, run_id)

    async def consume_upstream() -> None:
        upstream = None
        try:
            upstream = await request.app[HTTP_SESSION_KEY].post(
                f"{request.app[GATEWAY_CONFIG_KEY].upstream_url}{request.path_qs}",
                headers=_upstream_headers(request.app, request),
                data=body_bytes,
            )
            if not upstream.ok:
                raise RuntimeError(f"Hermes HTTP {upstream.status}")
            async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                tracker.consume_sse(session_id, chunk)
                tracker.publish(session_id, chunk)
            tracker.finish(session_id, "completed")
        except asyncio.CancelledError:
            tracker.finish(session_id, "stopped")
            raise
        except Exception as exc:
            attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
            if attachment_ids:
                request.app[MEDIA_STORE_KEY].discard_last_session_media(session_id, attachment_ids)
            tracker.finish(session_id, "failed", "上游服务暂时不可用")
            payload = json.dumps({"message": "上游服务暂时不可用"}, ensure_ascii=False)
            tracker.publish(session_id, f"event: error\ndata: {payload}\n\n".encode("utf-8"))
        finally:
            if upstream is not None:
                upstream.release()
            tracker.publish(session_id, b"event: done\ndata: {}\n\n")

    task = asyncio.create_task(consume_upstream())
    tracker.tasks[session_id] = task
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    try:
        while True:
            chunk = await queue.get()
            await response.write(chunk)
            if task.done() and queue.empty():
                break
    except (asyncio.CancelledError, ConnectionResetError):
        # Detach the phone only; the gateway keeps reading Hermes to completion.
        pass
    finally:
        tracker.unsubscribe(session_id, queue)
    return response


def _valid_file_id(file_id: str) -> bool:
    return len(file_id) == 32 and all(char in "0123456789abcdef" for char in file_id)


def create_app(
    *,
    username: str | None,
    password: str | None,
    session_secret: str | None,
    upstream_url: str | None,
    upstream_token: str | None,
    storage_dir: Path,
    credentials_path: Path | None = None,
    config_path: Path | None = None,
    bootstrap_token_path: Path | None = None,
    max_upload_bytes: int = 50 * 1024 * 1024,
    max_total_storage_bytes: int = 10 * 1024 * 1024 * 1024,
    min_free_disk_bytes: int = 512 * 1024 * 1024,
    login_rate_limit: int = 5,
    login_rate_window_seconds: float = 60.0,
    transcribe_audio: Callable[[str], dict[str, Any]] | None = None,
) -> web.Application:
    resolved_config_path = Path(config_path or (Path(storage_dir).resolve().parent / "config.json")).resolve()
    saved_config: dict[str, Any] = {}
    config_exists = resolved_config_path.exists()
    config_valid = not config_exists
    if config_exists:
        try:
            loaded = json.loads(resolved_config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                saved_config = loaded
                config_valid = True
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            config_valid = False
    username = str(saved_config.get("username") or username or "")
    password = str(saved_config.get("password") or password or "")
    session_secret = str(saved_config.get("session_secret") or session_secret or "")
    upstream_url = str(saved_config.get("hermes_api_url") or upstream_url or "")
    upstream_token = str(saved_config.get("hermes_api_token") or upstream_token or "")
    app = web.Application(
        middlewares=[security_headers, device_auth],
        client_max_size=max_upload_bytes + 1024 * 1024,
    )
    resolved_credentials_path = Path(credentials_path or (Path(storage_dir).resolve().parent / "account.json")).resolve()
    credentials_exists = resolved_credentials_path.exists()
    credentials_valid = not credentials_exists
    revision = 1
    password_salt = ""
    password_hash = ""
    legacy_password = ""
    if credentials_exists:
        try:
            saved_credentials = json.loads(resolved_credentials_path.read_text(encoding="utf-8"))
            if not isinstance(saved_credentials, dict):
                raise ValueError("credentials must be an object")
            username = str(saved_credentials.get("username", username))
            legacy_password = str(saved_credentials.get("password", ""))
            password_salt = str(saved_credentials.get("password_salt", ""))
            password_hash = str(saved_credentials.get("password_hash", ""))
            revision = max(1, int(saved_credentials.get("revision", 1)))
            credentials_valid = bool(username and (legacy_password or (password_salt and password_hash)))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            credentials_valid = False
    elif username and password:
        password_salt, password_hash = _hash_password(password)
        _write_credentials(resolved_credentials_path, username, password_salt, password_hash, revision)
        credentials_valid = True
    initialized = bool(
        config_valid
        and credentials_valid
        and username
        and (legacy_password or (password_salt and password_hash))
        and len(session_secret) >= 16
        and upstream_token
        and upstream_url.startswith(("http://", "https://"))
    )
    if initialized and not resolved_config_path.exists():
        _write_config(resolved_config_path, upstream_url, upstream_token, session_secret)
    app[USERNAME_KEY] = username
    app[PASSWORD_KEY] = password
    app[CREDENTIALS_PATH_KEY] = resolved_credentials_path
    app[CONFIG_PATH_KEY] = resolved_config_path
    resolved_bootstrap_token_path = Path(
        bootstrap_token_path or (resolved_config_path.parent / "bootstrap.token")
    ).resolve()
    setup_available = not initialized and config_valid and credentials_valid and not config_exists and not credentials_exists
    if setup_available and not resolved_bootstrap_token_path.exists():
        resolved_bootstrap_token_path.parent.mkdir(parents=True, exist_ok=True)
        resolved_bootstrap_token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
    app[BOOTSTRAP_TOKEN_PATH_KEY] = resolved_bootstrap_token_path
    app[GATEWAY_CONFIG_KEY] = GatewayConfig(
        initialized=initialized,
        setup_available=setup_available,
        session_secret=session_secret,
        upstream_url=upstream_url.rstrip("/"),
        upstream_token=upstream_token,
    )
    app[AUTH_STATE_KEY] = AuthState(username, password_salt, password_hash, legacy_password, revision)
    app[LOGIN_RATE_LIMITER_KEY] = LoginRateLimiter(login_rate_limit, login_rate_window_seconds)
    app[MEDIA_STORE_KEY] = MediaStore(
        storage_dir,
        max_upload_bytes,
        max_total_storage_bytes,
        min_free_disk_bytes,
    )
    app[RUN_TRACKER_KEY] = RunTracker(Path(storage_dir).resolve().parent / "run_status.json")
    if transcribe_audio is None:
        try:
            from tools.transcription_tools import transcribe_audio as hermes_transcribe_audio
            transcribe_audio = hermes_transcribe_audio
        except ModuleNotFoundError:
            def transcribe_audio(_path: str) -> dict[str, Any]:
                return {"success": False, "transcript": "", "error": "Hermes 语音转写组件不可用"}
    app[TRANSCRIBE_AUDIO_KEY] = transcribe_audio
    app.on_startup.append(_create_client_session)
    app.on_cleanup.append(_close_client_session)

    app.router.add_get("/", admin_page)
    app.router.add_get("/assets/{name}", web_asset)
    app.router.add_get("/health", health)
    app.router.add_get("/api/setup/status", setup_status)
    app.router.add_post("/api/setup", setup)
    app.router.add_post("/api/auth/login", login)
    app.router.add_put("/api/admin/account", change_account)
    app.router.add_get("/api/admin/overview", admin_overview)
    app.router.add_get("/api/admin/files", admin_files)
    app.router.add_get("/api/admin/audio", admin_audio)
    app.router.add_post("/api/audio/transcriptions", transcribe_upload)
    app.router.add_post("/api/uploads", upload)
    app.router.add_get("/api/files/{file_id}/metadata", file_metadata)
    app.router.add_get("/api/files/{file_id}", download)
    app.router.add_delete("/api/files/{file_id}", delete_file)
    app.router.add_route("*", "/api/{tail:.*}", proxy)
    app.router.add_route("*", "/v1/{tail:.*}", proxy)
    return app
