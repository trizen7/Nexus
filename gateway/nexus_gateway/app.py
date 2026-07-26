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
import stat
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
EXTERNAL_RUN_OBSERVER_KEY = web.AppKey("external_run_observer", object)
TRANSCRIBE_AUDIO_KEY = web.AppKey("transcribe_audio", object)
REQUEST_ATTACHMENT_IDS_KEY = web.RequestKey("nexus_attachment_ids", list)
LOGIN_RATE_LIMITER_KEY = web.AppKey("login_rate_limiter", object)
SETUP_LOCK_KEY = web.AppKey("setup_lock", asyncio.Lock)
TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
ALLOWED_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
MOBILE_CLIENT_CONTEXT_MARKER = "[NEXUS MOBILE CLIENT CONTEXT]"
MOBILE_CLIENT_SYSTEM_MESSAGE = f"""{MOBILE_CLIENT_CONTEXT_MARKER}
当前用户正通过 Nexus Android 手机客户端操作。
交互约束：
- 不要要求或假设用户能够访问 Hermes 运行主机的本地文件系统、桌面路径、拖拽区域、剪贴板或电脑快捷键。
- 本轮消息中的图片和文件均来自手机端附件；应直接根据消息内提供的图片、文件名、类型和可读内容处理，不要要求用户改用电脑重新上传。
- 需要向用户交付图片或文件时，只能在回复中直接呈现内容，或提供手机可访问的 HTTP/HTTPS 下载地址。不得把 Hermes 主机本地路径（例如 /tmp/...、C:/... 或 sandbox:/...）当作已发送的文件。
- 如果当前 API 无法把生成的二进制文件交付给手机，必须明确说明限制，并提供可在手机上完成的替代方案或可复制内容。
- 操作步骤应按 Android 触屏方式表述。"""
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


def _hermes_unavailable_message() -> str:
    base = 'Nexus 已启动，但无法访问 Hermes API。请检查 Hermes 地址、端口、API Server Key 和服务状态；'
    if os.getenv("NEXUS_DEPLOYMENT_MODE", "").strip().casefold() == "fnos-host":
        return base + 'fnOS 同机部署建议使用 http://127.0.0.1:8642，Hermes 在其他设备上时请使用其局域网地址。'
    return base + '普通 Docker bridge 同机部署可使用 host.docker.internal，其他部署请使用 Hermes 的实际可达地址。'


HERMES_UNAVAILABLE_MESSAGE = _hermes_unavailable_message()
HERMES_AUTH_FAILED_MESSAGE = (
    "Hermes API Server Key 无效或无权访问，请在 Nexus 配置中检查 Hermes API 地址和 API Server Key"
)


def _hermes_auth_failed_error() -> dict[str, dict[str, str]]:
    return {
        "error": {
            "code": "hermes_auth_failed",
            "message": HERMES_AUTH_FAILED_MESSAGE,
        }
    }


class HermesUpstreamAuthError(RuntimeError):
    """Raised when Hermes rejects the Gateway's own upstream credentials."""


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


