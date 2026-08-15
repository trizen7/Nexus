import asyncio
from pathlib import Path

import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from nexus_gateway.app import create_app
from nexus_gateway.cloud_asr import (
    CLOUD_ASR_PROTOCOL,
    CloudAsrCapabilities,
    CloudAsrEvent,
    CloudAsrLimits,
    CloudAsrStartConfig,
)
from nexus_gateway.__main__ import main


class FakeCloudAsrSession:
    def __init__(self) -> None:
        self.audio = bytearray()
        self.commits: list[int] = []
        self.close_count = 0
        self.events_queue: asyncio.Queue[CloudAsrEvent | None] = asyncio.Queue()

    async def events(self):
        while True:
            event = await self.events_queue.get()
            if event is None:
                return
            yield event

    async def send_audio(self, pcm: bytes) -> None:
        self.audio.extend(pcm)

    async def commit(self, utterance_id: int) -> None:
        self.commits.append(utterance_id)

    async def close(self) -> None:
        self.close_count += 1


class FakeCloudAsrProvider:
    capabilities = CloudAsrCapabilities()

    def __init__(self, session: FakeCloudAsrSession | None = None) -> None:
        self.session = session or FakeCloudAsrSession()
        self.configs: list[CloudAsrStartConfig] = []

    async def create_session(self, config: CloudAsrStartConfig) -> FakeCloudAsrSession:
        self.configs.append(config)
        return self.session


class FailingCloudAsrProvider(FakeCloudAsrProvider):
    async def create_session(self, config: CloudAsrStartConfig) -> FakeCloudAsrSession:
        raise RuntimeError("provider-key-secret private endpoint transcript")


def make_app(tmp_path: Path, provider=None, limits: CloudAsrLimits | None = None):
    return create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:9",
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=tmp_path / "config.json",
        cloud_asr_provider=provider,
        cloud_asr_limits=limits,
    )


async def login(client: TestClient) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": "nexus", "password": "test-password"},
    )
    assert response.status == 200
    return (await response.json())["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def start_payload() -> dict[str, object]:
    return {
        "type": "start",
        "protocol": CLOUD_ASR_PROTOCOL,
        "language": "zh-CN",
        "sample_rate": 16_000,
        "channels": 1,
        "encoding": "pcm_s16le",
        "frame_millis": 20,
        "punctuation": True,
        "interim_results": True,
    }


@pytest.mark.asyncio
async def test_cloud_asr_is_disabled_by_default_and_requires_authentication(tmp_path: Path):
    async with TestClient(TestServer(make_app(tmp_path))) as client:
        unauthorized = await client.get("/api/asr/capabilities")
        assert unauthorized.status == 401

        token = await login(client)
        capabilities = await client.get("/api/asr/capabilities", headers=auth(token))
        assert capabilities.status == 200
        assert await capabilities.json() == {
            "available": False,
            "protocol": CLOUD_ASR_PROTOCOL,
        }

        unavailable = await client.get("/api/asr/stream", headers=auth(token))
        assert unavailable.status == 501
        body = await unavailable.json()
        assert body["error"]["code"] == "cloud_asr_unavailable"
        assert "Hermes" not in body["error"]["message"]


@pytest.mark.asyncio
async def test_cloud_asr_stream_forwards_pcm_and_normalizes_events(tmp_path: Path):
    session = FakeCloudAsrSession()
    provider = FakeCloudAsrProvider(session)
    async with TestClient(TestServer(make_app(tmp_path, provider))) as client:
        token = await login(client)
        websocket = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await websocket.send_json(start_payload())
        ready = await websocket.receive_json()
        assert ready["type"] == "ready"
        assert ready["protocol"] == CLOUD_ASR_PROTOCOL
        assert ready["session_id"]
        assert provider.configs == [CloudAsrStartConfig("zh-CN", True, True)]

        await websocket.send_bytes(b"\x01\x00" * 320)
        await session.events_queue.put(CloudAsrEvent("speech_start", utterance_id=1))
        await session.events_queue.put(CloudAsrEvent("partial", utterance_id=1, text="你", revision=1))
        await session.events_queue.put(CloudAsrEvent("partial", utterance_id=1, text="旧", revision=1))
        await session.events_queue.put(CloudAsrEvent("partial", utterance_id=1, text="你好", revision=2))
        await websocket.send_json({"type": "commit", "utterance_id": 1})
        await session.events_queue.put(CloudAsrEvent("final", utterance_id=1, text="你好。"))
        await session.events_queue.put(CloudAsrEvent("final", utterance_id=1, text="重复"))
        await session.events_queue.put(CloudAsrEvent("speech_end", utterance_id=1))

        received = [await websocket.receive_json() for _ in range(5)]
        assert received == [
            {"type": "speech_start", "utterance_id": 1},
            {"type": "partial", "utterance_id": 1, "text": "你", "revision": 1},
            {"type": "partial", "utterance_id": 1, "text": "你好", "revision": 2},
            {"type": "final", "utterance_id": 1, "text": "你好。"},
            {"type": "speech_end", "utterance_id": 1},
        ]
        await websocket.send_json({"type": "cancel"})
        await websocket.close()

    assert bytes(session.audio) == b"\x01\x00" * 320
    assert session.commits == [1]
    assert session.close_count == 1


@pytest.mark.asyncio
async def test_cloud_asr_keeps_older_final_after_next_utterance_starts(tmp_path: Path):
    session = FakeCloudAsrSession()
    provider = FakeCloudAsrProvider(session)
    async with TestClient(TestServer(make_app(tmp_path, provider))) as client:
        token = await login(client)
        websocket = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await websocket.send_json(start_payload())
        assert (await websocket.receive_json())["type"] == "ready"
        await session.events_queue.put(CloudAsrEvent("speech_start", utterance_id=1))
        await session.events_queue.put(CloudAsrEvent("speech_end", utterance_id=1))
        await session.events_queue.put(CloudAsrEvent("speech_start", utterance_id=2))
        await session.events_queue.put(CloudAsrEvent("partial", utterance_id=2, text="第二句", revision=1))
        await session.events_queue.put(CloudAsrEvent("final", utterance_id=1, text="第一句。"))
        received = [await websocket.receive_json() for _ in range(5)]
        assert received[-1] == {"type": "final", "utterance_id": 1, "text": "第一句。"}
        await websocket.send_json({"type": "cancel"})
        await websocket.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_message", "expected_code"),
    [
        ({"type": "commit", "utterance_id": 1}, "start_required"),
        ({**start_payload(), "sample_rate": 8_000}, "unsupported_format"),
        ({**start_payload(), "protocol": "unknown"}, "unsupported_protocol"),
    ],
)
async def test_cloud_asr_rejects_invalid_start_messages(
    tmp_path: Path,
    first_message: dict[str, object],
    expected_code: str,
):
    provider = FakeCloudAsrProvider()
    async with TestClient(TestServer(make_app(tmp_path, provider))) as client:
        token = await login(client)
        websocket = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await websocket.send_json(first_message)
        error = await websocket.receive_json()
        assert error["type"] == "error"
        assert error["code"] == expected_code
        await websocket.close()
    assert not provider.configs


