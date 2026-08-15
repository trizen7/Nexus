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
from urllib.parse import quote, urlsplit

from aiohttp import ClientSession, ClientTimeout, FormData, web

from . import __version__
from .cloud_asr import (
    CloudAsrLimits,
    CloudAsrProvider,
    cloud_asr_capabilities,
    cloud_asr_stream,
    configure_cloud_asr,
)
from .openai_asr import (
    OpenAiAsrConfig,
    normalize_openai_base_url,
    normalize_openai_model,
    transcribe_openai_compatible,
)

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
HERMES_PROFILE_IDENTITY_CACHE_KEY = web.AppKey("hermes_profile_identity_cache", dict)
MEDIA_STORE_KEY = web.AppKey("media_store", object)
HTTP_SESSION_KEY = web.AppKey("http_session", ClientSession)
RUN_TRACKER_KEY = web.AppKey("run_tracker", object)
EXTERNAL_RUN_OBSERVER_KEY = web.AppKey("external_run_observer", object)
EXTERNAL_RUN_OBSERVERS_KEY = web.AppKey("external_run_observers", dict)
TRANSCRIBE_AUDIO_KEY = web.AppKey("transcribe_audio", object)
SYNTHESIZE_SPEECH_KEY = web.AppKey("synthesize_speech", object)
SPEECH_SEMAPHORE_KEY = web.AppKey("speech_semaphore", asyncio.Semaphore)
SPEECH_MAX_AUDIO_BYTES_KEY = web.AppKey("speech_max_audio_bytes", int)
EPHEMERAL_CLEANUP_TASKS_KEY = web.AppKey("ephemeral_cleanup_tasks", set)
MANAGED_PATH_GUARD_KEY = web.AppKey("managed_path_guard", object)
REQUEST_ATTACHMENT_IDS_KEY = web.RequestKey("nexus_attachment_ids", list)
HERMES_CONNECTION_HEADER = "X-Nexus-Hermes-Connection"
HERMES_PROFILE_HEADER = "X-Nexus-Hermes-Profile"  # Legacy Nexus clients.
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
HERMES_MODEL_NOT_CONFIGURED_MESSAGE = "Hermes 尚未配置可用的调用模型，请先在 Hermes 中完成模型配置"
HERMES_AUTH_PROBE_PATHS = ("/health/detailed", "/v1/models", "/api/sessions")
HERMES_AUTH_PROBE_FALLBACK_STATUSES = {404, 405, 501}
HERMES_PROBE_TIMEOUT = ClientTimeout(total=10, connect=5)
HERMES_PROFILE_IDENTITY_CACHE_SECONDS = 30.0
HERMES_PROFILE_IDENTITY_FAILURE_CACHE_SECONDS = 5.0
SPEECH_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",
    "xiaoyi": "zh-CN-XiaoyiNeural",
    "yunxi": "zh-CN-YunxiNeural",
    "yunjian": "zh-CN-YunjianNeural",
}
SPEECH_MAX_CHARS = 600
SPEECH_MAX_AUDIO_BYTES = 4 * 1024 * 1024
SPEECH_MAX_CONCURRENCY = 2
SPEECH_TIMEOUT = ClientTimeout(total=20, connect=6)
TRANSCRIPTION_TIMEOUT_SECONDS = 90.0
EXTERNAL_RUN_PROBE_TIMEOUT = ClientTimeout(total=2, connect=1, sock_read=1.5)
MODEL_CATALOG_TIMEOUT = ClientTimeout(total=20, connect=5, sock_read=18)
MODEL_CATALOG_ATTEMPTS = 2
MODEL_LOCK_TIMEOUT = ClientTimeout(total=10, connect=5, sock_read=8)
MAX_CHAT_ATTACHMENTS = 16
MAX_CHAT_ATTACHMENT_BYTES = 20 * 1024 * 1024
ATTACHMENT_SHARE_TTL_SECONDS = 10 * 60
SSE_EVENT_BUFFER_LIMIT = 1024 * 1024
EPHEMERAL_CLEANUP_RETRY_DELAYS = (0.0, 0.05, 0.2, 0.5, 1.0)
TRANSCRIPTION_PROMPT = (
    "普通话对话，请使用简体中文，并根据语义添加逗号、句号和问号。"
    "专有名词：Nexus、Hermes、Android、Gateway、API、模型、会话、定时任务。"
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


class HermesModelSelectionError(RuntimeError):
    """Raised when Hermes cannot safely apply a requested session model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SpeechAudioTooLargeError(RuntimeError):
    """Raised when synthesized audio exceeds the Gateway response limit."""


@dataclass
class AuthState:
    username: str
    password_salt: str
    password_hash: str
    legacy_password: str = ""
    revision: int = 1


@dataclass(frozen=True)
class HermesProfileConfig:
    id: str
    name: str
    upstream_url: str
    upstream_token: str
    is_default: bool = False


@dataclass
class GatewayConfig:
    initialized: bool
    setup_available: bool
    session_secret: str
    upstream_url: str
    upstream_token: str
    profiles: dict[str, HermesProfileConfig]
    openai_asr: OpenAiAsrConfig

    def profile(self, profile_id: str | None) -> HermesProfileConfig | None:
        normalized = (profile_id or "default").strip() or "default"
        if normalized == "default":
            return HermesProfileConfig(
                id="default",
                name="Hermes 默认（default）",
                upstream_url=self.upstream_url,
                upstream_token=self.upstream_token,
                is_default=True,
            )
        return self.profiles.get(normalized)

    def all_profiles(self) -> list[HermesProfileConfig]:
        return [self.profile("default"), *self.profiles.values()]  # type: ignore[list-item]


@dataclass(frozen=True)
class _OwnedFileIdentity:
    device: int
    inode: int
    size: int
    digest: bytes


class RunTracker:
    """Own mobile chat runs outside Hermes so upstream stays unmodified."""

    SNAPSHOT_LIMIT = 256 * 1024
    SUBSCRIBER_QUEUE_LIMIT = 16
    SUBSCRIBERS_PER_SESSION_LIMIT = 1

    def __init__(
        self,
        state_file: Path | None = None,
        path_guard: "_ManagedPathGuard | None" = None,
    ) -> None:
        self.state_file = Path(os.path.abspath(os.fspath(state_file))) if state_file else None
        self.path_guard = (
            path_guard
            if path_guard is not None
            else (_ManagedPathGuard(self.state_file.parent) if self.state_file else None)
        )
        self.statuses: dict[str, dict[str, Any]] = self._load()
        self.tasks: dict[str, asyncio.Task] = {}
        self.reservations: set[str] = set()
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
        if self.state_file is None or not _path_entry_exists(self.state_file):
            return {}
        try:
            content = _read_secure_existing_file(self.state_file)
            if content is None:
                return {}
            data = json.loads(content)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        if self.state_file is None:
            return
        _secure_atomic_write(
            self.state_file,
            json.dumps(self.statuses, ensure_ascii=False, indent=2),
            self.path_guard,
        )

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

    def reserve(self, session_id: str) -> bool:
        current = self.tasks.get(session_id)
        if session_id in self.reservations or (current is not None and not current.done()):
            return False
        self.reservations.add(session_id)
        return True

    def attach(self, session_id: str, task: asyncio.Task) -> None:
        if session_id not in self.reservations:
            raise RuntimeError("run reservation is missing")
        self.tasks[session_id] = task
        self.reservations.discard(session_id)

    def release_reservation(self, session_id: str) -> None:
        self.reservations.discard(session_id)

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
        self.buffers.pop(session_id, None)
        self.last_persisted.pop(session_id, None)
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

    async def publish(self, session_id: str, event: bytes) -> None:
        for queue in tuple(self.subscribers.get(session_id, set())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                self.unsubscribe(session_id, queue)
                while not queue.empty():
                    queue.get_nowait()
                payload = json.dumps({
                    "code": "slow_client",
                    "message": "客户端接收响应过慢，流已断开",
                }, ensure_ascii=False)
                queue.put_nowait(f"event: error\ndata: {payload}\n\n".encode("utf-8"))
                queue.put_nowait(None)

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queues = self.subscribers.setdefault(session_id, set())
        if len(queues) >= self.SUBSCRIBERS_PER_SESSION_LIMIT:
            raise RuntimeError("session subscriber limit reached")
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.SUBSCRIBER_QUEUE_LIMIT)
        queues.add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        queues = self.subscribers.get(session_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self.subscribers.pop(session_id, None)

    async def cancel_all(self) -> None:
        current_task = asyncio.current_task()
        pending = list({
            task
            for task in tuple(self.tasks.values())
            if not task.done() and task is not current_task
        })
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for session_id, task in tuple(self.tasks.items()):
            if task.done():
                self.tasks.pop(session_id, None)
        self.reservations.clear()
        for session_id, status in tuple(self.statuses.items()):
            if status.get("active"):
                self.finish(
                    session_id,
                    "interrupted",
                    "Nexus Gateway stopped; Hermes remote run status is unknown.",
                )


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

    async def reset(self) -> None:
        async with self._lock:
            self._last_probe_at = 0.0
            self._previous_sessions.clear()
            self._active_session_id = None
            self._active_status = None
            self._completed.clear()

    async def status(
        self,
        app: web.Application,
        session_id: str,
        profile: HermesProfileConfig | None = None,
    ) -> dict[str, Any] | None:
        await self._refresh(app, profile)
        now = self.clock()
        self._prune_completed(now)
        if session_id == self._active_session_id and self._active_status is not None:
            return dict(self._active_status)
        completed = self._completed.get(session_id)
        return dict(completed) if completed is not None else None

    async def _refresh(
        self,
        app: web.Application,
        profile: HermesProfileConfig | None = None,
    ) -> None:
        now = self.clock()
        if self._last_probe_at and now - self._last_probe_at < self.cache_seconds:
            return
        async with self._lock:
            now = self.clock()
            if self._last_probe_at and now - self._last_probe_at < self.cache_seconds:
                return
            self._last_probe_at = now
            await self._probe(app, now, profile)

    async def _probe(
        self,
        app: web.Application,
        now: float,
        profile: HermesProfileConfig | None = None,
    ) -> None:
        health_result, sessions_result = await asyncio.gather(
            self._fetch_json(app, "/health/detailed", profile),
            self._fetch_json(app, "/api/sessions", profile),
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

    async def _fetch_json(
        self,
        app: web.Application,
        path: str,
        profile: HermesProfileConfig | None = None,
    ) -> dict[str, Any] | None:
        selected = profile or app[GATEWAY_CONFIG_KEY].profile("default")
        if selected is None:
            return None
        session = app[HTTP_SESSION_KEY]
        headers = {"Authorization": f"Bearer {selected.upstream_token}"}
        try:
            async with session.get(
                f"{selected.upstream_url}{path}",
                headers=headers,
                timeout=EXTERNAL_RUN_PROBE_TIMEOUT,
            ) as response:
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
        path_guard: "_ManagedPathGuard | None" = None,
    ) -> None:
        lexical_root = Path(os.path.abspath(os.fspath(root)))
        self.path_guard = path_guard or _ManagedPathGuard(lexical_root.parent)
        self.root = self.path_guard.ensure_directory(lexical_root, pin=True)
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
            if not _path_entry_exists(metadata):
                continue
            try:
                content = _read_secure_existing_file(metadata)
                if content is None:
                    continue
                return StoredFile(**json.loads(content))
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def get(self, file_id: str) -> tuple[StoredFile, Path] | None:
        item = self._read_metadata(file_id)
        if item is None:
            return None
        stored_path = Path(item.server_path)
        try:
            stored_stat = stored_path.lstat()
        except OSError:
            return None
        if (
            not stat.S_ISREG(stored_stat.st_mode)
            or stored_path.is_symlink()
            or stored_stat.st_nlink != 1
        ):
            return None
        path = stored_path.resolve()
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
                content = _read_secure_existing_file(metadata)
                if content is None:
                    continue
                item = StoredFile(**json.loads(content))
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
        try:
            target_dir = self.path_guard.ensure_directory(target_dir)
            self.path_guard.verify_directory(self.root)
        except OSError:
            raise self._storage_error(
                "unsafe_storage_path",
                "Media storage path failed its safety check",
            )
        file_id = uuid.uuid4().hex
        suffix = Path(clean_name).suffix[:16]
        final_path = target_dir / f"{file_id}{suffix}"
        temp_path = target_dir / f".{file_id}.upload"
        digest = hashlib.sha256()
        size = 0
        reserved_bytes = 0
        committed = False
        upload_directory_descriptor = -1
        try:
            upload_parent, upload_parent_identity = self.path_guard.verify_write_target(temp_path)
            upload_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_BINARY"):
                upload_flags |= os.O_BINARY
            use_directory_handle = os.name != "nt" and os.open in os.supports_dir_fd
            if use_directory_handle:
                directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
                upload_directory_descriptor = os.open(upload_parent, directory_flags)
                opened_parent = os.fstat(upload_directory_descriptor)
                if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != upload_parent_identity:
                    raise OSError("media directory changed before upload")
                upload_descriptor = os.open(
                    temp_path.name,
                    upload_flags,
                    0o600,
                    dir_fd=upload_directory_descriptor,
                )
            else:
                self.path_guard.verify_directory(upload_parent, upload_parent_identity)
                upload_descriptor = os.open(temp_path, upload_flags, 0o600)
            with os.fdopen(upload_descriptor, "wb") as handle:
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
                self.path_guard.verify_write_target(final_path)
                self.path_guard.verify_directory(upload_parent, upload_parent_identity)
                if use_directory_handle:
                    opened_parent = os.fstat(upload_directory_descriptor)
                    if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != upload_parent_identity:
                        raise OSError("media directory changed before upload commit")
                    os.replace(
                        temp_path.name,
                        final_path.name,
                        src_dir_fd=upload_directory_descriptor,
                        dst_dir_fd=upload_directory_descriptor,
                    )
                else:
                    os.replace(temp_path, final_path)
                _secure_atomic_write(
                    self._metadata_path(file_id, target_dir),
                    json.dumps(asdict(item), ensure_ascii=False, indent=2),
                    self.path_guard,
                )
                self._reserved_upload_bytes = max(0, self._reserved_upload_bytes - reserved_bytes)
                reserved_bytes = 0
                committed = True
            return item
        finally:
            if reserved_bytes:
                await asyncio.shield(self._release_upload_bytes(reserved_bytes))
            if upload_directory_descriptor != -1:
                try:
                    os.unlink(temp_path.name, dir_fd=upload_directory_descriptor)
                except FileNotFoundError:
                    pass
                finally:
                    os.close(upload_directory_descriptor)
            else:
                try:
                    self.path_guard.unlink_file(temp_path)
                except OSError:
                    pass
            if not committed:
                try:
                    self.path_guard.unlink_file(final_path)
                except OSError:
                    pass

    def delete(self, file_id: str) -> bool:
        found = self.get(file_id)
        if found is None:
            return False
        _, path = found
        self.path_guard.unlink_file(path)
        for metadata in self._metadata_candidates(file_id):
            self.path_guard.unlink_file(metadata)
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
            if _path_entry_exists(index_path):
                content = _read_secure_existing_file(index_path)
                if content is None:
                    raise OSError("session media index is not a regular single-link file")
                index = json.loads(content)
            else:
                index = {}
        except (ValueError, json.JSONDecodeError):
            index = {}
        index.setdefault(session_id, []).append({
            "marker": marker,
            "file_ids": [item.id for item in items],
            "attachment_kinds": attachment_kinds or {},
        })
        _secure_atomic_write(
            index_path,
            json.dumps(index, ensure_ascii=False, indent=2),
            self.path_guard,
        )

    def discard_last_session_media(self, session_id: str, file_ids: list[str]) -> None:
        index_path = self.root / "session_media.json"
        try:
            if not _path_entry_exists(index_path):
                return
            content = _read_secure_existing_file(index_path)
            if content is None:
                return
            index = json.loads(content)
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
        _secure_atomic_write(
            index_path,
            json.dumps(index, ensure_ascii=False, indent=2),
            self.path_guard,
        )

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
            content = _read_secure_existing_file(index_path)
            if content is None:
                return payload
            raw_entries = json.loads(content).get(session_id, [])
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


def _attachment_share_signature(file_id: str, expires_at: int, secret: str) -> str:
    payload = f"nexus-attachment:{file_id}:{expires_at}"
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _valid_attachment_share_request(request: web.Request) -> bool:
    if request.method != "GET":
        return False
    config = request.app[GATEWAY_CONFIG_KEY]
    if not config.initialized or len(config.session_secret) < 16:
        return False
    match = re.fullmatch(r"/api/files/([0-9a-f]{32})", request.path)
    if match is None:
        return False
    try:
        expires_at = int(request.query.get("expires", ""))
    except ValueError:
        return False
    now = int(time.time())
    if expires_at <= now or expires_at > now + ATTACHMENT_SHARE_TTL_SECONDS:
        return False
    supplied = request.query.get("signature", "")
    expected = _attachment_share_signature(
        match.group(1),
        expires_at,
        config.session_secret,
    )
    return hmac.compare_digest(supplied, expected)


def _attachment_share_url(request: web.Request, file_id: str) -> str:
    expires_at = int(time.time()) + ATTACHMENT_SHARE_TTL_SECONDS
    signature = _attachment_share_signature(
        file_id,
        expires_at,
        request.app[GATEWAY_CONFIG_KEY].session_secret,
    )
    forwarded_scheme = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
    scheme = forwarded_scheme if forwarded_scheme in {"http", "https"} else request.scheme
    return (
        f"{scheme}://{request.host}/api/files/{file_id}"
        f"?expires={expires_at}&signature={signature}"
    )


@web.middleware
async def device_auth(request: web.Request, handler):
    if request.path in PUBLIC_PATHS or _valid_attachment_share_request(request):
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
    tracker = app.get(RUN_TRACKER_KEY)
    if isinstance(tracker, RunTracker):
        await tracker.cancel_all()
    session = app[HTTP_SESSION_KEY]
    if not session.closed:
        await session.close()


def _upstream_headers(
    app: web.Application,
    request: web.Request,
    profile: HermesProfileConfig | None = None,
) -> dict[str, str]:
    selected = profile or _request_profile(request)
    headers = {"Authorization": f"Bearer {selected.upstream_token}"}
    for key in ("Accept", "Content-Type", "Idempotency-Key", "X-Hermes-Session-Id", "X-Hermes-Session-Key"):
        value = request.headers.get(key)
        if value:
            headers[key] = value
    return headers


def _normalize_hermes_api_url(value: str) -> str | None:
    candidate = value.strip().rstrip("/")
    if not candidate or any(char.isspace() for char in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        _ = parsed.port
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
    except ValueError:
        return None
    return candidate


def _safe_upstream_label(value: Any) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= 64:
        return None
    return value if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+ -]*", value) else None


PROFILE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
MAX_HERMES_PROFILES = 16


def _normalize_profile_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or candidate == "default" or not PROFILE_ID_PATTERN.fullmatch(candidate):
        return None
    return candidate


def _normalize_profile_name(value: Any, profile_id: str) -> str | None:
    if value is not None and not isinstance(value, str):
        return None
    candidate = (value or "").strip()
    if not candidate:
        candidate = profile_id
    if len(candidate) > 64 or any(ord(char) < 32 for char in candidate):
        return None
    return candidate


def _load_saved_profiles(value: Any) -> dict[str, HermesProfileConfig]:
    if value is None:
        return {}
    if not isinstance(value, list):
        return {}
    profiles: dict[str, HermesProfileConfig] = {}
    for item in value[:MAX_HERMES_PROFILES]:
        if not isinstance(item, dict):
            continue
        profile_id = _normalize_profile_id(item.get("id"))
        if profile_id is None or profile_id in profiles:
            continue
        profile_name = _normalize_profile_name(item.get("name"), profile_id)
        upstream_url = _normalize_hermes_api_url(str(item.get("hermes_api_url", "")))
        upstream_token = str(item.get("hermes_api_token", "")).strip()
        if profile_name is None or upstream_url is None or not upstream_token:
            continue
        profiles[profile_id] = HermesProfileConfig(
            id=profile_id,
            name=profile_name,
            upstream_url=upstream_url,
            upstream_token=upstream_token,
        )
    return profiles


def _profile_public_payload(profile: HermesProfileConfig, *, admin: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": profile.id,
        "name": profile.name,
        "is_default": profile.is_default,
    }
    if admin:
        payload.update({
            "hermes_api_url": profile.upstream_url,
            "key_configured": bool(profile.upstream_token),
        })
    return payload


def _safe_hermes_profile_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 128 or any(ord(char) < 32 for char in candidate):
        return None
    return candidate


def _profile_name_from_models(payload: Any) -> str | None:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None
    models = [item for item in payload["data"] if isinstance(item, dict)]
    primary = next((item for item in models if item.get("parent") is None), None)
    if primary is None and models:
        primary = models[0]
    return _safe_hermes_profile_name(primary.get("id")) if primary else None


async def _resolve_profile_identity(
    app: web.Application,
    profile: HermesProfileConfig,
    *,
    force: bool = False,
) -> dict[str, Any]:
    now = time.monotonic()
    cache: dict[str, dict[str, Any]] = app[HERMES_PROFILE_IDENTITY_CACHE_KEY]
    cached = cache.get(profile.id)
    if (
        not force
        and cached is not None
        and cached.get("upstream_url") == profile.upstream_url
        and now - float(cached.get("cached_at", 0.0))
        < (
            HERMES_PROFILE_IDENTITY_CACHE_SECONDS
            if cached.get("available")
            else HERMES_PROFILE_IDENTITY_FAILURE_CACHE_SECONDS
        )
    ):
        return dict(cached)

    identity: dict[str, Any] = {
        "profile_name": None,
        "available": False,
        "state": "unavailable",
        "upstream_url": profile.upstream_url,
        "cached_at": now,
    }
    try:
        session = app[HTTP_SESSION_KEY]
        async with session.get(
            f"{profile.upstream_url}/v1/models",
            headers={"Authorization": f"Bearer {profile.upstream_token}", "Accept": "application/json"},
            timeout=HERMES_PROBE_TIMEOUT,
        ) as response:
            if response.status in {401, 403}:
                identity["state"] = "auth_failed"
            elif response.status == 200:
                try:
                    payload = await response.json(content_type=None)
                except (json.JSONDecodeError, ValueError):
                    payload = None
                profile_name = _profile_name_from_models(payload)
                if profile_name is not None:
                    identity.update(profile_name=profile_name, available=True, state="ok")
    except Exception:
        pass
    cache[profile.id] = identity
    return dict(identity)


def _persona_public_payload(profile: HermesProfileConfig, identity: dict[str, Any]) -> dict[str, Any]:
    profile_name = _safe_hermes_profile_name(identity.get("profile_name"))
    return {
        "id": profile.id,
        "name": profile_name or profile.name,
        "profile_name": profile_name,
        "connection_id": profile.id,
        "connection_name": profile.name,
        "is_default": profile.is_default,
        "available": bool(identity.get("available")),
        "state": str(identity.get("state") or "unavailable"),
        "source": "hermes_profile_api",
    }


def _request_profile(request: web.Request) -> HermesProfileConfig:
    requested = (
        request.headers.get(HERMES_CONNECTION_HEADER)
        or request.headers.get(HERMES_PROFILE_HEADER)
        or "default"
    ).strip() or "default"
    profile = request.app[GATEWAY_CONFIG_KEY].profile(requested)
    if profile is None:
        raise web.HTTPBadRequest(
            text=json.dumps({
                "error": {
                    "code": "unknown_hermes_profile",
                    "message": "所选 Hermes 人格连接不存在，请刷新人格列表或在 Gateway 中重新配置",
                }
            }, ensure_ascii=False),
            content_type="application/json",
        )
    return profile


def _scoped_session_id(profile: HermesProfileConfig, session_id: str) -> str:
    return session_id if profile.is_default else f"profile:{profile.id}:{session_id}"


def _external_run_observer(app: web.Application, profile: HermesProfileConfig) -> ExternalRunObserver:
    if profile.is_default:
        return app[EXTERNAL_RUN_OBSERVER_KEY]
    observers: dict[str, ExternalRunObserver] = app[EXTERNAL_RUN_OBSERVERS_KEY]
    return observers.setdefault(profile.id, ExternalRunObserver())


def _upstream_summary(payload: Any, *, status: str, http_status: int | None = None) -> dict[str, Any]:
    summary: dict[str, Any] = {"status": status}
    if http_status is not None:
        summary["http_status"] = http_status
    if isinstance(payload, dict):
        for key in ("platform", "version"):
            value = _safe_upstream_label(payload.get(key))
            if value is not None:
                summary[key] = value
    return summary


async def _probe_hermes(
    app: web.Application,
    *,
    upstream_url: str | None = None,
    upstream_token: str | None = None,
) -> dict[str, Any]:
    config = app[GATEWAY_CONFIG_KEY]
    url = (upstream_url or config.upstream_url).rstrip("/")
    token = config.upstream_token if upstream_token is None else upstream_token
    session = app[HTTP_SESSION_KEY]
    try:
        async with session.get(f"{url}/health", timeout=HERMES_PROBE_TIMEOUT) as response:
            try:
                payload = await response.json(content_type=None)
            except (json.JSONDecodeError, ValueError):
                payload = None
            if response.status != 200 or not isinstance(payload, dict) or payload.get("status") != "ok":
                public_status = payload.get("status") if isinstance(payload, dict) else None
                safe_status = public_status if public_status in {"degraded", "error", "unavailable"} else "unavailable"
                return {
                    "state": "degraded",
                    "upstream": _upstream_summary(payload, status=safe_status, http_status=response.status),
                }
    except Exception:
        return {"state": "degraded", "upstream": {"status": "unavailable"}}

    public_summary = _upstream_summary(payload, status="ok")
    headers = {"Authorization": f"Bearer {token}"}
    for index, probe_path in enumerate(HERMES_AUTH_PROBE_PATHS):
        try:
            async with session.get(
                f"{url}{probe_path}", headers=headers, timeout=HERMES_PROBE_TIMEOUT,
            ) as response:
                status = response.status
        except Exception:
            unavailable = dict(public_summary)
            unavailable["status"] = "unavailable"
            return {"state": "degraded", "upstream": unavailable}
        if 200 <= status < 300:
            public_summary["authenticated"] = True
            return {"state": "ok", "upstream": public_summary}
        if status in {401, 403}:
            failed = dict(public_summary)
            failed.update({"status": "auth_failed", "http_status": status})
            return {"state": "auth_failed", "upstream": failed}
        if status in HERMES_AUTH_PROBE_FALLBACK_STATUSES and index < len(HERMES_AUTH_PROBE_PATHS) - 1:
            continue
        unavailable = dict(public_summary)
        unavailable.update({"status": "unavailable", "http_status": status})
        return {"state": "degraded", "upstream": unavailable}
    unavailable = dict(public_summary)
    unavailable["status"] = "unavailable"
    return {"state": "degraded", "upstream": unavailable}


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
        _write_credentials(
            request.app[CREDENTIALS_PATH_KEY],
            username,
            salt,
            password_hash,
            auth_state.revision,
            request.app[MANAGED_PATH_GUARD_KEY],
        )
        auth_state.password_salt = salt
        auth_state.password_hash = password_hash
        auth_state.legacy_password = ""
    return web.json_response({
        "access_token": _encode_token(username, request.app[GATEWAY_CONFIG_KEY].session_secret, auth_state.revision),
        "token_type": "bearer",
        "expires_in": TOKEN_TTL_SECONDS,
        "username": username,
    })


def _write_credentials(
    path: Path,
    username: str,
    password_salt: str,
    password_hash: str,
    revision: int,
    path_guard: "_ManagedPathGuard | None" = None,
) -> None:
    _secure_atomic_write(path, json.dumps({
        "username": username,
        "password_scheme": "scrypt",
        "password_salt": password_salt,
        "password_hash": password_hash,
        "revision": revision,
    }, ensure_ascii=False, indent=2), path_guard)


def _write_config(
    path: Path,
    hermes_api_url: str,
    hermes_api_token: str,
    session_secret: str,
    profiles: dict[str, HermesProfileConfig] | None = None,
    path_guard: "_ManagedPathGuard | None" = None,
    openai_asr: OpenAiAsrConfig | None = None,
) -> None:
    payload: dict[str, Any] = {
        "hermes_api_url": hermes_api_url.rstrip("/"),
        "hermes_api_token": hermes_api_token,
        "session_secret": session_secret,
    }
    configured_profiles = profiles or {}
    if configured_profiles:
        payload["hermes_profiles"] = [
            {
                "id": profile.id,
                "name": profile.name,
                "hermes_api_url": profile.upstream_url,
                "hermes_api_token": profile.upstream_token,
            }
            for profile in configured_profiles.values()
        ]
    if openai_asr is not None and (openai_asr.base_url or openai_asr.api_key):
        payload["openai_asr"] = {
            "enabled": openai_asr.enabled,
            "base_url": openai_asr.base_url,
            "api_key": openai_asr.api_key,
            "model": openai_asr.model,
        }
    _secure_atomic_write(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2),
        path_guard,
    )


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
    hermes_api_url = _normalize_hermes_api_url(str(body.get("hermes_api_url", "")))
    hermes_api_token = str(body.get("hermes_api_token", "")).strip()
    supplied_bootstrap_token = str(body.get("bootstrap_token", ""))

    if len(username) < 3 or len(username) > 48:
        return web.json_response({"error": {"code": "invalid_username", "message": "账号长度应为 3–48 个字符"}}, status=400)
    if len(password) < 8:
        return web.json_response({"error": {"code": "weak_password", "message": "密码至少需要 8 个字符"}}, status=400)
    if hermes_api_url is None:
        return web.json_response({"error": {"code": "invalid_hermes_url", "message": "Hermes 地址必须是有效的 http:// 或 https:// 地址"}}, status=400)
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
            }, ensure_ascii=False, indent=2), request.app[MANAGED_PATH_GUARD_KEY])
            password_salt, password_hash = _hash_password(password)
            credentials_identity = _secure_exclusive_write(credentials_path, json.dumps({
                "username": username,
                "password_scheme": "scrypt",
                "password_salt": password_salt,
                "password_hash": password_hash,
                "revision": 1,
            }, ensure_ascii=False, indent=2), request.app[MANAGED_PATH_GUARD_KEY])
            request.app[MANAGED_PATH_GUARD_KEY].unlink_file(
                token_path,
                missing_ok=False,
            )
        except Exception:
            _unlink_owned_file(
                config_path,
                config_identity,
                request.app[MANAGED_PATH_GUARD_KEY],
            )
            _unlink_owned_file(
                credentials_path,
                credentials_identity,
                request.app[MANAGED_PATH_GUARD_KEY],
            )
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
    _write_credentials(
        request.app[CREDENTIALS_PATH_KEY],
        username,
        password_salt,
        password_hash,
        revision,
        request.app[MANAGED_PATH_GUARD_KEY],
    )
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


async def admin_hermes_config(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY]
    return web.json_response({
        "hermes_api_url": config.upstream_url,
        "key_configured": bool(config.upstream_token),
        "profiles": [
            _profile_public_payload(profile, admin=True)
            for profile in config.profiles.values()
        ],
    })


async def admin_asr_config(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY].openai_asr
    return web.json_response({
        "enabled": config.enabled,
        "base_url": config.base_url,
        "model": config.model,
        "key_configured": bool(config.api_key),
    })


async def update_asr_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)
    if not _verify_password(str(body.get("current_password", "")), request.app[AUTH_STATE_KEY]):
        return web.json_response({"error": {"code": "invalid_current_password", "message": "当前密码错误"}}, status=401)

    enabled = body.get("enabled") is True
    async with request.app[SETUP_LOCK_KEY]:
        gateway_config = request.app[GATEWAY_CONFIG_KEY]
        current = gateway_config.openai_asr
        supplied_url = str(body.get("base_url", "")).strip()
        base_url = normalize_openai_base_url(supplied_url) if supplied_url else current.base_url
        supplied_model = str(body.get("model", "")).strip()
        model = normalize_openai_model(supplied_model) if supplied_model else current.model
        supplied_key = str(body.get("api_key", "")).strip()
        if supplied_url and base_url is None:
            return web.json_response({"error": {
                "code": "invalid_online_asr_url",
                "message": "在线 ASR 地址必须是有效的 http:// 或 https:// 地址",
            }}, status=400)
        if supplied_model and model is None:
            return web.json_response({"error": {
                "code": "invalid_online_asr_model",
                "message": "在线 ASR 模型名称无效",
            }}, status=400)
        if not supplied_key and base_url != current.base_url:
            return web.json_response({"error": {
                "code": "missing_online_asr_key",
                "message": "修改在线 ASR 地址时必须重新填写 API Key",
            }}, status=400)
        candidate = OpenAiAsrConfig(
            enabled=enabled,
            base_url=base_url or "",
            api_key=supplied_key or current.api_key,
            model=model or "whisper-1",
        )
        if enabled and not candidate.configured:
            return web.json_response({"error": {
                "code": "incomplete_online_asr_config",
                "message": "启用在线 ASR 前请填写地址、API Key 和模型",
            }}, status=400)
        _write_config(
            request.app[CONFIG_PATH_KEY],
            gateway_config.upstream_url,
            gateway_config.upstream_token,
            gateway_config.session_secret,
            gateway_config.profiles,
            request.app[MANAGED_PATH_GUARD_KEY],
            candidate,
        )
        gateway_config.openai_asr = candidate
    return web.json_response({
        "status": "ok",
        "enabled": candidate.enabled,
        "base_url": candidate.base_url,
        "model": candidate.model,
        "key_configured": bool(candidate.api_key),
    })


async def list_hermes_connections(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY]
    return web.json_response({
        "object": "list",
        "data": [
            _profile_public_payload(profile)
            for profile in config.all_profiles()
        ],
    })


async def list_hermes_profiles(request: web.Request) -> web.Response:
    config = request.app[GATEWAY_CONFIG_KEY]
    profiles = config.all_profiles()
    force = request.query.get("refresh", "").strip().casefold() in {"1", "true", "yes", "on"}
    identities = await asyncio.gather(*(
        _resolve_profile_identity(request.app, profile, force=force)
        for profile in profiles
    ))
    data = [
        _persona_public_payload(profile, identity)
        for profile, identity in zip(profiles, identities)
    ]
    unavailable = sum(1 for item in data if not item["available"])
    if len(data) == 1:
        current_name = data[0].get("profile_name")
        if current_name:
            notice = (
                f"当前原版 Hermes API 只公开活动 Profile「{current_name}」；"
                "其他人格需要各自可访问的原版 Hermes Profile API 地址和 Key。"
            )
        else:
            notice = (
                "暂时无法读取当前 Hermes Profile 名称；"
                "已保留默认连接，请检查 Hermes API 状态。"
            )
    elif unavailable:
        notice = (
            f"{unavailable} 个 Hermes Profile API 连接暂时不可用；"
            "已保留配置项，恢复后可再刷新。"
        )
    else:
        notice = None
    return web.json_response({
        "object": "list",
        "data": data,
        "notice": notice,
        "discovery": {
            "mode": "configured_profile_apis",
            "directory_complete": False,
            "reason": "hermes_api_server_has_no_machine_profile_directory",
        },
    })


async def update_hermes_config(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 无效"}}, status=400)

    current_password = str(body.get("current_password", ""))
    hermes_api_url = _normalize_hermes_api_url(str(body.get("hermes_api_url", "")))
    supplied_token = str(body.get("hermes_api_token", "")).strip()
    if not _verify_password(current_password, request.app[AUTH_STATE_KEY]):
        return web.json_response({"error": {"code": "invalid_current_password", "message": "当前密码错误"}}, status=401)
    if hermes_api_url is None:
        return web.json_response({"error": {"code": "invalid_hermes_url", "message": "Hermes 地址必须是有效的 http:// 或 https:// 地址"}}, status=400)

    async with request.app[SETUP_LOCK_KEY]:
        config = request.app[GATEWAY_CONFIG_KEY]
        if not supplied_token and hermes_api_url != config.upstream_url:
            return web.json_response({
                "error": {
                    "code": "missing_hermes_token",
                    "message": "修改 Hermes API 地址时必须重新填写 API Server Key",
                }
            }, status=400)
        hermes_api_token = supplied_token or config.upstream_token
        if not hermes_api_token:
            return web.json_response({"error": {"code": "missing_hermes_token", "message": "请填写 Hermes API Server Key"}}, status=400)

        candidate_profiles = dict(config.profiles)
        if "profiles" in body:
            raw_profiles = body.get("profiles")
            if not isinstance(raw_profiles, list):
                return web.json_response({
                    "error": {"code": "invalid_hermes_profiles", "message": "Hermes 人格配置必须是数组"}
                }, status=400)
            if len(raw_profiles) > MAX_HERMES_PROFILES:
                return web.json_response({
                    "error": {"code": "too_many_hermes_profiles", "message": f"最多配置 {MAX_HERMES_PROFILES} 个额外人格"}
                }, status=400)
            candidate_profiles = {}
            for item in raw_profiles:
                if not isinstance(item, dict):
                    return web.json_response({
                        "error": {"code": "invalid_hermes_profile", "message": "Hermes 人格配置项无效"}
                    }, status=400)
                profile_id = _normalize_profile_id(item.get("id"))
                if profile_id is None:
                    return web.json_response({
                        "error": {
                            "code": "invalid_hermes_profile_id",
                            "message": "人格 ID 只能包含字母、数字、点、下划线和短横线，且不能使用 default",
                        }
                    }, status=400)
                if profile_id in candidate_profiles:
                    return web.json_response({
                        "error": {"code": "duplicate_hermes_profile", "message": f"人格 ID 重复：{profile_id}"}
                    }, status=400)
                profile_name = _normalize_profile_name(item.get("name"), profile_id)
                profile_url = _normalize_hermes_api_url(str(item.get("hermes_api_url", "")))
                if profile_name is None:
                    return web.json_response({
                        "error": {"code": "invalid_hermes_profile_name", "message": f"人格名称无效：{profile_id}"}
                    }, status=400)
                if profile_url is None:
                    return web.json_response({
                        "error": {"code": "invalid_hermes_profile_url", "message": f"人格 {profile_name} 的 API 地址无效"}
                    }, status=400)
                existing = config.profiles.get(profile_id)
                profile_token = str(item.get("hermes_api_token", "")).strip()
                if (
                    not profile_token
                    and existing is not None
                    and existing.upstream_url == profile_url
                ):
                    profile_token = existing.upstream_token
                if not profile_token:
                    message = (
                        f"修改人格 {profile_name} 的 API 地址时必须重新填写 API Server Key"
                        if existing is not None and existing.upstream_url != profile_url
                        else f"请填写人格 {profile_name} 的 API Server Key"
                    )
                    return web.json_response({
                        "error": {"code": "missing_hermes_profile_token", "message": message}
                    }, status=400)
                candidate_profiles[profile_id] = HermesProfileConfig(
                    id=profile_id,
                    name=profile_name,
                    upstream_url=profile_url,
                    upstream_token=profile_token,
                )

        default_probe = await _probe_hermes(
            request.app,
            upstream_url=hermes_api_url,
            upstream_token=hermes_api_token,
        )
        if default_probe["state"] == "auth_failed":
            return web.json_response({
                "status": "degraded",
                "upstream": default_probe["upstream"],
                **_hermes_auth_failed_error(),
            }, status=422)
        if default_probe["state"] != "ok":
            return web.json_response({
                "status": "degraded",
                "upstream": default_probe["upstream"],
                "error": {"code": "hermes_unavailable", "message": HERMES_UNAVAILABLE_MESSAGE},
            }, status=502)

        for profile in candidate_profiles.values():
            profile_probe = await _probe_hermes(
                request.app,
                upstream_url=profile.upstream_url,
                upstream_token=profile.upstream_token,
            )
            if profile_probe["state"] == "auth_failed":
                return web.json_response({
                    "status": "degraded",
                    "error": {
                        "code": "hermes_profile_auth_failed",
                        "message": f"人格 {profile.name} 的 Hermes API Server Key 无效或无权访问",
                    },
                }, status=422)
            if profile_probe["state"] != "ok":
                return web.json_response({
                    "status": "degraded",
                    "error": {
                        "code": "hermes_profile_unavailable",
                        "message": f"无法连接人格 {profile.name} 的 Hermes API 地址",
                    },
                }, status=502)

        _write_config(
            request.app[CONFIG_PATH_KEY],
            hermes_api_url,
            hermes_api_token,
            config.session_secret,
            candidate_profiles,
            request.app[MANAGED_PATH_GUARD_KEY],
            config.openai_asr,
        )
        config.upstream_url = hermes_api_url
        config.upstream_token = hermes_api_token
        config.profiles = candidate_profiles
        request.app[HERMES_PROFILE_IDENTITY_CACHE_KEY].clear()
        await request.app[EXTERNAL_RUN_OBSERVER_KEY].reset()
        extra_observers: dict[str, ExternalRunObserver] = request.app[EXTERNAL_RUN_OBSERVERS_KEY]
        for observer in tuple(extra_observers.values()):
            await observer.reset()
        extra_observers.clear()

    return web.json_response({
        "status": "ok",
        "hermes_api_url": hermes_api_url,
        "key_configured": True,
        "profiles": [
            _profile_public_payload(profile, admin=True)
            for profile in candidate_profiles.values()
        ],
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
            timeout=HERMES_PROBE_TIMEOUT,
        ) as upstream:
            if upstream.status in {401, 403}:
                upstream_status = "auth_failed"
            elif upstream.status == 200:
                payload = await upstream.json(content_type=None)
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    session_count = len(data)
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

    probe = await _probe_hermes(request.app)
    payload = {
        "status": "ok" if probe["state"] == "ok" else "degraded",
        "gateway": "nexus-mobile-gateway",
        "version": __version__,
        "upstream": probe["upstream"],
    }
    if probe["state"] == "ok":
        return web.json_response(payload)
    if probe["state"] == "auth_failed":
        payload.update(_hermes_auth_failed_error())
    else:
        payload["error"] = {"code": "hermes_unavailable", "message": HERMES_UNAVAILABLE_MESSAGE}
    return web.json_response(payload, status=503)


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


async def _transcribe_via_hermes_api(
    app: web.Application,
    profile: HermesProfileConfig,
    item: StoredFile,
) -> dict[str, Any]:
    session = app[HTTP_SESSION_KEY]
    headers = {"Authorization": f"Bearer {profile.upstream_token}"}
    try:
        async with session.get(
            f"{profile.upstream_url}/v1/capabilities",
            headers=headers,
            timeout=HERMES_PROBE_TIMEOUT,
        ) as capabilities_response:
            if capabilities_response.status in {401, 403}:
                raise HermesUpstreamAuthError()
            if capabilities_response.status != 200:
                return {
                    "success": False,
                    "code": "transcription_unavailable",
                    "message": "当前 Hermes API 未提供语音转写能力",
                    "status": 501,
                }
            capabilities = await capabilities_response.json(content_type=None)
    except HermesUpstreamAuthError:
        raise
    except Exception:
        return {
            "success": False,
            "code": "transcription_unavailable",
            "message": "无法读取 Hermes API 语音能力",
            "status": 503,
        }

    if not isinstance(capabilities, dict):
        return {
            "success": False,
            "code": "transcription_unavailable",
            "message": "当前 Hermes API 未提供语音转写能力",
            "status": 501,
        }
    features = capabilities.get("features")
    endpoints = capabilities.get("endpoints")
    if not isinstance(features, dict) or not features.get("audio_api"):
        return {
            "success": False,
            "code": "transcription_unavailable",
            "message": "当前 Hermes API 未提供语音转写能力",
            "status": 501,
        }
    endpoint = endpoints.get("audio_transcriptions") if isinstance(endpoints, dict) else None
    endpoint_path = endpoint.get("path") if isinstance(endpoint, dict) else None
    if not isinstance(endpoint_path, str) or not endpoint_path.startswith("/") or ".." in endpoint_path:
        endpoint_path = "/v1/audio/transcriptions"

    form = FormData()
    try:
        with Path(item.server_path).open("rb") as handle:
            form.add_field("language", "zh")
            form.add_field("prompt", TRANSCRIPTION_PROMPT)
            form.add_field("temperature", "0")
            form.add_field(
                "file",
                handle,
                filename=item.name,
                content_type=item.mime_type or "application/octet-stream",
            )
            async with session.post(
                f"{profile.upstream_url}{endpoint_path}",
                headers=headers,
                data=form,
            ) as response:
                if response.status in {401, 403}:
                    raise HermesUpstreamAuthError()
                if response.status < 200 or response.status >= 300:
                    return {
                        "success": False,
                        "code": "transcription_failed",
                        "message": "Hermes API 语音转写失败",
                        "status": 503,
                    }
                payload = await response.json(content_type=None)
    except HermesUpstreamAuthError:
        raise
    except Exception:
        return {
            "success": False,
            "code": "transcription_failed",
            "message": "Hermes API 语音转写失败",
            "status": 503,
        }
    if not isinstance(payload, dict):
        payload = {}
    transcript = str(payload.get("transcript") or payload.get("text") or "").strip()
    if not transcript:
        return {
            "success": False,
            "code": "transcription_failed",
            "message": "Hermes API 没有返回转写文本",
            "status": 503,
        }
    return {
        "success": True,
        "transcript": transcript,
        "provider": str(payload.get("provider") or "hermes-api"),
    }


def _track_ephemeral_cleanup_task(app: web.Application, task: asyncio.Task) -> None:
    tasks = app.get(EPHEMERAL_CLEANUP_TASKS_KEY)
    if tasks is None:
        tasks = set()
        app[EPHEMERAL_CLEANUP_TASKS_KEY] = tasks
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def _finish_transcription_and_cleanup(
    app: web.Application,
    worker: asyncio.Task | None,
    file_id: str | None,
) -> None:
    if worker is not None:
        try:
            await asyncio.shield(worker)
        except BaseException:
            pass
    if file_id is None:
        return
    store: MediaStore = app[MEDIA_STORE_KEY]
    for delay in EPHEMERAL_CLEANUP_RETRY_DELAYS:
        if delay:
            await asyncio.sleep(delay)
        try:
            if store.delete(file_id) or store.get(file_id) is None:
                return
        except OSError:
            continue


def _defer_transcription_cleanup(
    app: web.Application,
    worker: asyncio.Task | None,
    file_id: str | None,
) -> None:
    task = asyncio.create_task(_finish_transcription_and_cleanup(app, worker, file_id))
    _track_ephemeral_cleanup_task(app, task)


def _delete_ephemeral_or_retry(app: web.Application, file_id: str) -> None:
    try:
        if app[MEDIA_STORE_KEY].delete(file_id):
            return
    except OSError:
        pass
    _defer_transcription_cleanup(app, None, file_id)


async def transcribe_upload(request: web.Request) -> web.Response:
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError):
        return web.json_response({"error": {"code": "multipart_required", "message": "请上传录音文件"}}, status=400)
    ephemeral = False
    item: StoredFile | None = None
    async for field in reader:
        if field.name == "ephemeral":
            ephemeral = (await field.text()).strip().casefold() in {"1", "true", "yes"}
            continue
        if field.name != "file" or not field.filename or item is not None:
            continue
        item = await request.app[MEDIA_STORE_KEY].save(field, category="audio")
    if item is None:
        return web.json_response({"error": {"code": "file_required", "message": "缺少录音文件"}}, status=400)
    if not item.mime_type.startswith("audio/"):
        request.app[MEDIA_STORE_KEY].delete(item.id)
        return web.json_response({"error": {"code": "audio_required", "message": "上传内容不是音频"}}, status=400)

    file_payload = item.public_dict()
    worker_task: asyncio.Task | None = None
    cleanup_deferred = False
    try:
        transcriber = request.app[TRANSCRIBE_AUDIO_KEY]
        try:
            if transcriber is None:
                online_config = request.app[GATEWAY_CONFIG_KEY].openai_asr
                if online_config.configured:
                    transcription = transcribe_openai_compatible(
                        request.app[HTTP_SESSION_KEY],
                        online_config,
                        path=item.server_path,
                        filename=item.name,
                        mime_type=item.mime_type,
                        prompt=TRANSCRIPTION_PROMPT,
                    )
                else:
                    transcription = _transcribe_via_hermes_api(request.app, _request_profile(request), item)
            else:
                worker_task = asyncio.create_task(asyncio.to_thread(transcriber, item.server_path))
                transcription = asyncio.shield(worker_task)
            result = await asyncio.wait_for(
                transcription,
                timeout=TRANSCRIPTION_TIMEOUT_SECONDS,
            )
        except asyncio.CancelledError:
            if worker_task is not None:
                _defer_transcription_cleanup(
                    request.app,
                    worker_task,
                    item.id if ephemeral else None,
                )
                cleanup_deferred = ephemeral
            raise
        except asyncio.TimeoutError:
            if worker_task is not None:
                _defer_transcription_cleanup(
                    request.app,
                    worker_task,
                    item.id if ephemeral else None,
                )
                cleanup_deferred = ephemeral
            return web.json_response({
                "error": {
                    "code": "transcription_timeout",
                    "message": "语音转写超时，请重试",
                },
                "file": file_payload,
            }, status=504)
        except HermesUpstreamAuthError:
            return web.json_response({
                **_hermes_auth_failed_error(),
                "file": file_payload,
            }, status=502)
        except Exception:
            return web.json_response({
                "error": {
                    "code": "transcription_failed",
                    "message": "语音转写失败",
                },
                "file": file_payload,
            }, status=503)
        if not result.get("success"):
            return web.json_response({
                "error": {
                    "code": str(result.get("code") or "transcription_failed"),
                    "message": str(result.get("message") or "语音转写失败"),
                },
                "file": file_payload,
            }, status=int(result.get("status") or 503))
        return web.json_response({
            "object": "nexus.audio.transcription",
            "transcript": str(result.get("transcript", "")).strip(),
            "provider": result.get("provider"),
            "file": file_payload,
        })
    finally:
        if ephemeral and not cleanup_deferred:
            _delete_ephemeral_or_retry(request.app, item.id)


async def _synthesize_via_edge_tts(
    text: str,
    voice: str,
    max_audio_bytes: int = SPEECH_MAX_AUDIO_BYTES,
) -> bytes:
    try:
        import edge_tts
    except ImportError as error:
        raise RuntimeError("Gateway 未安装语音合成组件") from error

    audio = bytearray()
    communicator = edge_tts.Communicate(text=text, voice=voice)
    async for chunk in communicator.stream():
        if chunk.get("type") == "audio":
            data = chunk.get("data") or b""
            if len(audio) + len(data) > max_audio_bytes:
                raise SpeechAudioTooLargeError("synthesized audio exceeds the response limit")
            audio.extend(data)
    if not audio:
        raise RuntimeError("语音服务没有返回音频")
    return bytes(audio)


async def synthesize_speech(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):
        return web.json_response(
            {"error": {"code": "invalid_json", "message": "请求 JSON 无效"}},
            status=400,
        )
    if not isinstance(body, dict):
        body = {}
    text = re.sub(r"\s+", " ", str(body.get("text") or "")).strip()
    if not text:
        return web.json_response(
            {"error": {"code": "speech_text_required", "message": "缺少要朗读的文本"}},
            status=400,
        )
    if len(text) > SPEECH_MAX_CHARS:
        return web.json_response(
            {"error": {"code": "speech_text_too_long", "message": "单段朗读文本过长"}},
            status=413,
        )
    voice_id = str(body.get("voice") or "xiaoxiao").strip().casefold()
    voice = SPEECH_VOICES.get(voice_id)
    if voice is None:
        return web.json_response(
            {"error": {"code": "unsupported_voice", "message": "不支持该音色"}},
            status=400,
        )
    synthesizer = request.app[SYNTHESIZE_SPEECH_KEY]
    semaphore = request.app[SPEECH_SEMAPHORE_KEY]
    max_audio_bytes = request.app[SPEECH_MAX_AUDIO_BYTES_KEY]

    async def synthesize() -> bytes:
        async with semaphore:
            if synthesizer is None:
                return await _synthesize_via_edge_tts(text, voice, max_audio_bytes)
            result = synthesizer(text, voice)
            return await result if asyncio.iscoroutine(result) else result

    try:
        audio = await asyncio.wait_for(synthesize(), timeout=SPEECH_TIMEOUT.total)
    except asyncio.TimeoutError:
        return web.json_response(
            {"error": {"code": "speech_timeout", "message": "语音合成超时，请重试"}},
            status=504,
        )
    except SpeechAudioTooLargeError:
        return web.json_response(
            {"error": {"code": "speech_response_too_large", "message": "Speech audio response is too large"}},
            status=502,
        )
    except Exception:
        return web.json_response(
            {"error": {"code": "speech_failed", "message": "语音合成暂不可用"}},
            status=503,
        )
    if not isinstance(audio, (bytes, bytearray)) or not audio:
        return web.json_response(
            {"error": {"code": "speech_failed", "message": "语音服务没有返回音频"}},
            status=503,
        )
    if len(audio) > max_audio_bytes:
        return web.json_response(
            {"error": {"code": "speech_response_too_large", "message": "Speech audio response is too large"}},
            status=502,
        )
    return web.Response(
        body=bytes(audio),
        content_type="audio/mpeg",
        headers={"Cache-Control": "no-store", "X-Nexus-Voice": voice_id},
    )


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
    download_url: str | None = None,
) -> dict[str, Any]:
    if item.mime_type in IMAGE_MIME_TYPES and kind != "file":
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{item.mime_type};base64,{encoded}"}}
    note = f"附件：{item.name}\n类型：{item.mime_type}\n大小：{item.size} 字节"
    if mobile_client:
        note += "\n来源：Nexus Android 手机端附件"
    if download_url:
        note += (
            f"\n下载地址：{download_url}"
            "\n读取说明：这是短时有效的只读附件地址；请立即通过 HTTP/Web 工具读取实际文件内容。"
        )
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
    session_storage_id: str | None = None,
) -> dict[str, Any]:
    attachment_ids = body.pop("attachment_ids", [])
    attachment_kinds = body.pop("attachment_kinds", {})
    if not isinstance(attachment_ids, list):
        raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "invalid_attachment_ids", "message": "attachment_ids 必须是数组"}}, ensure_ascii=False), content_type="application/json")
    if not attachment_ids:
        return body
    if not isinstance(attachment_kinds, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "invalid_attachment_kinds", "message": "attachment_kinds 必须是对象"}}, ensure_ascii=False), content_type="application/json")
    if len(attachment_ids) > MAX_CHAT_ATTACHMENTS:
        raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "too_many_attachments", "message": f"单条消息最多包含 {MAX_CHAT_ATTACHMENTS} 个附件"}}, ensure_ascii=False), content_type="application/json")

    normalized_ids = [str(file_id) for file_id in attachment_ids]
    if len(set(normalized_ids)) != len(normalized_ids):
        raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "duplicate_attachment_id", "message": "附件列表包含重复文件"}}, ensure_ascii=False), content_type="application/json")

    resolved_attachments: list[tuple[StoredFile, Path]] = []
    total_attachment_bytes = 0
    for file_id in normalized_ids:
        found = request.app[MEDIA_STORE_KEY].get(file_id)
        if found is None:
            raise web.HTTPBadRequest(text=json.dumps({"error": {"code": "attachment_not_found", "message": f"附件不存在：{file_id}"}}, ensure_ascii=False), content_type="application/json")
        total_attachment_bytes += found[0].size
        if total_attachment_bytes > MAX_CHAT_ATTACHMENT_BYTES:
            raise web.HTTPRequestEntityTooLarge(
                max_size=MAX_CHAT_ATTACHMENT_BYTES,
                actual_size=total_attachment_bytes,
                text=json.dumps({"error": {"code": "attachments_too_large", "message": "附件总大小超过单条消息限制"}}, ensure_ascii=False),
                content_type="application/json",
            )
        resolved_attachments.append(found)

    current_message = body.get("message", "")
    parts: list[dict[str, Any]] = []
    if isinstance(current_message, str) and current_message.strip():
        parts.append({"type": "text", "text": current_message})
    elif isinstance(current_message, list):
        parts.extend(current_message)
    request[REQUEST_ATTACHMENT_IDS_KEY] = normalized_ids
    marker = current_message.strip() if isinstance(current_message, str) else ""
    for file_id, found in zip(normalized_ids, resolved_attachments):
        parts.append(
            _attachment_part(
                *found,
                kind=str(attachment_kinds.get(file_id, "")),
                mobile_client=mobile_client,
                download_url=_attachment_share_url(request, file_id),
            )
        )
    session_match = re.fullmatch(r"/api/sessions/([^/]+)/chat(?:/stream)?", request.path)
    if session_match:
        request.app[MEDIA_STORE_KEY].record_session_media(
            session_storage_id or session_match.group(1),
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
    profile = _request_profile(request)
    is_message_history = request.method == "GET" and re.fullmatch(r"/api/sessions/[^/]+/messages", request.path)
    if request.method == "GET" and re.fullmatch(r"/api/sessions/[^/]+/run", request.path):
        session_id = request.path.split("/")[3]
        tracking_id = _scoped_session_id(profile, session_id)
        local_status = request.app[RUN_TRACKER_KEY].status(tracking_id)
        local_status["session_id"] = session_id
        if local_status.get("active") or local_status.get("status") in {"queued", "running", "stopping"}:
            return web.json_response(local_status)
        observer = _external_run_observer(request.app, profile)
        external_status = await observer.status(request.app, session_id, profile)
        return web.json_response(external_status or local_status)
    if request.method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/run/stop", request.path):
        session_id = request.path.split("/")[3]
        tracking_id = _scoped_session_id(profile, session_id)
        tracker: RunTracker = request.app[RUN_TRACKER_KEY]
        task = tracker.tasks.get(tracking_id)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                # The run task owns its terminal status; stopping only waits for cleanup.
                pass
            return web.json_response({
                "session_id": session_id,
                "stopped": True,
                "local_forwarding_cancelled": True,
                "remote_status": "unknown",
                "remote_stopped": False,
            })
        return web.json_response({
            "session_id": session_id,
            "stopped": False,
            "local_forwarding_cancelled": False,
            "remote_status": "unknown",
            "remote_stopped": False,
        })
    if request.method == "POST" and re.fullmatch(r"/api/sessions/[^/]+/chat", request.path):
        return await _tracked_session_chat(request)
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
            if not isinstance(body, dict):
                return web.json_response({"error": {"code": "invalid_json", "message": "请求 JSON 必须是对象"}}, status=400)
            mobile_client = _apply_client_context(body)
            session_id = request.path.split("/")[3] if "/api/sessions/" in request.path else ""
            body = await _prepare_chat_body(
                request,
                body,
                mobile_client=mobile_client,
                session_storage_id=_scoped_session_id(profile, session_id) if session_id else None,
            )
            validation_error = _normalize_chat_model_config(body)
            if validation_error is not None:
                return validation_error
            selected_provider = str(body.get("provider") or "").strip()
            selected_model = str(body.get("model") or "").strip()
            if request.path.endswith("/chat") and selected_provider and selected_model:
                model_options = body.get("model_options")
                try:
                    await _lock_selected_session_model(
                        request,
                        profile,
                        session_id,
                        selected_provider,
                        selected_model,
                        model_options if isinstance(model_options, dict) else None,
                    )
                except HermesUpstreamAuthError:
                    return web.json_response(_hermes_auth_failed_error(), status=502)
                except HermesModelSelectionError as error:
                    return web.json_response(
                        {"error": {"code": error.code, "message": str(error)}},
                        status=400,
                    )
                body.pop("provider", None)
                body.pop("model", None)
                body.pop("require_model_lock", None)
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        else:
            body_bytes = await request.read()

    session = request.app[HTTP_SESSION_KEY]
    upstream = await session.request(
        request.method,
        f"{profile.upstream_url}{relative}",
        headers=_upstream_headers(request.app, request, profile),
        data=body_bytes,
        allow_redirects=False,
    )
    if upstream.status in {401, 403}:
        upstream.release()
        return web.json_response(_hermes_auth_failed_error(), status=502)
    if upstream.status >= 500:
        upstream.release()
        return web.json_response(
            {"error": {"code": "hermes_upstream_error", "message": "上游服务暂时不可用"}},
            status=502,
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
        payload = request.app[MEDIA_STORE_KEY].enrich_session_messages(
            _scoped_session_id(profile, session_id),
            payload,
        )
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


def _normalize_chat_model_config(body: dict[str, Any]) -> web.Response | None:
    body.pop("persona_model", None)
    legacy_model = body.pop("inference_model", None)
    legacy_provider = body.pop("inference_provider", None)
    if "model" not in body and legacy_model is not None and legacy_model != "":
        body["model"] = legacy_model
    if "provider" not in body and legacy_provider is not None and legacy_provider != "":
        body["provider"] = legacy_provider

    for field in ("model", "provider"):
        value = body.get(field)
        if value is None or value == "":
            body.pop(field, None)
        elif not isinstance(value, str) or not value.strip():
            return web.json_response(
                {"error": {"code": "invalid_model_selection", "message": "模型配置无效"}},
                status=400,
            )
        else:
            body[field] = value.strip()
    if "provider" in body and "model" not in body:
        return web.json_response(
            {"error": {"code": "invalid_model_selection", "message": "选择供应商时必须同时选择模型"}},
            status=400,
        )
    if "require_model_lock" in body and not isinstance(body["require_model_lock"], bool):
        return web.json_response(
            {"error": {"code": "invalid_model_selection", "message": "模型锁定配置无效"}},
            status=400,
        )
    if "model" in body and "provider" in body:
        body.setdefault("require_model_lock", True)
    elif body.get("require_model_lock") is True:
        return web.json_response(
            {"error": {"code": "invalid_model_selection", "message": "锁定模型时必须同时选择供应商和模型"}},
            status=400,
        )

    raw_model_options = body.get("model_options")
    if raw_model_options is None:
        model_options: dict[str, Any] = {}
    elif not isinstance(raw_model_options, dict):
        return web.json_response(
            {"error": {"code": "invalid_model_options", "message": "模型参数无效"}},
            status=400,
        )
    else:
        model_options = dict(raw_model_options)
    legacy_reasoning_effort = body.pop("reasoning_effort", None)
    if legacy_reasoning_effort is not None and legacy_reasoning_effort != "":
        model_options["reasoning_effort"] = legacy_reasoning_effort
    raw_reasoning_effort = model_options.get("reasoning_effort")
    if raw_reasoning_effort is None or raw_reasoning_effort == "":
        model_options.pop("reasoning_effort", None)
    elif not isinstance(raw_reasoning_effort, str) or raw_reasoning_effort.strip().lower() not in ALLOWED_REASONING_EFFORTS:
        return web.json_response(
            {"error": {"code": "invalid_reasoning_effort", "message": "推理深度无效"}},
            status=400,
        )
    else:
        model_options["reasoning_effort"] = raw_reasoning_effort.strip().lower()
    if model_options:
        body["model_options"] = model_options
    else:
        body.pop("model_options", None)
    return None


def _session_sse_event(event_name: str, payload: dict[str, Any] | None = None) -> bytes:
    data = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event_name}\ndata: {data}\n\n".encode("utf-8")


def _parse_session_sse_block(block: bytes) -> tuple[str, dict[str, Any]]:
    event_name = ""
    data_lines: list[str] = []
    for raw_line in block.decode("utf-8", errors="replace").splitlines():
        if raw_line.startswith("event:"):
            event_name = raw_line.partition(":")[2].strip()
        elif raw_line.startswith("data:"):
            data_lines.append(raw_line.partition(":")[2].lstrip())
    data_text = "\n".join(data_lines).strip()
    try:
        payload = json.loads(data_text) if data_text else {}
    except json.JSONDecodeError:
        payload = {}
    return event_name, payload if isinstance(payload, dict) else {}


def _friendly_model_setup_error(event_name: str, payload: dict[str, Any]) -> dict[str, str] | None:
    message = str(payload.get("message") or "")
    if event_name != "error" or "no llm provider configured" not in message.casefold():
        return None
    return {
        "code": "model_not_configured",
        "message": HERMES_MODEL_NOT_CONFIGURED_MESSAGE,
    }


def _safe_upstream_stream_error(
    code: str = "hermes_upstream_error",
    message: str = "上游服务暂时不可用",
) -> bytes:
    return _session_sse_event("error", {"code": code, "message": message})


def _canonical_model_provider(provider: Any) -> str:
    value = str(provider or "").strip()
    return value.removeprefix("custom:")


async def _lock_selected_session_model(
    request: web.Request,
    profile: HermesProfileConfig,
    session_id: str,
    provider: str,
    model: str,
    model_options: dict[str, Any] | None,
) -> bool:
    """Validate a picker selection and persist it through Hermes' public API."""
    session = request.app[HTTP_SESSION_KEY]
    headers = _upstream_headers(request.app, request, profile)
    headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    options_response = None
    for attempt in range(MODEL_CATALOG_ATTEMPTS):
        try:
            options_response = await session.get(
                f"{profile.upstream_url}/api/model/options?explicit_only=true&refresh=0",
                headers=headers,
                timeout=MODEL_CATALOG_TIMEOUT,
            )
            break
        except asyncio.TimeoutError:
            if attempt + 1 >= MODEL_CATALOG_ATTEMPTS:
                raise HermesModelSelectionError(
                    "model_catalog_timeout",
                    "读取 Hermes 模型列表超时，请重试",
                ) from None
    if options_response is None:
        raise HermesModelSelectionError(
            "model_catalog_timeout",
            "读取 Hermes 模型列表超时，请重试",
        )
    try:
        if options_response.status in {401, 403}:
            raise HermesUpstreamAuthError()
        if not options_response.ok:
            raise HermesModelSelectionError(
                "model_catalog_unavailable",
                "Hermes 模型列表暂时不可用，请刷新后重试",
            )
        try:
            options_payload = await options_response.json(content_type=None)
        except asyncio.TimeoutError:
            raise HermesModelSelectionError(
                "model_catalog_timeout",
                "读取 Hermes 模型列表超时，请重试",
            ) from None
        except (json.JSONDecodeError, ValueError):
            raise HermesModelSelectionError(
                "model_catalog_unavailable",
                "Hermes 模型列表响应无效，请刷新后重试",
            )
    finally:
        options_response.release()

    providers = options_payload.get("providers") if isinstance(options_payload, dict) else None
    provider_slug = _canonical_model_provider(provider)
    selected_entry = next(
        (
            entry
            for entry in providers or []
            if isinstance(entry, dict)
            and _canonical_model_provider(entry.get("slug")) == provider_slug
        ),
        None,
    )
    available_models = selected_entry.get("models") if isinstance(selected_entry, dict) else None
    unavailable_models = selected_entry.get("unavailable_models") if isinstance(selected_entry, dict) else None
    if (
        not isinstance(selected_entry, dict)
        or selected_entry.get("authenticated") is not True
        or not isinstance(available_models, list)
        or model not in available_models
        or (isinstance(unavailable_models, list) and model in unavailable_models)
    ):
        raise HermesModelSelectionError(
            "model_not_available",
            "所选模型已不可用，请刷新模型列表后重试",
        )

    lock_body: dict[str, Any] = {
        "provider": provider,
        "model": model,
        # Hermes 0.20 reports user-config providers as `custom` at runtime.
        # Validation below and the stream adapter retain strict model checking.
        "require_model_lock": False,
    }
    if model_options:
        lock_body["model_options"] = dict(model_options)
    try:
        lock_response = await session.post(
            f"{profile.upstream_url}/api/sessions/{quote(session_id, safe='')}/model",
            headers=headers,
            data=json.dumps(lock_body, ensure_ascii=False).encode("utf-8"),
            timeout=MODEL_LOCK_TIMEOUT,
        )
    except asyncio.TimeoutError:
        raise HermesModelSelectionError(
            "model_lock_timeout",
            "Hermes 应用所选模型超时，请重试",
        ) from None
    try:
        if lock_response.status in {401, 403}:
            raise HermesUpstreamAuthError()
        if not lock_response.ok:
            raise HermesModelSelectionError(
                "model_lock_failed",
                "Hermes 无法应用所选模型，请刷新模型列表后重试",
            )
        try:
            lock_payload = await lock_response.json(content_type=None)
        except asyncio.TimeoutError:
            raise HermesModelSelectionError(
                "model_lock_timeout",
                "Hermes 应用所选模型超时，请重试",
            ) from None
        except (json.JSONDecodeError, ValueError):
            raise HermesModelSelectionError(
                "model_lock_failed",
                "Hermes 模型锁定响应无效，请刷新模型列表后重试",
            )
    finally:
        lock_response.release()

    runtime = lock_payload.get("runtime") if isinstance(lock_payload, dict) else None
    acknowledged_provider = runtime.get("provider") if isinstance(runtime, dict) else None
    acknowledged_model = runtime.get("model") if isinstance(runtime, dict) else None
    if (
        acknowledged_model != model
        or _canonical_model_provider(acknowledged_provider) != provider_slug
    ):
        raise HermesModelSelectionError(
            "model_lock_failed",
            "Hermes 未确认所选模型，请刷新模型列表后重试",
        )
    return (
        str(selected_entry.get("source", "")).strip().lower() == "user-config"
        or selected_entry.get("is_user_defined") is True
    )


class _NativeSelectedModelStreamAdapter:
    """Preserve Hermes session SSE while reconciling its custom-provider alias."""

    _ALIAS_MISMATCH = re.compile(
        r"confirmed model lock runtime mismatch: "
        r"expected provider=(?P<expected_provider>\S+) model=(?P<expected_model>.+?); "
        r"actual provider=(?P<actual_provider>\S+) model=(?P<actual_model>.+)"
    )

    def __init__(self, provider: str, model: str, allow_custom_alias: bool) -> None:
        self.provider = provider
        self.model = model
        self.allow_custom_alias = allow_custom_alias
        self._buffer = b""
        self._lock_confirmed = False
        self._assistant_output_seen = False
        self._suppress_upstream_done = False
        self._discard_remaining = False
        self.outcome: str | None = None

    def feed(self, chunk: bytes) -> list[bytes]:
        if self._discard_remaining:
            return []
        if len(self._buffer) + len(chunk) > SSE_EVENT_BUFFER_LIMIT:
            self._buffer = b""
            self._discard_remaining = True
            self.outcome = "failed"
            return [_safe_upstream_stream_error(
                "upstream_event_too_large",
                "Hermes 响应事件过大，流已终止",
            )]
        self._buffer += chunk
        blocks = re.split(br"\r?\n\r?\n", self._buffer)
        self._buffer = blocks.pop() if blocks else b""
        events: list[bytes] = []
        for block in blocks:
            events.extend(self._convert_block(block))
            if self._discard_remaining:
                self._buffer = b""
                break
        return events

    def finish(self) -> list[bytes]:
        if self._discard_remaining:
            return []
        events: list[bytes] = []
        if self._buffer.strip():
            events.extend(self._convert_block(self._buffer))
        self._buffer = b""
        if self.outcome is None:
            self.outcome = "failed"
            events.append(_session_sse_event("error", {"message": "Hermes 模型请求失败：流意外结束"}))
        return events

    def _convert_block(self, block: bytes) -> list[bytes]:
        if not block.strip():
            return []
        event_name, payload = _parse_session_sse_block(block)
        friendly_error = _friendly_model_setup_error(event_name, payload)
        if friendly_error is not None:
            self.outcome = "failed"
            return [_session_sse_event("error", friendly_error)]

        if event_name == "run.started":
            runtime = payload.get("runtime")
            self._lock_confirmed = self._runtime_confirms_selection(runtime)
            if not self._lock_confirmed:
                return self._fail_model_lock()
        elif event_name == "assistant.delta":
            if not self._lock_confirmed:
                return self._fail_model_lock()
            self._assistant_output_seen = bool(payload.get("delta")) or self._assistant_output_seen
        elif event_name == "assistant.completed":
            if not self._lock_confirmed:
                return self._fail_model_lock()
            self._assistant_output_seen = True
            self.outcome = "completed"
        elif event_name == "run.completed":
            if not self._lock_confirmed:
                return self._fail_model_lock()
            completed_runtime = payload.get("runtime")
            if completed_runtime is not None and not self._runtime_confirms_selection(completed_runtime):
                return self._fail_model_lock()
            self.outcome = "completed"
        elif event_name == "error":
            upstream_message = str(payload.get("message", ""))
            runtime = self._custom_alias_runtime(upstream_message)
            if runtime is not None:
                self.outcome = "completed"
                self._suppress_upstream_done = True
                return [_session_sse_event("run.completed", {"completed": True, "runtime": runtime})]
            self.outcome = "failed"
            if self._ALIAS_MISMATCH.fullmatch(upstream_message.strip()) is not None:
                return [_safe_upstream_stream_error(
                    "model_lock_failed",
                    "Hermes 未确认所选模型，请刷新模型列表后重试",
                )]
            return [_safe_upstream_stream_error()]
        elif event_name == "done":
            if self._suppress_upstream_done:
                return []
            if not self._lock_confirmed:
                return self._fail_model_lock()
            if self.outcome is None:
                self.outcome = "completed"
                return [_session_sse_event("run.completed", {"completed": True})]

        return [block + b"\n\n"]

    def _runtime_confirms_selection(self, runtime: Any) -> bool:
        if not isinstance(runtime, dict):
            return False
        requested = runtime.get("requested")
        actual_provider = str(runtime.get("provider") or "").strip()
        actual_model = str(runtime.get("model") or "").strip()
        actual_runtime_pending = (
            runtime.get("model_lock") == "accepted"
            and not actual_provider
            and not actual_model
        )
        provider_matches = (
            _canonical_model_provider(actual_provider) == _canonical_model_provider(self.provider)
            or (self.allow_custom_alias and actual_provider == "custom")
        )
        return bool(
            isinstance(requested, dict)
            and runtime.get("route_source") == "session_model_lock"
            and runtime.get("model_lock") in {"accepted", "confirmed"}
            and _canonical_model_provider(requested.get("provider"))
            == _canonical_model_provider(self.provider)
            and requested.get("model") == self.model
            and (
                actual_runtime_pending
                or (actual_model == self.model and provider_matches)
            )
        )

    def _fail_model_lock(self) -> list[bytes]:
        self.outcome = "failed"
        self._discard_remaining = True
        return [_safe_upstream_stream_error(
            "model_lock_failed",
            "Hermes 未确认所选模型，请刷新模型列表后重试",
        )]

    def _custom_alias_runtime(self, message: str) -> dict[str, Any] | None:
        if not self.allow_custom_alias or not self._lock_confirmed or not self._assistant_output_seen:
            return None
        match = self._ALIAS_MISMATCH.fullmatch(message.strip())
        if match is None:
            return None
        expected_provider = match.group("expected_provider")
        expected_model = match.group("expected_model")
        actual_provider = match.group("actual_provider")
        actual_model = match.group("actual_model")
        if (
            _canonical_model_provider(expected_provider) != _canonical_model_provider(self.provider)
            or expected_model != self.model
            or actual_provider != "custom"
            or actual_model != self.model
        ):
            return None
        return {
            "provider": actual_provider,
            "model": actual_model,
            "route_source": "session_model_lock",
            "model_lock": "confirmed",
            "requested": {"provider": self.provider, "model": self.model},
        }


class _HermesSessionStreamSanitizer:
    def __init__(self) -> None:
        self._buffer = b""
        self.model_not_configured = False
        self.outcome: str | None = None
        self.message: str | None = None
        self._discard_remaining = False

    def feed(self, chunk: bytes) -> list[bytes]:
        if self._discard_remaining:
            return []
        if len(self._buffer) + len(chunk) > SSE_EVENT_BUFFER_LIMIT:
            self._buffer = b""
            self._discard_remaining = True
            self.outcome = "failed"
            self.message = "Hermes 响应事件过大，流已终止"
            return [_safe_upstream_stream_error(
                "upstream_event_too_large",
                self.message,
            )]
        self._buffer += chunk
        blocks = re.split(br"\r?\n\r?\n", self._buffer)
        self._buffer = blocks.pop() if blocks else b""
        return [event for block in blocks for event in self._convert_block(block)]

    def finish(self) -> list[bytes]:
        if self._discard_remaining:
            return []
        events = self._convert_block(self._buffer) if self._buffer.strip() else []
        self._buffer = b""
        if self.outcome is None:
            self.outcome = "failed"
            self.message = "Hermes 响应流意外结束"
            events.append(_session_sse_event("error", {"message": self.message}))
        return events

    def _convert_block(self, block: bytes) -> list[bytes]:
        if not block.strip():
            return []
        event_name, payload = _parse_session_sse_block(block)
        friendly_error = _friendly_model_setup_error(event_name, payload)
        if friendly_error is not None:
            self.model_not_configured = True
            self.outcome = "failed"
            self.message = friendly_error["message"]
            return [_session_sse_event("error", friendly_error)]
        if event_name == "error":
            self.outcome = "failed"
            self.message = "上游服务暂时不可用"
            return [_safe_upstream_stream_error()]
        elif event_name in {"run.completed", "assistant.completed", "done"} and self.outcome is None:
            self.outcome = "completed"
        return [block + b"\n\n"]


async def _tracked_session_chat(request: web.Request) -> web.StreamResponse:
    """Track legacy /chat without changing its HTTP response contract."""
    profile = _request_profile(request)
    session_id = request.path.split("/")[3]
    tracking_id = _scoped_session_id(profile, session_id)
    tracker: RunTracker = request.app[RUN_TRACKER_KEY]
    if not tracker.reserve(tracking_id):
        return web.json_response(
            {"error": {"code": "run_active", "message": "该会话仍在处理中"}},
            status=409,
        )

    try:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            tracker.release_reservation(tracking_id)
            return web.json_response(
                {"error": {"code": "invalid_json", "message": "请求 JSON 无效"}},
                status=400,
            )
        if not isinstance(body, dict):
            tracker.release_reservation(tracking_id)
            return web.json_response(
                {"error": {"code": "invalid_json", "message": "请求 JSON 必须是对象"}},
                status=400,
            )
        mobile_client = _apply_client_context(body)
        body = await _prepare_chat_body(
            request,
            body,
            mobile_client=mobile_client,
            session_storage_id=tracking_id,
        )
        validation_error = _normalize_chat_model_config(body)
        if validation_error is not None:
            tracker.release_reservation(tracking_id)
            return validation_error
    except BaseException:
        tracker.release_reservation(tracking_id)
        raise

    selected_provider = str(body.get("provider") or "").strip()
    selected_model = str(body.get("model") or "").strip()
    use_selected_model = bool(selected_provider and selected_model)
    upstream_body = dict(body)
    if use_selected_model:
        upstream_body.pop("provider", None)
        upstream_body.pop("model", None)
        upstream_body.pop("require_model_lock", None)

    current_task = asyncio.current_task()
    if current_task is None:
        tracker.release_reservation(tracking_id)
        raise RuntimeError("chat request task is unavailable")
    tracker.start(tracking_id, f"run_{uuid.uuid4().hex}")
    tracker.attach(tracking_id, current_task)
    upstream = None
    try:
        allow_custom_alias = False
        if use_selected_model:
            model_options = body.get("model_options")
            try:
                allow_custom_alias = await _lock_selected_session_model(
                    request,
                    profile,
                    session_id,
                    selected_provider,
                    selected_model,
                    model_options if isinstance(model_options, dict) else None,
                )
            except HermesUpstreamAuthError:
                tracker.finish(tracking_id, "failed", HERMES_AUTH_FAILED_MESSAGE)
                return web.json_response(_hermes_auth_failed_error(), status=502)
            except HermesModelSelectionError as error:
                attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
                if attachment_ids:
                    request.app[MEDIA_STORE_KEY].discard_last_session_media(tracking_id, attachment_ids)
                tracker.finish(tracking_id, "failed", str(error))
                return web.json_response(
                    {"error": {"code": error.code, "message": str(error)}},
                    status=400,
                )

        upstream_headers = _upstream_headers(request.app, request, profile)
        upstream = await request.app[HTTP_SESSION_KEY].post(
            f"{profile.upstream_url}{request.path_qs}",
            headers=upstream_headers,
            data=json.dumps(upstream_body, ensure_ascii=False).encode("utf-8"),
        )
        if upstream.status in {401, 403}:
            tracker.finish(tracking_id, "failed", HERMES_AUTH_FAILED_MESSAGE)
            return web.json_response(_hermes_auth_failed_error(), status=502)
        if upstream.status >= 500:
            tracker.finish(tracking_id, "failed", "上游服务暂时不可用")
            return web.json_response(
                {"error": {"code": "hermes_upstream_error", "message": "上游服务暂时不可用"}},
                status=502,
            )

        excluded = {"content-length", "transfer-encoding", "connection", "content-encoding"}
        headers = {key: value for key, value in upstream.headers.items() if key.lower() not in excluded}
        content_type = upstream.headers.get("Content-Type", "").partition(";")[0].strip().casefold()
        if use_selected_model and content_type != "text/event-stream":
            response_body = bytearray()
            async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                if len(response_body) + len(chunk) > SSE_EVENT_BUFFER_LIMIT:
                    tracker.finish(tracking_id, "failed", "Hermes 模型响应过大")
                    return web.json_response(
                        {"error": {
                            "code": "model_runtime_response_too_large",
                            "message": "Hermes 模型响应过大，无法验证实际运行模型",
                        }},
                        status=502,
                    )
                response_body.extend(chunk)
            try:
                response_payload = json.loads(response_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                response_payload = None
            runtime = response_payload.get("runtime") if isinstance(response_payload, dict) else None
            verifier = _NativeSelectedModelStreamAdapter(
                selected_provider,
                selected_model,
                allow_custom_alias,
            )
            if not verifier._runtime_confirms_selection(runtime):
                message = "Hermes 未确认所选模型，请刷新模型列表后重试"
                tracker.finish(tracking_id, "failed", message)
                return web.json_response(
                    {"error": {"code": "model_lock_failed", "message": message}},
                    status=502,
                )
            tracker.finish(tracking_id, "completed")
            return web.Response(
                body=bytes(response_body),
                status=upstream.status,
                reason=upstream.reason,
                headers=headers,
            )

        response = web.StreamResponse(status=upstream.status, reason=upstream.reason, headers=headers)
        client_attached = True
        try:
            await response.prepare(request)
        except ConnectionResetError:
            client_attached = False

        selected_adapter = (
            _NativeSelectedModelStreamAdapter(
                selected_provider,
                selected_model,
                allow_custom_alias,
            )
            if use_selected_model and content_type == "text/event-stream"
            else None
        )
        status_sanitizer = (
            _HermesSessionStreamSanitizer()
            if selected_adapter is None and content_type == "text/event-stream"
            else None
        )

        async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
            if selected_adapter is not None:
                outgoing = selected_adapter.feed(chunk)
                for event in outgoing:
                    tracker.consume_sse(tracking_id, event)
                    if client_attached:
                        try:
                            await response.write(event)
                        except ConnectionResetError:
                            client_attached = False
            else:
                if status_sanitizer is not None:
                    status_sanitizer.feed(chunk)
                    tracker.consume_sse(tracking_id, chunk)
                if client_attached:
                    try:
                        await response.write(chunk)
                    except ConnectionResetError:
                        client_attached = False

        if selected_adapter is not None:
            for event in selected_adapter.finish():
                tracker.consume_sse(tracking_id, event)
                if client_attached:
                    try:
                        await response.write(event)
                    except ConnectionResetError:
                        client_attached = False
            tracker.finish(tracking_id, selected_adapter.outcome or "failed")
        elif status_sanitizer is not None:
            status_sanitizer.finish()
            tracker.finish(
                tracking_id,
                status_sanitizer.outcome or ("completed" if upstream.ok else "failed"),
                status_sanitizer.message,
            )
        else:
            tracker.finish(tracking_id, "completed" if upstream.ok else "failed")

        if client_attached:
            try:
                await response.write_eof()
            except ConnectionResetError:
                pass
        return response
    except asyncio.CancelledError:
        tracker.finish(
            tracking_id,
            "interrupted",
            "Nexus local forwarding was cancelled; Hermes remote run status is unknown.",
        )
        raise
    except Exception:
        attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
        if attachment_ids:
            request.app[MEDIA_STORE_KEY].discard_last_session_media(tracking_id, attachment_ids)
        tracker.finish(tracking_id, "failed", "上游服务暂时不可用")
        raise
    finally:
        if upstream is not None:
            upstream.release()
        if tracker.tasks.get(tracking_id) is current_task:
            tracker.tasks.pop(tracking_id, None)


async def _tracked_session_stream(request: web.Request) -> web.StreamResponse:
    profile = _request_profile(request)
    session_id = request.path.split("/")[3]
    tracking_id = _scoped_session_id(profile, session_id)
    tracker: RunTracker = request.app[RUN_TRACKER_KEY]
    if not tracker.reserve(tracking_id):
        return web.json_response({"error": {"code": "run_active", "message": "该会话仍在处理中"}}, status=409)
    preparation_error: web.StreamResponse | None = None
    try:
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            preparation_error = web.json_response(
                {"error": {"code": "invalid_json", "message": "请求 JSON 无效"}},
                status=400,
            )
            body = {}
        if preparation_error is None and not isinstance(body, dict):
            preparation_error = web.json_response(
                {"error": {"code": "invalid_json", "message": "请求 JSON 必须是对象"}},
                status=400,
            )
        if preparation_error is None:
            mobile_client = _apply_client_context(body)
            body = await _prepare_chat_body(
                request,
                body,
                mobile_client=mobile_client,
                session_storage_id=tracking_id,
            )
            preparation_error = _normalize_chat_model_config(body)
        if preparation_error is None:
            selected_provider = str(body.get("provider") or "").strip()
            selected_model = str(body.get("model") or "").strip()
            use_selected_model = bool(selected_provider and selected_model)
            upstream_body = dict(body)
            if use_selected_model:
                # The original Hermes session model endpoint owns model selection.
                # Per-message provider fields use a different raw route in Hermes 0.20.
                upstream_body.pop("provider", None)
                upstream_body.pop("model", None)
                upstream_body.pop("require_model_lock", None)
            body_bytes = json.dumps(upstream_body, ensure_ascii=False).encode("utf-8")
            try:
                queue = tracker.subscribe(tracking_id)
            except RuntimeError:
                preparation_error = web.json_response(
                    {"error": {"code": "subscriber_limit", "message": "Run subscriber limit reached"}},
                    status=409,
                )
    except BaseException:
        tracker.release_reservation(tracking_id)
        raise
    if preparation_error is not None:
        tracker.release_reservation(tracking_id)
        return preparation_error

    async def consume_upstream() -> None:
        upstream = None
        try:
            upstream_headers = _upstream_headers(request.app, request, profile)
            upstream_headers.update({
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
            })
            allow_custom_alias = False
            if use_selected_model:
                model_options = body.get("model_options")
                allow_custom_alias = await _lock_selected_session_model(
                    request,
                    profile,
                    session_id,
                    selected_provider,
                    selected_model,
                    model_options if isinstance(model_options, dict) else None,
                )
            upstream = await request.app[HTTP_SESSION_KEY].post(
                f"{profile.upstream_url}{request.path_qs}",
                headers=upstream_headers,
                data=body_bytes,
            )
            if upstream.status in {401, 403}:
                raise HermesUpstreamAuthError()
            if not upstream.ok:
                raise RuntimeError(f"Hermes HTTP {upstream.status}")
            if use_selected_model:
                adapter = _NativeSelectedModelStreamAdapter(
                    selected_provider,
                    selected_model,
                    allow_custom_alias,
                )
                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    for event in adapter.feed(chunk):
                        tracker.consume_sse(tracking_id, event)
                        await tracker.publish(tracking_id, event)
                for event in adapter.finish():
                    tracker.consume_sse(tracking_id, event)
                    await tracker.publish(tracking_id, event)
                tracker.finish(tracking_id, adapter.outcome or "failed")
            else:
                sanitizer = _HermesSessionStreamSanitizer()
                async for chunk in upstream.content.iter_chunked(CHUNK_SIZE):
                    for event in sanitizer.feed(chunk):
                        tracker.consume_sse(tracking_id, event)
                        await tracker.publish(tracking_id, event)
                for event in sanitizer.finish():
                    tracker.consume_sse(tracking_id, event)
                    await tracker.publish(tracking_id, event)
                tracker.finish(
                    tracking_id,
                    sanitizer.outcome or "failed",
                    sanitizer.message,
                )
        except asyncio.CancelledError:
            tracker.finish(
                tracking_id,
                "interrupted",
                "Nexus local forwarding was cancelled; Hermes remote run status is unknown.",
            )
            raise
        except HermesUpstreamAuthError:
            attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
            if attachment_ids:
                request.app[MEDIA_STORE_KEY].discard_last_session_media(tracking_id, attachment_ids)
            tracker.finish(tracking_id, "failed", HERMES_AUTH_FAILED_MESSAGE)
            payload = json.dumps({
                "code": "hermes_auth_failed",
                "message": HERMES_AUTH_FAILED_MESSAGE,
            }, ensure_ascii=False)
            await tracker.publish(tracking_id, f"event: error\ndata: {payload}\n\n".encode("utf-8"))
        except HermesModelSelectionError as error:
            attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
            if attachment_ids:
                request.app[MEDIA_STORE_KEY].discard_last_session_media(tracking_id, attachment_ids)
            tracker.finish(tracking_id, "failed", str(error))
            payload = json.dumps({"code": error.code, "message": str(error)}, ensure_ascii=False)
            await tracker.publish(tracking_id, f"event: error\ndata: {payload}\n\n".encode("utf-8"))
        except Exception:
            attachment_ids = request.get(REQUEST_ATTACHMENT_IDS_KEY, [])
            if attachment_ids:
                request.app[MEDIA_STORE_KEY].discard_last_session_media(tracking_id, attachment_ids)
            tracker.finish(tracking_id, "failed", "上游服务暂时不可用")
            payload = json.dumps({"message": "上游服务暂时不可用"}, ensure_ascii=False)
            await tracker.publish(tracking_id, f"event: error\ndata: {payload}\n\n".encode("utf-8"))
        finally:
            if upstream is not None:
                upstream.release()
            await tracker.publish(tracking_id, b"event: done\ndata: {}\n\n")

    run_id = f"run_{uuid.uuid4().hex}"
    try:
        tracker.start(tracking_id, run_id)
        task = asyncio.create_task(consume_upstream())
        tracker.attach(tracking_id, task)
    except BaseException:
        tracker.release_reservation(tracking_id)
        tracker.unsubscribe(tracking_id, queue)
        raise

    def discard_completed_task(completed: asyncio.Task) -> None:
        if tracker.tasks.get(tracking_id) is completed:
            tracker.tasks.pop(tracking_id, None)

    task.add_done_callback(discard_completed_task)
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    try:
        await response.prepare(request)
        while True:
            chunk = await queue.get()
            if chunk is None:
                break
            await response.write(chunk)
            if task.done() and queue.empty():
                break
    except (asyncio.CancelledError, ConnectionResetError):
        # Detach the phone only; the gateway keeps reading Hermes to completion.
        pass
    finally:
        tracker.unsubscribe(tracking_id, queue)
    return response


def _valid_file_id(file_id: str) -> bool:
    return len(file_id) == 32 and all(char in "0123456789abcdef" for char in file_id)


@dataclass(frozen=True)
class _DirectoryIdentity:
    device: int
    inode: int


def _is_reparse_entry(path: Path, entry_stat: os.stat_result) -> bool:
    if path.is_symlink():
        return True
    if hasattr(os.path, "isjunction") and os.path.isjunction(path):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(getattr(entry_stat, "st_file_attributes", 0) & reparse_flag)


class _ManagedPathGuard:
    """Pins Nexus-owned directory identities and rejects redirected write paths."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        self.root.mkdir(parents=True, exist_ok=True)
        root_stat = self._directory_stat(self.root)
        self._anchors: dict[Path, _DirectoryIdentity] = {
            self.root: self._identity(root_stat),
        }
        if os.name != "nt":
            os.chmod(self.root, 0o700, follow_symlinks=False)

    @staticmethod
    def _identity(entry_stat: os.stat_result) -> _DirectoryIdentity:
        return _DirectoryIdentity(entry_stat.st_dev, entry_stat.st_ino)

    @staticmethod
    def _directory_stat(path: Path) -> os.stat_result:
        entry_stat = path.lstat()
        if not stat.S_ISDIR(entry_stat.st_mode) or _is_reparse_entry(path, entry_stat):
            raise OSError(f"managed directory is redirected or is not a directory: {path}")
        return entry_stat

    def _managed_path(self, path: Path) -> Path:
        candidate = Path(os.path.abspath(os.fspath(path)))
        try:
            candidate.relative_to(self.root)
        except ValueError as error:
            raise OSError(f"path is outside the Nexus data directory: {candidate}") from error
        return candidate

    def _verify_anchor(self, path: Path, entry_stat: os.stat_result) -> None:
        expected = self._anchors.get(path)
        if expected is not None and self._identity(entry_stat) != expected:
            raise OSError(f"managed directory identity changed: {path}")

    def ensure_directory(self, path: Path, *, pin: bool = False) -> Path:
        target = self._managed_path(path)
        root_stat = self._directory_stat(self.root)
        self._verify_anchor(self.root, root_stat)
        current = self.root
        relative = target.relative_to(self.root)
        for part in relative.parts:
            current = current / part
            try:
                entry_stat = self._directory_stat(current)
            except FileNotFoundError:
                self._verify_anchor(self.root, self._directory_stat(self.root))
                os.mkdir(current, 0o700)
                entry_stat = self._directory_stat(current)
            self._verify_anchor(current, entry_stat)
        if pin:
            self._anchors[target] = self._identity(self._directory_stat(target))
        return target

    def verify_directory(
        self,
        path: Path,
        expected: _DirectoryIdentity | None = None,
    ) -> _DirectoryIdentity:
        target = self.ensure_directory(path)
        entry_stat = self._directory_stat(target)
        identity = self._identity(entry_stat)
        if expected is not None and identity != expected:
            raise OSError(f"managed directory identity changed during write: {target}")
        return identity

    def verify_write_target(self, path: Path) -> tuple[Path, _DirectoryIdentity]:
        target = self._managed_path(path)
        if target == self.root:
            raise OSError("the Nexus data directory cannot be used as a file target")
        parent = self.ensure_directory(target.parent)
        parent_identity = self.verify_directory(parent)
        try:
            target_stat = target.lstat()
        except FileNotFoundError:
            pass
        else:
            if (
                not stat.S_ISREG(target_stat.st_mode)
                or _is_reparse_entry(target, target_stat)
                or target_stat.st_nlink != 1
            ):
                raise OSError(f"managed file target is redirected or hard-linked: {target}")
        return parent, parent_identity

    def unlink_file(self, path: Path, *, missing_ok: bool = True) -> None:
        target = self._managed_path(path)
        try:
            parent, parent_identity = self.verify_write_target(target)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        if not _path_entry_exists(target):
            if missing_ok:
                return
            raise FileNotFoundError(target)
        use_directory_handle = os.name != "nt" and os.unlink in os.supports_dir_fd
        if use_directory_handle:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_descriptor = os.open(parent, directory_flags)
            try:
                opened_parent = os.fstat(directory_descriptor)
                if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
                    raise OSError("managed parent changed before file removal")
                os.unlink(target.name, dir_fd=directory_descriptor)
            finally:
                os.close(directory_descriptor)
        else:
            self.verify_directory(parent, parent_identity)
            target.unlink(missing_ok=missing_ok)


def _secure_atomic_write(
    path: Path,
    content: str,
    path_guard: _ManagedPathGuard | None = None,
) -> None:
    guard = path_guard or _ManagedPathGuard(path.parent)
    path = guard._managed_path(path)
    parent, parent_identity = guard.verify_write_target(path)
    temporary_name = f".{path.name}.{uuid.uuid4().hex}.tmp"
    temporary = parent / temporary_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = -1
    directory_descriptor = -1
    use_directory_handle = os.name != "nt" and os.open in os.supports_dir_fd
    try:
        if use_directory_handle:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_descriptor = os.open(parent, directory_flags)
            opened_parent = os.fstat(directory_descriptor)
            if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
                raise OSError("managed parent changed before temporary file creation")
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        else:
            guard.verify_directory(parent, parent_identity)
            descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        guard.verify_write_target(path)
        guard.verify_directory(parent, parent_identity)
        if use_directory_handle:
            opened_parent = os.fstat(directory_descriptor)
            if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
                raise OSError("managed parent changed before atomic replacement")
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
        else:
            os.replace(temporary, path)
        if os.name != "nt" and stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode) != 0o600:
            raise OSError("persisted file permissions are not owner-only")
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if directory_descriptor != -1:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            os.close(directory_descriptor)
        else:
            try:
                guard.verify_directory(parent, parent_identity)
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _read_secure_existing_file(path: Path) -> str | None:
    try:
        file_stat = path.lstat()
        if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink() or file_stat.st_nlink != 1:
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


def _secure_exclusive_write(
    path: Path,
    content: str,
    path_guard: _ManagedPathGuard | None = None,
) -> _OwnedFileIdentity:
    guard = path_guard or _ManagedPathGuard(path.parent)
    path = guard._managed_path(path)
    parent, parent_identity = guard.verify_write_target(path)
    flags = _bootstrap_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    descriptor = -1
    directory_descriptor = -1
    use_directory_handle = os.name != "nt" and os.open in os.supports_dir_fd
    if use_directory_handle:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = os.open(parent, directory_flags)
        opened_parent = os.fstat(directory_descriptor)
        if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
            os.close(directory_descriptor)
            raise OSError("managed parent changed before exclusive file creation")
        descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
    else:
        guard.verify_directory(parent, parent_identity)
        descriptor = os.open(path, flags, 0o600)
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
        _unlink_owned_file(path, written_identity, guard)
        raise
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if directory_descriptor != -1:
            os.close(directory_descriptor)


def _unlink_owned_file(
    path: Path,
    identity: _OwnedFileIdentity | None,
    path_guard: _ManagedPathGuard | None = None,
) -> None:
    if identity is None:
        return
    descriptor = -1
    try:
        if path_guard is not None:
            path_guard.verify_write_target(path)
        current = path.lstat()
        if not stat.S_ISREG(current.st_mode) or _is_reparse_entry(path, current):
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
        if path_guard is not None:
            path_guard.verify_write_target(path)
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
        if not stat.S_ISREG(file_stat.st_mode) or path.is_symlink() or file_stat.st_nlink != 1:
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


def _secure_bootstrap_token_file(
    path: Path,
    path_guard: _ManagedPathGuard | None = None,
) -> bool:
    guard = path_guard or _ManagedPathGuard(path.parent)
    try:
        path = guard._managed_path(path)
        parent, parent_identity = guard.verify_write_target(path)
    except OSError:
        return False
    descriptor = -1
    directory_descriptor = -1
    use_directory_handle = os.name != "nt" and os.open in os.supports_dir_fd
    try:
        flags = _bootstrap_open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        if use_directory_handle:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
            directory_descriptor = os.open(parent, directory_flags)
            opened_parent = os.fstat(directory_descriptor)
            if _DirectoryIdentity(opened_parent.st_dev, opened_parent.st_ino) != parent_identity:
                raise OSError("managed parent changed before bootstrap token creation")
            descriptor = os.open(path.name, flags, 0o600, dir_fd=directory_descriptor)
        else:
            guard.verify_directory(parent, parent_identity)
            descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if directory_descriptor != -1:
            os.close(directory_descriptor)
        try:
            guard.verify_write_target(path)
            existing_stat = path.lstat()
            if (
                not stat.S_ISREG(existing_stat.st_mode)
                or _is_reparse_entry(path, existing_stat)
                or existing_stat.st_nlink != 1
            ):
                return False
            if os.name != "nt":
                os.chmod(path, 0o600, follow_symlinks=False)
        except (OSError, NotImplementedError):
            return False
        return bool(_read_secure_bootstrap_token(path))
    except OSError:
        if directory_descriptor != -1:
            os.close(directory_descriptor)
        return False
    try:
        os.write(descriptor, secrets.token_urlsafe(32).encode("utf-8"))
        os.fsync(descriptor)
    except OSError:
        return False
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if directory_descriptor != -1:
            os.close(directory_descriptor)
    return bool(_read_secure_bootstrap_token(path))


def _canonical_parent_path(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    return absolute.parent / absolute.name


def _canonical_directory_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _nexus_owned_path(data_root: Path, path: Path, label: str) -> Path:
    resolved = _canonical_parent_path(path)
    if resolved == data_root or data_root not in resolved.parents:
        raise ValueError(f"{label} must be inside the Nexus data directory")
    return resolved


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
    data_dir: Path | None = None,
    credentials_path: Path | None = None,
    config_path: Path | None = None,
    bootstrap_token_path: Path | None = None,
    max_upload_bytes: int = 50 * 1024 * 1024,
    max_total_storage_bytes: int = 10 * 1024 * 1024 * 1024,
    min_free_disk_bytes: int = 512 * 1024 * 1024,
    login_rate_limit: int = 5,
    login_rate_window_seconds: float = 60.0,
    transcribe_audio: Callable[[str], dict[str, Any]] | None = None,
    synthesize_speech_audio: Callable[[str, str], bytes] | None = None,
    speech_max_concurrency: int = SPEECH_MAX_CONCURRENCY,
    speech_max_audio_bytes: int = SPEECH_MAX_AUDIO_BYTES,
    cloud_asr_provider: CloudAsrProvider | None = None,
    cloud_asr_limits: CloudAsrLimits | None = None,
) -> web.Application:
    resolved_data_root = _canonical_directory_path(
        Path(data_dir) if data_dir is not None else Path(storage_dir).parent
    )
    path_guard = _ManagedPathGuard(resolved_data_root)
    resolved_storage_path = _canonical_directory_path(Path(storage_dir))
    resolved_storage_path = _nexus_owned_path(
        resolved_data_root, resolved_storage_path, "media directory"
    )
    path_guard.ensure_directory(resolved_storage_path, pin=True)
    resolved_config_path = _nexus_owned_path(
        resolved_data_root,
        Path(config_path or (resolved_data_root / "config.json"))
        ,
        "configuration path",
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
    profiles = _load_saved_profiles(saved_config.get("hermes_profiles"))
    raw_openai_asr = saved_config.get("openai_asr")
    if not isinstance(raw_openai_asr, dict):
        raw_openai_asr = {}
    openai_asr = OpenAiAsrConfig(
        enabled=raw_openai_asr.get("enabled") is True,
        base_url=normalize_openai_base_url(str(raw_openai_asr.get("base_url", ""))) or "",
        api_key=str(raw_openai_asr.get("api_key", "")).strip(),
        model=normalize_openai_model(str(raw_openai_asr.get("model", "whisper-1"))) or "whisper-1",
    )
    app = web.Application(
        middlewares=[security_headers, device_auth],
        client_max_size=max_upload_bytes + 1024 * 1024,
    )
    resolved_credentials_path = _nexus_owned_path(
        resolved_data_root,
        Path(credentials_path or (resolved_data_root / "account.json"))
        ,
        "credentials path",
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
        _write_credentials(
            resolved_credentials_path,
            username,
            password_salt,
            password_hash,
            revision,
            path_guard,
        )
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
        _write_config(
            resolved_config_path,
            upstream_url,
            upstream_token,
            session_secret,
            path_guard=path_guard,
        )
    app[USERNAME_KEY] = username
    app[PASSWORD_KEY] = password
    app[CREDENTIALS_PATH_KEY] = resolved_credentials_path
    app[CONFIG_PATH_KEY] = resolved_config_path
    resolved_bootstrap_token_path = _nexus_owned_path(resolved_data_root, Path(
        bootstrap_token_path or (resolved_config_path.parent / "bootstrap.token")
    ), "bootstrap token path")
    setup_available = not initialized and config_valid and credentials_valid and not config_exists and not credentials_exists
    if setup_available:
        setup_available = _secure_bootstrap_token_file(
            resolved_bootstrap_token_path,
            path_guard,
        )
    app[BOOTSTRAP_TOKEN_PATH_KEY] = resolved_bootstrap_token_path
    app[GATEWAY_CONFIG_KEY] = GatewayConfig(
        initialized=initialized,
        setup_available=setup_available,
        session_secret=session_secret,
        upstream_url=upstream_url.rstrip("/"),
        upstream_token=upstream_token,
        profiles=profiles,
        openai_asr=openai_asr,
    )
    app[AUTH_STATE_KEY] = AuthState(username, password_salt, password_hash, legacy_password, revision)
    app[MANAGED_PATH_GUARD_KEY] = path_guard
    app[HERMES_PROFILE_IDENTITY_CACHE_KEY] = {}
    app[LOGIN_RATE_LIMITER_KEY] = LoginRateLimiter(login_rate_limit, login_rate_window_seconds)
    app[SETUP_LOCK_KEY] = asyncio.Lock()
    app[MEDIA_STORE_KEY] = MediaStore(
        resolved_storage_path,
        max_upload_bytes,
        max_total_storage_bytes,
        min_free_disk_bytes,
        path_guard,
    )
    app[RUN_TRACKER_KEY] = RunTracker(
        resolved_data_root / "run_status.json",
        path_guard,
    )
    app[EXTERNAL_RUN_OBSERVER_KEY] = ExternalRunObserver()
    app[EXTERNAL_RUN_OBSERVERS_KEY] = {}
    # Tests and custom deployments may inject a standalone transcriber. The
    # packaged Gateway never imports Hermes internals; without an injected
    # callback it uses only a capability-advertised original Hermes HTTP API.
    app[TRANSCRIBE_AUDIO_KEY] = transcribe_audio
    app[SYNTHESIZE_SPEECH_KEY] = synthesize_speech_audio
    app[SPEECH_SEMAPHORE_KEY] = asyncio.Semaphore(max(1, speech_max_concurrency))
    app[SPEECH_MAX_AUDIO_BYTES_KEY] = max(1, speech_max_audio_bytes)
    app[EPHEMERAL_CLEANUP_TASKS_KEY] = set()
    configure_cloud_asr(app, cloud_asr_provider, cloud_asr_limits)
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
    app.router.add_get("/api/admin/hermes-config", admin_hermes_config)
    app.router.add_put("/api/admin/hermes-config", update_hermes_config)
    app.router.add_get("/api/admin/asr-config", admin_asr_config)
    app.router.add_put("/api/admin/asr-config", update_asr_config)
    app.router.add_get("/api/hermes/connections", list_hermes_connections)
    app.router.add_get("/api/hermes/personas", list_hermes_profiles)
    app.router.add_get("/api/hermes/profiles", list_hermes_profiles)
    app.router.add_get("/api/admin/files", admin_files)
    app.router.add_get("/api/admin/audio", admin_audio)
    app.router.add_get("/api/asr/capabilities", cloud_asr_capabilities)
    app.router.add_get("/api/asr/stream", cloud_asr_stream)
    app.router.add_post("/api/audio/transcriptions", transcribe_upload)
    app.router.add_post("/api/audio/speech", synthesize_speech)
    app.router.add_post("/api/uploads", upload)
    app.router.add_get("/api/files/{file_id}/metadata", file_metadata)
    app.router.add_get("/api/files/{file_id}", download)
    app.router.add_delete("/api/files/{file_id}", delete_file)
    app.router.add_route("*", "/api/{tail:.*}", proxy)
    app.router.add_route("*", "/v1/{tail:.*}", proxy)
    return app
