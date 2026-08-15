from __future__ import annotations

import asyncio
import inspect
import json
import time
import uuid
from dataclasses import asdict, dataclass
from typing import AsyncIterator, Protocol, runtime_checkable

from aiohttp import WSMsgType, web


CLOUD_ASR_PROTOCOL = "nexus.asr.v1"
CLOUD_ASR_SAMPLE_RATE = 16_000
CLOUD_ASR_CHANNELS = 1
CLOUD_ASR_ENCODING = "pcm_s16le"
CLOUD_ASR_FRAME_MILLIS = 20
CLOUD_ASR_MAX_FRAME_BYTES = 32 * 1024
CLOUD_ASR_MAX_CONTROL_BYTES = 4 * 1024
CLOUD_ASR_MAX_SESSION_BYTES = 1_920_000
CLOUD_ASR_START_TIMEOUT_SECONDS = 5.0
CLOUD_ASR_IDLE_TIMEOUT_SECONDS = 10.0
CLOUD_ASR_MAX_SESSION_SECONDS = 75.0
CLOUD_ASR_FINAL_TIMEOUT_SECONDS = 15.0
CLOUD_ASR_MAX_CONCURRENCY = 2


@dataclass(frozen=True)
class CloudAsrCapabilities:
    supports_partial: bool = True
    supports_punctuation: bool = True

    def public_payload(self, available: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "available": available,
            "protocol": CLOUD_ASR_PROTOCOL,
        }
        if available:
            payload.update({
                "transport": "websocket",
                "sample_rate": CLOUD_ASR_SAMPLE_RATE,
                "channels": CLOUD_ASR_CHANNELS,
                "encoding": CLOUD_ASR_ENCODING,
                "frame_millis": CLOUD_ASR_FRAME_MILLIS,
                "supports_partial": self.supports_partial,
                "supports_punctuation": self.supports_punctuation,
                # Deliberately hides endpoint details and credentials.
                "provider": "configured",
            })
        return payload


@dataclass(frozen=True)
class CloudAsrStartConfig:
    language: str
    punctuation: bool
    interim_results: bool
    sample_rate: int = CLOUD_ASR_SAMPLE_RATE
    channels: int = CLOUD_ASR_CHANNELS
    encoding: str = CLOUD_ASR_ENCODING
    frame_millis: int = CLOUD_ASR_FRAME_MILLIS


@dataclass(frozen=True)
class CloudAsrEvent:
    type: str
    utterance_id: int | None = None
    text: str | None = None
    revision: int | None = None
    code: str | None = None
    retryable: bool | None = None


@runtime_checkable
class CloudAsrSession(Protocol):
    def events(self) -> AsyncIterator[CloudAsrEvent]: ...

    async def send_audio(self, pcm: bytes) -> None: ...

    async def commit(self, utterance_id: int) -> None: ...

    async def close(self) -> None: ...


@runtime_checkable
class CloudAsrProvider(Protocol):
    @property
    def capabilities(self) -> CloudAsrCapabilities: ...

    async def create_session(self, config: CloudAsrStartConfig) -> CloudAsrSession: ...


@dataclass(frozen=True)
class CloudAsrLimits:
    max_frame_bytes: int = CLOUD_ASR_MAX_FRAME_BYTES
    max_control_bytes: int = CLOUD_ASR_MAX_CONTROL_BYTES
    max_session_bytes: int = CLOUD_ASR_MAX_SESSION_BYTES
    start_timeout_seconds: float = CLOUD_ASR_START_TIMEOUT_SECONDS
    idle_timeout_seconds: float = CLOUD_ASR_IDLE_TIMEOUT_SECONDS
    max_session_seconds: float = CLOUD_ASR_MAX_SESSION_SECONDS
    final_timeout_seconds: float = CLOUD_ASR_FINAL_TIMEOUT_SECONDS
    max_concurrency: int = CLOUD_ASR_MAX_CONCURRENCY