@dataclass(frozen=True)
class _OwnedFileIdentity:
    device: int
    inode: int
    size: int
    digest: bytes


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
        status = dict(self.statuses.get(session_id) or {
            "session_id": session_id,
            "run_id": None,
            "status": "idle",
            "active": False,
            "phase": "idle",
            "snapshot": "",
            "updated_at": time.time(),
        })
        status.setdefault("source", "nexus_gateway")
        status.setdefault("stoppable", bool(status.get("active")))
        return status

    def start(self, session_id: str, run_id: str) -> None:
        self.statuses[session_id] = {
            "session_id": session_id,
            "run_id": run_id,
            "status": "running",
            "active": True,
            "phase": "thinking",
            "snapshot": "",
            "tool_name": None,
            "source": "nexus_gateway",
            "stoppable": True,
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
            source="nexus_gateway",
            stoppable=False,
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


class ExternalRunObserver:
    """Best-effort, read-only observation of runs started outside Nexus."""

    CHANNEL_EXCLUDED_SOURCES = {"", "api_server", "cron", "desktop", "cli", "tui"}

    def __init__(
        self,
        *,
        cache_seconds: float = 2.0,
        freshness_seconds: float = 120.0,
        completion_seconds: float = 12.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cache_seconds = max(0.0, cache_seconds)
        self.freshness_seconds = max(0.0, freshness_seconds)
        self.completion_seconds = max(0.0, completion_seconds)
        self.clock = clock
        self._lock = asyncio.Lock()
        self._last_probe_at = 0.0
        self._previous_sessions: dict[str, dict[str, Any]] = {}
        self._active_session_id: str | None = None
        self._active_status: dict[str, Any] | None = None
        self._completed: dict[str, dict[str, Any]] = {}

    async def status(self, app: web.Application, session_id: str) -> dict[str, Any] | None:
        await self._refresh(app)
        now = self.clock()
        self._prune_completed(now)
        if session_id == self._active_session_id and self._active_status is not None:
            return dict(self._active_status)
        completed = self._completed.get(session_id)
        return dict(completed) if completed is not None else None

    async def _refresh(self, app: web.Application) -> None:
        now = self.clock()
        if self._last_probe_at and now - self._last_probe_at < self.cache_seconds:
            return
        async with self._lock:
            now = self.clock()
            if self._last_probe_at and now - self._last_probe_at < self.cache_seconds:
                return
            self._last_probe_at = now
            await self._probe(app, now)

    async def _probe(self, app: web.Application, now: float) -> None:
        health_result, sessions_result = await asyncio.gather(
            self._fetch_json(app, "/health/detailed"),
            self._fetch_json(app, "/api/sessions"),
            return_exceptions=True,
        )
        health = health_result if isinstance(health_result, dict) else None
        sessions_payload = sessions_result if isinstance(sessions_result, dict) else None
        sessions = self._session_snapshot(sessions_payload) if sessions_payload is not None else None

        if health is None:
            # Unknown upstream state must never leave a synthetic run stuck active.
            self._drop_active()
            if sessions is not None:
                self._previous_sessions = sessions
            self._prune_completed(now)
            return

        gateway_busy = self._bool_value(health.get("gateway_busy"))
        active_agents = self._int_value(health.get("active_agents"))
        busy = gateway_busy or active_agents > 0
        if not busy:
            self._complete_active(now)
            if sessions is not None:
                self._previous_sessions = sessions
            self._prune_completed(now)
            return

        if sessions is None:
            # A global busy flag without session metadata cannot be mapped safely.
            self._drop_active()
            self._prune_completed(now)
            return

        candidate = self._candidate_session(
            sessions,
            now=now,
            gateway_busy=gateway_busy,
        )
        self._previous_sessions = sessions
        if candidate is None:
            self._drop_active()
        else:
            self._activate(candidate, now)
        self._prune_completed(now)

    async def _fetch_json(self, app: web.Application, path: str) -> dict[str, Any] | None:
        config = app[GATEWAY_CONFIG_KEY]
        session = app[HTTP_SESSION_KEY]
        headers = {"Authorization": f"Bearer {config.upstream_token}"}
        try:
            async with session.get(f"{config.upstream_url}{path}", headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
                return payload if isinstance(payload, dict) else None
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            return None

    def _candidate_session(
        self,
        sessions: dict[str, dict[str, Any]],
        *,
        now: float,
        gateway_busy: bool,
    ) -> str | None:
        eligible = [
            item for item in sessions.values()
            if (self._is_channel_session(item) if gateway_busy else self._is_agent_session(item))
        ]
        if not eligible:
            return None

        changed = [item for item in eligible if self._session_changed(item)]
        if len(changed) == 1:
            return str(changed[0]["id"])
        if len(changed) > 1:
            if self._active_session_id and any(item["id"] == self._active_session_id for item in changed):
                return self._active_session_id
            return self._unique_latest(changed)

        if self._active_session_id and any(item["id"] == self._active_session_id for item in eligible):
            return self._active_session_id

        recent = [
            item for item in eligible
            if item["last_active"] > 0 and item["last_active"] >= now - self.freshness_seconds
        ]
        recent_candidate = self._unique_latest(recent)
        if recent_candidate is not None:
            return recent_candidate

        # Hermes may persist a channel transcript only after the turn finishes. When
        # its messaging gateway explicitly reports busy, the uniquely latest channel
        # session is the safest available fallback even if last_active is unchanged.
        return self._unique_latest(eligible) if gateway_busy else None

    def _session_changed(self, current: dict[str, Any]) -> bool:
        previous = self._previous_sessions.get(str(current["id"]))
        if previous is None:
            return bool(self._previous_sessions)
        return (
            current["message_count"] > previous["message_count"]
            or current["last_active"] > previous["last_active"] + 0.001
        )

    @classmethod
    def _session_snapshot(cls, payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        rows = payload.get("data")
        if not isinstance(rows, list):
            return {}
        snapshot: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            session_id = str(row.get("id", "")).strip()
            if not session_id:
                continue
            snapshot[session_id] = {
                "id": session_id,
                "source": str(row.get("source", "")).strip().lower(),
                "message_count": cls._int_value(row.get("message_count")),
                "last_active": cls._timestamp_value(row.get("last_active")),
            }
        return snapshot

    @classmethod
    def _is_channel_session(cls, item: dict[str, Any]) -> bool:
        return str(item.get("source", "")).lower() not in cls.CHANNEL_EXCLUDED_SOURCES

    @staticmethod
    def _is_agent_session(item: dict[str, Any]) -> bool:
        return str(item.get("source", "")).lower() not in {"", "cron"}

    @staticmethod
    def _unique_latest(items: list[dict[str, Any]]) -> str | None:
        if not items:
            return None
        ordered = sorted(items, key=lambda item: item["last_active"], reverse=True)
        if len(ordered) > 1 and ordered[0]["last_active"] <= ordered[1]["last_active"] + 0.001:
            return None
        return str(ordered[0]["id"])

    def _activate(self, session_id: str, now: float) -> None:
        if session_id == self._active_session_id and self._active_status is not None:
            return
        self._active_session_id = session_id
        self._completed.pop(session_id, None)
        self._active_status = {
            "session_id": session_id,
            "run_id": f"hermes_gateway_{uuid.uuid4().hex}",
            "status": "running",
            "active": True,
            "phase": "thinking",
            "snapshot": "",
            "tool_name": None,
            "message": None,
            "source": "hermes_gateway",
            "stoppable": False,
            "updated_at": now,
        }

    def _complete_active(self, now: float) -> None:
        if self._active_session_id is None or self._active_status is None:
            return
        completed = dict(self._active_status)
        completed.update(
            status="completed",
            active=False,
            phase="completed",
            stoppable=False,
            updated_at=now,
        )
        self._completed[self._active_session_id] = completed
        self._drop_active()

    def _drop_active(self) -> None:
        self._active_session_id = None
        self._active_status = None

    def _prune_completed(self, now: float) -> None:
        expired = [
            session_id for session_id, status in self._completed.items()
            if now - float(status.get("updated_at", 0.0)) > self.completion_seconds
        ]
        for session_id in expired:
            self._completed.pop(session_id, None)

    @staticmethod
    def _bool_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _int_value(value: Any) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _timestamp_value(value: Any) -> float:
        try:
            timestamp = float(value)
        except (TypeError, ValueError):
            return 0.0
        if timestamp > 10_000_000_000:
            timestamp /= 1000.0
        return max(0.0, timestamp)


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
        self._capacity_lock = asyncio.Lock()
        self._reserved_upload_bytes = 0

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

    async def _reserve_upload_bytes(self, incoming_bytes: int) -> None:
        async with self._capacity_lock:
            self._check_capacity(
                self._stored_bytes(),
                self._reserved_upload_bytes + incoming_bytes,
            )
            self._reserved_upload_bytes += incoming_bytes

    async def _release_upload_bytes(self, reserved_bytes: int) -> None:
        async with self._capacity_lock:
            self._reserved_upload_bytes = max(0, self._reserved_upload_bytes - reserved_bytes)

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
        reserved_bytes = 0
        committed = False
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
                    await self._reserve_upload_bytes(len(chunk))
                    reserved_bytes += len(chunk)
                    digest.update(chunk)
                    handle.write(chunk)
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
            async with self._capacity_lock:
                temp_path.replace(final_path)
                self._metadata_path(file_id, target_dir).write_text(json.dumps(asdict(item), ensure_ascii=False, indent=2), encoding="utf-8")
                self._reserved_upload_bytes = max(0, self._reserved_upload_bytes - reserved_bytes)
                reserved_bytes = 0
                committed = True
            return item
        finally:
            if reserved_bytes:
                await asyncio.shield(self._release_upload_bytes(reserved_bytes))
            temp_path.unlink(missing_ok=True)
            if not committed:
                final_path.unlink(missing_ok=True)

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


async def removed_tls_admin(_request: web.Request) -> web.StreamResponse:
    raise web.HTTPNotFound()


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
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
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
    _secure_atomic_write(path, json.dumps({
        "username": username,
        "password_scheme": "scrypt",
        "password_salt": password_salt,
        "password_hash": password_hash,
        "revision": revision,
    }, ensure_ascii=False, indent=2))


def _write_config(path: Path, hermes_api_url: str, hermes_api_token: str, session_secret: str) -> None:
    _secure_atomic_write(path, json.dumps({
        "hermes_api_url": hermes_api_url.rstrip("/"),
        "hermes_api_token": hermes_api_token,
        "session_secret": session_secret,
    }, ensure_ascii=False, indent=2))


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
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    hermes_api_url = str(body.get("hermes_api_url", "")).strip().rstrip("/")
    hermes_api_token = str(body.get("hermes_api_token", "")).strip()
    supplied_bootstrap_token = str(body.get("bootstrap_token", ""))

    if len(username) < 3 or len(username) > 48:
        return web.json_response({"error": {"code": "invalid_username", "message": "账号长度应为 3–48 个字符"}}, status=400)
    if len(password) < 8:
        return web.json_response({"error": {"code": "weak_password", "message": "密码至少需要 8 个字符"}}, status=400)
    if not hermes_api_url.startswith(("http://", "https://")):
        return web.json_response({"error": {"code": "invalid_hermes_url", "message": "Hermes 地址必须使用 http:// 或 https://"}}, status=400)
    if not hermes_api_token:
        return web.json_response({"error": {"code": "missing_hermes_token", "message": "请填写 Hermes API Server Key"}}, status=400)
    async with request.app[SETUP_LOCK_KEY]:
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
        token_path = request.app[BOOTSTRAP_TOKEN_PATH_KEY]
        expected_bootstrap_token = _read_secure_bootstrap_token(token_path)
        if not expected_bootstrap_token or not _secure_text_equal(supplied_bootstrap_token, expected_bootstrap_token):
            return web.json_response(
                {"error": {"code": "invalid_bootstrap_token", "message": "初始化令牌无效"}},
                status=403,
            )
        session_secret = secrets.token_urlsafe(48)
        config_path = request.app[CONFIG_PATH_KEY]
        credentials_path = request.app[CREDENTIALS_PATH_KEY]
        config_identity = None
        credentials_identity = None
        try:
            config_identity = _secure_exclusive_write(config_path, json.dumps({
                "hermes_api_url": hermes_api_url.rstrip("/"),
                "hermes_api_token": hermes_api_token,
                "session_secret": session_secret,
            }, ensure_ascii=False, indent=2))
            password_salt, password_hash = _hash_password(password)
            credentials_identity = _secure_exclusive_write(credentials_path, json.dumps({
                "username": username,
                "password_scheme": "scrypt",
                "password_salt": password_salt,
                "password_hash": password_hash,
                "revision": 1,
            }, ensure_ascii=False, indent=2))
            token_path.unlink()
        except Exception:
            _unlink_owned_file(config_path, config_identity)
            _unlink_owned_file(credentials_path, credentials_identity)
            raise

        config.session_secret = session_secret
        config.upstream_url = hermes_api_url
        config.upstream_token = hermes_api_token
        config.initialized = True
        config.setup_available = False
        auth_state = request.app[AUTH_STATE_KEY]
        auth_state.username = username
        auth_state.password_salt = password_salt
        auth_state.password_hash = password_hash
        auth_state.legacy_password = ""
        auth_state.revision = 1
    return web.json_response({"initialized": True, "username": username}, status=201)


async def change_account(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
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
            if upstream.status == 200 and isinstance(data, dict) and data.get("status") == "ok":
                return web.json_response({
                    "status": "ok",
                    "gateway": "nexus-mobile-gateway",
                    "version": __version__,
                    "upstream": data,
                })
            upstream_summary = {
                "status": data.get("status", "unavailable") if isinstance(data, dict) else "unavailable",
                "http_status": upstream.status,
            }
            if isinstance(data, dict) and data.get("version"):
                upstream_summary["version"] = data["version"]
            return web.json_response({
                "status": "degraded",
                "gateway": "nexus-mobile-gateway",
                "version": __version__,
                "upstream": upstream_summary,
                "error": {"code": "hermes_unavailable", "message": HERMES_UNAVAILABLE_MESSAGE},
            }, status=503)
    except Exception as exc:
        return web.json_response({
            "status": "degraded",
            "gateway": "nexus-mobile-gateway",
            "version": __version__,
            "upstream": {"status": "unavailable", "error": type(exc).__name__},
            "error": {"code": "hermes_unavailable", "message": HERMES_UNAVAILABLE_MESSAGE},
        }, status=503)


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


def _is_mobile_client_context(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    platform = str(value.get("platform", "")).strip().lower()
    form_factor = str(value.get("form_factor", "")).strip().lower()
    return platform == "android" and form_factor in {"", "phone", "tablet", "mobile", "handset"}


def _merge_system_message(existing: Any, required_context: str) -> str:
    existing_text = existing.strip() if isinstance(existing, str) else ""
    return f"{required_context}\n\n{existing_text}" if existing_text else required_context


def _apply_client_context(body: dict[str, Any]) -> bool:
    mobile_client = _is_mobile_client_context(body.pop("client_context", None))
    if mobile_client:
        body["system_message"] = _merge_system_message(
            body.get("system_message"),
            MOBILE_CLIENT_SYSTEM_MESSAGE,
        )
    return mobile_client


def _attachment_part(
    item: StoredFile,
    path: Path,
    kind: str | None = None,
    mobile_client: bool = False,
) -> dict[str, Any]:
    if item.mime_type in IMAGE_MIME_TYPES and kind != "file":
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{item.mime_type};base64,{encoded}"}}
    note = f"附件：{item.name}\n类型：{item.mime_type}\n大小：{item.size} 字节"
    if mobile_client:
        note += "\n来源：Nexus Android 手机端附件"
    if item.mime_type in TEXT_MIME_TYPES and item.size <= 512 * 1024:
        try:
            text = path.read_text(encoding="utf-8")
            note += f"\n\n附件内容：\n{text}"
        except UnicodeDecodeError:
            pass
    return {"type": "text", "text": note}


async def _prepare_chat_body(
    request: web.Request,
    body: dict[str, Any],
    mobile_client: bool = False,
) -> dict[str, Any]:
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
        parts.append(
            _attachment_part(
                *found,
                kind=str(attachment_kinds.get(file_id, "")),
                mobile_client=mobile_client,
            )
        )
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
    role = str(message.get("role", "")).lower()
    content = message.get("content", "")
    if isinstance(content, list):
        content = "\n".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
        )
    normalized = str(content).lstrip().lower()
    if normalized.startswith(MOBILE_CLIENT_CONTEXT_MARKER.lower()):
        return True
    if role != "user":
        return False
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
        local_status = request.app[RUN_TRACKER_KEY].status(session_id)
        if local_status.get("active") or local_status.get("status") in {"queued", "running", "stopping"}:
            return web.json_response(local_status)
        external_status = await request.app[EXTERNAL_RUN_OBSERVER_KEY].status(request.app, session_id)
        return web.json_response(external_status or local_status)
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
            mobile_client = _apply_client_context(body)
            body = await _prepare_chat_body(request, body, mobile_client=mobile_client)
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
    if upstream.status in {401, 403}:
        upstream.release()
        return web.json_response(_hermes_auth_failed_error(), status=502)
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


def _session_sse_event(event_name: str, payload: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


class _OpenAISessionStreamAdapter:
    """Convert Hermes' OpenAI-compatible SSE into the mobile session SSE contract."""

    def __init__(self) -> None:
        self._buffer = b""
        self.outcome: str | None = None

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer += chunk
        blocks = re.split(br"\r?\n\r?\n", self._buffer)
        self._buffer = blocks.pop() if blocks else b""
        events: list[bytes] = []
        for block in blocks:
            events.extend(self._convert_block(block))
        return events

    def finish(self) -> list[bytes]:
        events: list[bytes] = []
        if self._buffer.strip():
            events.extend(self._convert_block(self._buffer))
        self._buffer = b""
        if self.outcome is None:
            self.outcome = "failed"
            events.append(_session_sse_event("error", {"message": "Hermes 模型请求失败：流意外结束"}))
        return events

    def _convert_block(self, block: bytes) -> list[bytes]:
        event_name = ""
        data_lines: list[str] = []
        for raw_line in block.decode("utf-8", errors="replace").splitlines():
            if raw_line.startswith("event:"):
                event_name = raw_line.partition(":")[2].strip()
            elif raw_line.startswith("data:"):
                data_lines.append(raw_line.partition(":")[2].lstrip())
        data_text = "\n".join(data_lines).strip()
        if not data_text:
            return []
        if data_text == "[DONE]":
            return self._complete_if_needed()
        try:
            payload = json.loads(data_text)
        except json.JSONDecodeError:
            return []
        if not isinstance(payload, dict):
            return []

        if event_name == "hermes.tool.progress":
            status = str(payload.get("status", "")).lower()
            tool_name = payload.get("tool") or payload.get("tool_name") or payload.get("name")
            event = "tool.completed" if status in {"completed", "complete", "success", "succeeded"} else "tool.started"
            return [_session_sse_event(event, {"tool_name": tool_name})]

        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            if payload.get("error"):
                return self._fail("Hermes 模型请求失败")
            return []
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        events: list[bytes] = []
        if isinstance(content, str) and content:
            events.append(_session_sse_event("assistant.delta", {"delta": content}))
        elif isinstance(content, list):
            text = "".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
            )
            if text:
                events.append(_session_sse_event("assistant.delta", {"delta": text}))

        finish_reason = choice.get("finish_reason")
        if finish_reason:
            if str(finish_reason).lower() == "stop":
                events.extend(self._complete_if_needed())
            else:
                events.extend(self._fail("Hermes 模型请求失败"))
        return events

    def _complete_if_needed(self) -> list[bytes]:
        if self.outcome is not None:
            return []
        self.outcome = "completed"
        return [_session_sse_event("run.completed", {"completed": True})]

    def _fail(self, message: str) -> list[bytes]:
        if self.outcome is not None:
            return []
        self.outcome = "failed"
        return [_session_sse_event("error", {"message": message})]


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
    mobile_client = _apply_client_context(body)
    body = await _prepare_chat_body(request, body, mobile_client=mobile_client)
    legacy_model = str(body.pop("model", "") or "").strip()
    persona_model = str(body.pop("persona_model", "") or "").strip()
    inference_model = str(body.pop("inference_model", "") or "").strip() or legacy_model
    raw_reasoning_effort = body.pop("reasoning_effort", None)
    if raw_reasoning_effort is None or raw_reasoning_effort == "":
        reasoning_effort = None
    elif not isinstance(raw_reasoning_effort, str) or raw_reasoning_effort.strip().lower() not in ALLOWED_REASONING_EFFORTS:
        return web.json_response(
            {"error": {"code": "invalid_reasoning_effort", "message": "推理深度无效"}},
            status=400,
        )
    else:
        reasoning_effort = raw_reasoning_effort.strip().lower()
    use_model_route = bool(inference_model)
    if use_model_route:
        messages: list[dict[str, Any]] = []
        system_message = body.pop("system_message", None)
        if isinstance(system_message, str) and system_message.strip():
            messages.append({"role": "system", "content": system_message.strip()})
        messages.append({"role": "user", "content": body.get("message", "")})
        upstream_body = {
            "model": inference_model,
            "stream": True,
            "messages": messages,
        }
        if reasoning_effort is not None:
            upstream_body["reasoning_effort"] = reasoning_effort
        upstream_path = "/v1/chat/completions"
    else:
        if persona_model:
            body["model"] = persona_model
        if reasoning_effort is not None:
            body["reasoning_effort"] = reasoning_effort
        upstream_body = body
        upstream_path = request.path_qs
    body_bytes = json.dumps(upstream_body, ensure_ascii=False).encode("utf-8")
    queue = tracker.subscribe(session_id)
    run_id = f"run_{uuid.uuid4().hex}"
    tracker.start(session_id, run_id)

    async def consume_upstream() -> None:
        upstream = None
        try:
            upstream_headers = _upstream_headers(request.app, request)
            if use_model_route:
                upstream_headers.update({
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                    "X-Hermes-Session-Id": session_id,
                })
            upstream = await request.app[HTTP_SESSION_KEY].post(
                f"{request.app[GATEWAY_CONFIG_KEY].upstream_url}{upstream_path}",
                headers=upstream_headers,
                data=body_bytes,
            )
            if upstream.status in {401, 403}:
                raise HermesUpstreamAuthError()
            if not upstream.ok:
                raise RuntimeError(f"Hermes HTTP {upstream.status}")
            if use_model_route:
                adapter = _OpenAISessionStreamAdapter()
                started = _session_sse_event("run.started", {"run_id": run_id})
                tracker.consume_sse(session_id, started)
                tracker.publish(session_id, started)
                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    for event in adapter.feed(chunk):
                        tracker.consume_sse(session_id, event)
                        tracker.publish(session_id, event)
                for event in adapter.finish():
                    tracker.consume_sse(session_id, event)
                    tracker.publish(session_id, event)
                tracker.finish(session_id, adapter.outcome or "failed")
            else:
                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    tracker.consume_sse(session_id, chunk)
                    tracker.publish(session_id, chunk)
                tracker.finish(session_id, "completed")
        except asyncio.CancelledError:
            tracker.finish(session_id, "stopped")
            raise
        except HermesUpstreamAuthError:
            attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
            if attachment_ids:
                request.app[MEDIA_STORE_KEY].discard_last_session_media(session_id, attachment_ids)
            tracker.finish(session_id, "failed", HERMES_AUTH_FAILED_MESSAGE)
            payload = json.dumps({
                "code": "hermes_auth_failed",
                "message": HERMES_AUTH_FAILED_MESSAGE,
            }, ensure_ascii=False)
            tracker.publish(session_id, f"event: error\ndata: {payload}\n\n".encode("utf-8"))
        except Exception:
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


def _secure_atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
            if stat.S_IMODE(os.stat(temporary, follow_symlinks=False).st_mode) != 0o600:
                raise OSError("temporary file permissions are not owner-only")
        os.replace(temporary, path)
        if os.name != "nt" and stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode) != 0o600:
            raise OSError("persisted file permissions are not owner-only")
    finally:
        if descriptor != -1:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_secure_existing_file(path: Path) -> str | None:
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
            return None
        if os.name != "nt":
            os.chmod(path, 0o600, follow_symlinks=False)
        descriptor = os.open(path, _bootstrap_open_flags(os.O_RDONLY))
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                return None
            if (opened_stat.st_dev, opened_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
                return None
            if os.name != "nt" and stat.S_IMODE(opened_stat.st_mode) != 0o600:
                return None
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                return handle.read()
        finally:
            if descriptor != -1:
                os.close(descriptor)
    except (OSError, UnicodeError, NotImplementedError):
        return None


def _secure_exclusive_write(path: Path, content: str) -> _OwnedFileIdentity:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, _bootstrap_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL), 0o600)
    written_identity: _OwnedFileIdentity | None = None
    encoded_content = content.encode("utf-8")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            written_stat = os.fstat(handle.fileno())
        written_identity = _OwnedFileIdentity(
            device=written_stat.st_dev,
            inode=written_stat.st_ino,
            size=written_stat.st_size,
            digest=hashlib.sha256(encoded_content).digest(),
        )
        if os.name != "nt" and stat.S_IMODE(written_stat.st_mode) != 0o600:
            raise OSError("persisted file permissions are not owner-only")
        return written_identity
    except Exception:
        _unlink_owned_file(path, written_identity)
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _unlink_owned_file(path: Path, identity: _OwnedFileIdentity | None) -> None:
    if identity is None:
        return
    descriptor = -1
    try:
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or path.is_symlink():
            return
        if (current.st_dev, current.st_ino, current.st_size) != (
            identity.device,
            identity.inode,
            identity.size,
        ):
            return
        descriptor = os.open(path, _bootstrap_open_flags(os.O_RDONLY))
        opened_stat = os.fstat(descriptor)
        if not stat.S_ISREG(opened_stat.st_mode):
            return
        if (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_size) != (
            identity.device,
            identity.inode,
            identity.size,
        ):
            return
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
        if not hmac.compare_digest(digest.digest(), identity.digest):
            return
        os.close(descriptor)
        descriptor = -1
        final_stat = path.lstat()
        if (final_stat.st_dev, final_stat.st_ino, final_stat.st_size) != (
            identity.device,
            identity.inode,
            identity.size,
        ) or not stat.S_ISREG(final_stat.st_mode):
            return
        path.unlink()
    except (OSError, NotImplementedError):
        pass
    finally:
        if descriptor != -1:
            os.close(descriptor)


def _bootstrap_open_flags(access: int) -> int:
    flags = access
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    return flags


def _read_secure_bootstrap_token(path: Path) -> str:
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink():
            return ""
        if os.name != "nt" and stat.S_IMODE(file_stat.st_mode) != 0o600:
            return ""
        descriptor = os.open(path, _bootstrap_open_flags(os.O_RDONLY))
        try:
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                return ""
            if os.name != "nt" and stat.S_IMODE(opened_stat.st_mode) != 0o600:
                return ""
            if (opened_stat.st_dev, opened_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino):
                return ""
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                return handle.read().strip()
        finally:
            if descriptor != -1:
                os.close(descriptor)
    except (OSError, UnicodeError):
        return ""


def _secure_bootstrap_token_file(path: Path) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = _bootstrap_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        try:
            existing_stat = path.lstat()
            if not stat.S_ISREG(existing_stat.st_mode) or path.is_symlink():
                return False
            if os.name != "nt":
                os.chmod(path, 0o600, follow_symlinks=False)
        except (OSError, NotImplementedError):
            return False
        return bool(_read_secure_bootstrap_token(path))
    except OSError:
        return False
    if descriptor is not None:
        try:
            os.write(descriptor, secrets.token_urlsafe(32).encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    if os.name != "nt":
        try:
            if stat.S_IMODE(path.lstat().st_mode) != 0o600:
                path.unlink(missing_ok=True)
                return False
        except OSError:
            path.unlink(missing_ok=True)
            return False
    return bool(_read_secure_bootstrap_token(path))


def _canonical_parent_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        parent = absolute.parent.resolve(strict=False)
    except OSError:
        parent = absolute.parent
    return parent / absolute.name


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        # Permission and I/O failures must not be mistaken for an unused path.
        return True


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
    resolved_config_path = _canonical_parent_path(
        Path(config_path or (Path(storage_dir).resolve().parent / "config.json"))
    )
    saved_config: dict[str, Any] = {}
    config_exists = _path_entry_exists(resolved_config_path)
    config_valid = not config_exists
    if config_exists:
        try:
            config_content = _read_secure_existing_file(resolved_config_path)
            if config_content is None:
                raise OSError("configuration file is not secure")
            loaded = json.loads(config_content)
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
    resolved_credentials_path = _canonical_parent_path(
        Path(credentials_path or (Path(storage_dir).resolve().parent / "account.json"))
    )
    credentials_exists = _path_entry_exists(resolved_credentials_path)
    credentials_valid = not credentials_exists
    revision = 1
    password_salt = ""
    password_hash = ""
    legacy_password = ""
    if credentials_exists:
        try:
            credentials_content = _read_secure_existing_file(resolved_credentials_path)
            if credentials_content is None:
                raise OSError("credentials file is not secure")
            saved_credentials = json.loads(credentials_content)
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
    if initialized and not config_exists:
        _write_config(resolved_config_path, upstream_url, upstream_token, session_secret)
    app[USERNAME_KEY] = username
    app[PASSWORD_KEY] = password
    app[CREDENTIALS_PATH_KEY] = resolved_credentials_path
    app[CONFIG_PATH_KEY] = resolved_config_path
    resolved_bootstrap_token_path = Path(os.path.abspath(Path(
        bootstrap_token_path or (resolved_config_path.parent / "bootstrap.token")
    )))
    setup_available = not initialized and config_valid and credentials_valid and not config_exists and not credentials_exists
    if setup_available:
        setup_available = _secure_bootstrap_token_file(resolved_bootstrap_token_path)
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
    app[SETUP_LOCK_KEY] = asyncio.Lock()
    app[MEDIA_STORE_KEY] = MediaStore(
        storage_dir,
        max_upload_bytes,
        max_total_storage_bytes,
        min_free_disk_bytes,
    )
    app[RUN_TRACKER_KEY] = RunTracker(Path(storage_dir).resolve().parent / "run_status.json")
    app[EXTERNAL_RUN_OBSERVER_KEY] = ExternalRunObserver()
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
    app.router.add_route("*", "/api/admin/tls", removed_tls_admin)
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
