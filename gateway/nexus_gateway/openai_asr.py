from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientSession, FormData


@dataclass(frozen=True)
class OpenAiAsrConfig:
    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = "whisper-1"

    @property
    def configured(self) -> bool:
        return bool(self.enabled and self.base_url and self.api_key and self.model)


def normalize_openai_base_url(value: str) -> str | None:
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


def normalize_openai_model(value: str) -> str | None:
    candidate = value.strip()
    if not candidate or len(candidate) > 128 or any(ord(char) < 32 for char in candidate):
        return None
    return candidate


def transcription_url(base_url: str) -> str:
    suffix = "/audio/transcriptions"
    return base_url if base_url.endswith(suffix) else base_url + suffix


async def transcribe_openai_compatible(
    session: ClientSession,
    config: OpenAiAsrConfig,
    *,
    path: str,
    filename: str,
    mime_type: str,
    prompt: str,
) -> dict[str, Any]:
    if not config.configured:
        return {
            "success": False,
            "code": "transcription_unavailable",
            "message": "OpenAI 兼容在线语音识别尚未配置",
            "status": 501,
        }

    form = FormData()
    try:
        with Path(path).open("rb") as handle:
            form.add_field("model", config.model)
            form.add_field("language", "zh")
            form.add_field("prompt", prompt)
            form.add_field(
                "file",
                handle,
                filename=filename,
                content_type=mime_type or "application/octet-stream",
            )
            async with session.post(
                transcription_url(config.base_url),
                headers={"Authorization": f"Bearer {config.api_key}"},
                data=form,
            ) as response:
                if response.status in {401, 403}:
                    return {
                        "success": False,
                        "code": "online_asr_auth_failed",
                        "message": "在线语音识别 API Key 无效或无权访问",
                        "status": 502,
                    }
                if response.status < 200 or response.status >= 300:
                    return {
                        "success": False,
                        "code": "online_asr_failed",
                        "message": "在线语音识别服务转写失败",
                        "status": 503,
                    }
                payload = await response.json(content_type=None)
    except Exception:
        return {
            "success": False,
            "code": "online_asr_unavailable",
            "message": "无法连接在线语音识别服务",
            "status": 503,
        }

    if not isinstance(payload, dict):
        payload = {}
    transcript = str(payload.get("text") or payload.get("transcript") or "").strip()
    if not transcript:
        return {
            "success": False,
            "code": "online_asr_empty",
            "message": "在线语音识别服务没有返回文本",
            "status": 503,
        }
    return {
        "success": True,
        "transcript": transcript,
        "provider": "openai-compatible",
    }