CLOUD_ASR_PROVIDER_KEY = web.AppKey("cloud_asr_provider", object)
CLOUD_ASR_LIMITS_KEY = web.AppKey("cloud_asr_limits", CloudAsrLimits)
CLOUD_ASR_SEMAPHORE_KEY = web.AppKey("cloud_asr_semaphore", asyncio.Semaphore)


def configure_cloud_asr(
    app: web.Application,
    provider: CloudAsrProvider | None,
    limits: CloudAsrLimits | None = None,
) -> None:
    resolved = limits or CloudAsrLimits()
    app[CLOUD_ASR_PROVIDER_KEY] = provider
    app[CLOUD_ASR_LIMITS_KEY] = resolved
    app[CLOUD_ASR_SEMAPHORE_KEY] = asyncio.Semaphore(max(1, resolved.max_concurrency))
    if provider is not None and callable(getattr(provider, "close", None)):
        app.on_cleanup.append(_close_cloud_asr_provider)


async def _close_cloud_asr_provider(app: web.Application) -> None:
    provider = app[CLOUD_ASR_PROVIDER_KEY]
    close = getattr(provider, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


async def cloud_asr_capabilities(request: web.Request) -> web.Response:
    provider = request.app[CLOUD_ASR_PROVIDER_KEY]
    capabilities = (
        provider.capabilities
        if isinstance(provider, CloudAsrProvider)
        else CloudAsrCapabilities()
    )
    return web.json_response(capabilities.public_payload(provider is not None))


def _error_payload(code: str, message: str, retryable: bool) -> dict[str, object]:
    return {
        "type": "error",
        "code": code,
        "message": message,
        "retryable": retryable,
    }


def _parse_start_message(message: web.WSMessage, limits: CloudAsrLimits) -> CloudAsrStartConfig:
    if message.type != WSMsgType.TEXT:
        raise ValueError("start_required")
    encoded = message.data.encode("utf-8")
    if len(encoded) > limits.max_control_bytes:
        raise ValueError("control_too_large")
    try:
        payload = json.loads(message.data)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("invalid_json") from error
    if not isinstance(payload, dict) or payload.get("type") != "start":
        raise ValueError("start_required")
    if payload.get("protocol") != CLOUD_ASR_PROTOCOL:
        raise ValueError("unsupported_protocol")
    if payload.get("sample_rate") != CLOUD_ASR_SAMPLE_RATE:
        raise ValueError("unsupported_format")
    if payload.get("channels") != CLOUD_ASR_CHANNELS:
        raise ValueError("unsupported_format")
    if payload.get("encoding") != CLOUD_ASR_ENCODING:
        raise ValueError("unsupported_format")
    if payload.get("frame_millis", CLOUD_ASR_FRAME_MILLIS) != CLOUD_ASR_FRAME_MILLIS:
        raise ValueError("unsupported_format")
    language = payload.get("language", "zh-CN")
    if not isinstance(language, str) or not language or len(language) > 32:
        raise ValueError("invalid_language")
    return CloudAsrStartConfig(
        language=language,
        punctuation=payload.get("punctuation", True) is not False,
        interim_results=payload.get("interim_results", True) is not False,
    )


def _event_payload(event: CloudAsrEvent) -> dict[str, object]:
    if event.type == "error":
        return _error_payload(
            "provider_unavailable",
            "云端语音识别暂不可用",
            True,
        )
    if event.type not in {"speech_start", "partial", "final", "speech_end"}:
        raise ValueError("invalid_provider_event")
    if not isinstance(event.utterance_id, int) or event.utterance_id <= 0:
        raise ValueError("invalid_provider_event")
    payload: dict[str, object] = {
        "type": event.type,
        "utterance_id": event.utterance_id,
    }
    if event.type in {"partial", "final"}:
        text = event.text.strip() if isinstance(event.text, str) else ""
        if len(text) > 8_192:
            raise ValueError("invalid_provider_event")
        payload["text"] = text
    if event.type == "partial":
        if not isinstance(event.revision, int) or event.revision < 0:
            raise ValueError("invalid_provider_event")
        payload["revision"] = event.revision
    return payload


async def _send_ws_error(
    websocket: web.WebSocketResponse,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    if not websocket.closed:
        await websocket.send_json(_error_payload(code, message, retryable))


async def _receive_client_audio(
    websocket: web.WebSocketResponse,
    session: CloudAsrSession,
    limits: CloudAsrLimits,
) -> str:
    total_bytes = 0
    last_commit_id = 0
    while True:
        message = await asyncio.wait_for(websocket.receive(), timeout=limits.idle_timeout_seconds)
        if message.type == WSMsgType.BINARY:
            frame = bytes(message.data)
            if not frame or len(frame) % 2 or len(frame) > limits.max_frame_bytes:
                raise ValueError("invalid_audio_frame")
            total_bytes += len(frame)
            if total_bytes > limits.max_session_bytes:
                raise ValueError("session_audio_limit")
            await session.send_audio(frame)
            continue
        if message.type == WSMsgType.TEXT:
            if len(message.data.encode("utf-8")) > limits.max_control_bytes:
                raise ValueError("control_too_large")
            try:
                payload = json.loads(message.data)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                raise ValueError("invalid_json") from error
            if not isinstance(payload, dict):
                raise ValueError("invalid_control")
            message_type = payload.get("type")
            if message_type == "commit":
                utterance_id = payload.get("utterance_id")
                if not isinstance(utterance_id, int) or utterance_id <= last_commit_id:
                    raise ValueError("invalid_utterance_id")
                last_commit_id = utterance_id
                await session.commit(utterance_id)
                continue
            if message_type in {"cancel", "stop"}:
                return str(message_type)
            raise ValueError("invalid_control")
        if message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.CLOSING}:
            return "disconnected"
        if message.type == WSMsgType.ERROR:
            return "disconnected"
        raise ValueError("invalid_frame_type")


async def _send_provider_events(
    websocket: web.WebSocketResponse,
    session: CloudAsrSession,
) -> None:
    revisions: dict[int, int] = {}
    finalized: set[int] = set()
    ended: set[int] = set()
    highest_utterance_id = 0
    async for event in session.events():
        payload = _event_payload(event)
        if event.type == "error":
            await websocket.send_json(payload)
            return
        utterance_id = int(payload["utterance_id"])
        if event.type in {"speech_start", "partial"} and utterance_id < highest_utterance_id:
            continue
        if event.type in {"speech_start", "partial"}:
            highest_utterance_id = max(highest_utterance_id, utterance_id)
        if event.type == "partial":
            if utterance_id in finalized:
                continue
            revision = int(payload["revision"])
            if revision <= revisions.get(utterance_id, -1):
                continue
            revisions[utterance_id] = revision
        elif event.type == "final":
            if utterance_id in finalized:
                continue
            finalized.add(utterance_id)
        elif event.type == "speech_end":
            if utterance_id in ended:
                continue
            ended.add(utterance_id)
        await websocket.send_json(payload)


_PROTOCOL_MESSAGES = {
    "start_required": "必须先发送 start 消息",
    "control_too_large": "语音控制消息过大",
    "invalid_json": "语音控制消息格式无效",
    "unsupported_protocol": "客户端语音协议版本不受支持",
    "unsupported_format": "仅支持 16 kHz 单声道 PCM16 音频",
    "invalid_language": "语音语言设置无效",
    "invalid_audio_frame": "语音音频帧格式无效",
    "session_audio_limit": "本次语音会话已达到时长上限",
    "invalid_utterance_id": "语句编号无效",
    "invalid_control": "语音控制消息无效",
    "invalid_frame_type": "语音消息类型无效",
}


async def cloud_asr_stream(request: web.Request) -> web.StreamResponse:
    provider = request.app[CLOUD_ASR_PROVIDER_KEY]
    if provider is None:
        return web.json_response(
            {"error": {
                "code": "cloud_asr_unavailable",
                "message": "云端实时语音识别尚未配置",
            }},
            status=501,
        )
    limits = request.app[CLOUD_ASR_LIMITS_KEY]
    semaphore = request.app[CLOUD_ASR_SEMAPHORE_KEY]
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=0.05)
    except TimeoutError:
        return web.json_response(
            {"error": {"code": "cloud_asr_busy", "message": "云端语音识别繁忙，请稍后重试"}},
            status=429,
        )

    websocket = web.WebSocketResponse(
        max_msg_size=max(limits.max_frame_bytes, limits.max_control_bytes),
        heartbeat=20.0,
        compress=False,
    )
    websocket.headers["Cache-Control"] = "no-store"
    websocket.headers["X-Content-Type-Options"] = "nosniff"
    session: CloudAsrSession | None = None
    incoming: asyncio.Task[str] | None = None
    outgoing: asyncio.Task[None] | None = None
    try:
        await websocket.prepare(request)
        try:
            first = await asyncio.wait_for(websocket.receive(), timeout=limits.start_timeout_seconds)
            config = _parse_start_message(first, limits)
        except TimeoutError:
            await _send_ws_error(websocket, "start_timeout", "等待语音会话启动超时", True)
            return websocket
        except ValueError as error:
            code = str(error)
            await _send_ws_error(websocket, code, _PROTOCOL_MESSAGES.get(code, "语音协议错误"), False)
            return websocket

        # Provider exceptions are intentionally mapped to fixed messages. Never expose
        # provider response bodies, endpoints, credentials, or exception strings.
        try:
            session = await provider.create_session(config)
        except Exception:
            await _send_ws_error(websocket, "provider_unavailable", "云端语音识别暂不可用", True)
            return websocket

        await websocket.send_json({
            "type": "ready",
            "protocol": CLOUD_ASR_PROTOCOL,
            "session_id": str(uuid.uuid4()),
        })
        incoming = asyncio.create_task(_receive_client_audio(websocket, session, limits))
        outgoing = asyncio.create_task(_send_provider_events(websocket, session))
        started_at = time.monotonic()
        done, _ = await asyncio.wait(
            {incoming, outgoing},
            timeout=limits.max_session_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            await _send_ws_error(websocket, "session_timeout", "本次语音会话已达到时长上限", True)
        elif incoming in done:
            try:
                reason = incoming.result()
            except TimeoutError:
                await _send_ws_error(websocket, "idle_timeout", "语音连接等待音频超时", True)
            except ValueError as error:
                code = str(error)
                await _send_ws_error(websocket, code, _PROTOCOL_MESSAGES.get(code, "语音协议错误"), False)
            except Exception:
                await _send_ws_error(websocket, "provider_unavailable", "云端语音识别暂不可用", True)
            else:
                if reason == "stop" and outgoing is not None:
                    remaining = max(0.0, limits.max_session_seconds - (time.monotonic() - started_at))
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(outgoing),
                            timeout=min(limits.final_timeout_seconds, remaining),
                        )
                    except TimeoutError:
                        await _send_ws_error(websocket, "final_timeout", "等待最终识别结果超时", True)
                if reason not in {"disconnected", "cancel"} and not websocket.closed:
                    await websocket.send_json({"type": "closed"})
        else:
            try:
                outgoing.result()
            except Exception:
                await _send_ws_error(websocket, "provider_unavailable", "云端语音识别暂不可用", True)
    finally:
        for task in (incoming, outgoing):
            if task is not None and not task.done():
                task.cancel()
        for task in (incoming, outgoing):
            if task is not None:
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
        semaphore.release()
        if websocket.prepared and not websocket.closed:
            await websocket.close()
    return websocket