@pytest.mark.asyncio
async def test_cloud_asr_rejects_odd_or_excessive_audio_and_closes_provider(tmp_path: Path):
    limits = CloudAsrLimits(max_frame_bytes=8, idle_timeout_seconds=1)
    for frame in (b"\x00", b"\x00" * 10):
        session = FakeCloudAsrSession()
        async with TestClient(TestServer(make_app(tmp_path, FakeCloudAsrProvider(session), limits))) as client:
            token = await login(client)
            websocket = await client.ws_connect("/api/asr/stream", headers=auth(token))
            await websocket.send_json(start_payload())
            assert (await websocket.receive_json())["type"] == "ready"
            await websocket.send_bytes(frame)
            error = await websocket.receive_json()
            assert error["code"] == "invalid_audio_frame"
            await websocket.close()
        assert session.close_count == 1


@pytest.mark.asyncio
async def test_cloud_asr_provider_failure_is_redacted(tmp_path: Path):
    async with TestClient(TestServer(make_app(tmp_path, FailingCloudAsrProvider()))) as client:
        token = await login(client)
        websocket = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await websocket.send_json(start_payload())
        error = await websocket.receive_json()
        serialized = str(error)
        assert error == {
            "type": "error",
            "code": "provider_unavailable",
            "message": "云端语音识别暂不可用",
            "retryable": True,
        }
        assert "provider-key-secret" not in serialized
        assert "private endpoint" not in serialized


@pytest.mark.asyncio
async def test_cloud_asr_concurrency_limit_fails_fast_and_releases_slot(tmp_path: Path):
    limits = CloudAsrLimits(max_concurrency=1, idle_timeout_seconds=2)
    provider = FakeCloudAsrProvider()
    async with TestClient(TestServer(make_app(tmp_path, provider, limits))) as client:
        token = await login(client)
        first = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await first.send_json(start_payload())
        assert (await first.receive_json())["type"] == "ready"

        busy = await client.get("/api/asr/stream", headers=auth(token))
        assert busy.status == 429
        assert (await busy.json())["error"]["code"] == "cloud_asr_busy"

        await first.send_json({"type": "cancel"})
        assert (await first.receive()).type in {WSMsgType.CLOSE, WSMsgType.CLOSED}
        second = await client.ws_connect("/api/asr/stream", headers=auth(token))
        await second.send_json(start_payload())
        assert (await second.receive_json())["type"] == "ready"
        await second.send_json({"type": "cancel"})
        await second.close()


def test_reserved_cloud_asr_provider_fails_closed(monkeypatch, capsys):
    monkeypatch.setenv("NEXUS_CLOUD_ASR_PROVIDER", "unknown")
    monkeypatch.setattr("sys.argv", ["nexus_gateway"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "is not supported by this Gateway build" in output
    assert "unknown" not in output
