import json
import wave
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from nexus_gateway.app import GATEWAY_CONFIG_KEY, create_app
from nexus_gateway.openai_asr import (
    OpenAiAsrConfig,
    normalize_openai_base_url,
    transcribe_openai_compatible,
    transcription_url,
)


def _wav() -> bytes:
    from io import BytesIO

    output = BytesIO()
    with wave.open(output, "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16_000)
        target.writeframes(b"\x00\x00" * 8_000)
    return output.getvalue()


def test_openai_base_url_and_transcription_path_are_normalized():
    assert normalize_openai_base_url("https://api.example.test/v1/") == "https://api.example.test/v1"
    assert normalize_openai_base_url("https://user:secret@example.test/v1") is None
    assert normalize_openai_base_url("file:///tmp/asr") is None
    assert transcription_url("https://api.example.test/v1") == (
        "https://api.example.test/v1/audio/transcriptions"
    )
    assert transcription_url("https://api.example.test/v1/audio/transcriptions") == (
        "https://api.example.test/v1/audio/transcriptions"
    )


@pytest.mark.asyncio
async def test_openai_transcription_sends_minimal_compatible_multipart(tmp_path: Path):
    observed = {}

    async def transcribe(request: web.Request):
        observed["authorization"] = request.headers.get("Authorization")
        reader = await request.multipart()
        async for field in reader:
            if field.filename:
                observed[field.name] = {"filename": field.filename, "content": await field.read()}
            else:
                observed[field.name] = await field.text()
        return web.json_response({"text": "测试完成。"})

    app = web.Application()
    app.router.add_post("/v1/audio/transcriptions", transcribe)
    audio = tmp_path / "voice.wav"
    audio.write_bytes(_wav())
    async with TestClient(TestServer(app)) as server:
        result = await transcribe_openai_compatible(
            server.session,
            OpenAiAsrConfig(True, str(server.make_url("/v1")).rstrip("/"), "secret-key", "whisper-test"),
            path=str(audio),
            filename=audio.name,
            mime_type="audio/wav",
            prompt="普通话",
        )

    assert result == {"success": True, "transcript": "测试完成。", "provider": "openai-compatible"}
    assert observed["authorization"] == "Bearer secret-key"
    assert observed["model"] == "whisper-test"
    assert observed["language"] == "zh"
    assert observed["prompt"] == "普通话"
    assert observed["file"]["filename"] == "voice.wav"
    assert observed["file"]["content"] == audio.read_bytes()


@pytest.mark.asyncio
async def test_admin_asr_config_never_returns_key_and_persists_it(tmp_path: Path):
    config_path = tmp_path / "config.json"
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:9",
        upstream_token="hermes-secret",
        data_dir=tmp_path,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=config_path,
    )
    async with TestClient(TestServer(app)) as client:
        login = await client.post("/api/auth/login", json={"username": "nexus", "password": "test-password"})
        token = (await login.json())["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        changed = await client.put(
            "/api/admin/asr-config",
            headers=headers,
            json={
                "enabled": True,
                "base_url": "https://api.example.test/v1",
                "api_key": "online-secret",
                "model": "whisper-test",
                "current_password": "test-password",
            },
        )
        assert changed.status == 200
        fetched = await client.get("/api/admin/asr-config", headers=headers)
        raw = await fetched.text()
        assert "online-secret" not in raw
        assert await fetched.json() == {
            "enabled": True,
            "base_url": "https://api.example.test/v1",
            "model": "whisper-test",
            "key_configured": True,
        }

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["openai_asr"]["api_key"] == "online-secret"
    assert app[GATEWAY_CONFIG_KEY].openai_asr.configured
