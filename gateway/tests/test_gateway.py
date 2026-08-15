import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from nexus_gateway.app import (
    CONFIG_PATH_KEY,
    CREDENTIALS_PATH_KEY,
    EPHEMERAL_CLEANUP_TASKS_KEY,
    EXTERNAL_RUN_OBSERVER_KEY,
    GATEWAY_CONFIG_KEY,
    MEDIA_STORE_KEY,
    MOBILE_CLIENT_CONTEXT_MARKER,
    MOBILE_CLIENT_SYSTEM_MESSAGE,
    RUN_TRACKER_KEY,
    SETUP_LOCK_KEY,
    SYNTHESIZE_SPEECH_KEY,
    TRANSCRIBE_AUDIO_KEY,
    MediaStore,
    RunTracker,
    StoredFile,
    _hermes_unavailable_message,
    create_app,
    security_headers,
)


@pytest_asyncio.fixture
async def upstream_client():
    captured_chat: dict = {}
    chat_requests: list[dict] = []
    captured_model_lock: dict = {}
    captured_completion: dict = {}
    captured_completion_headers: dict[str, str] = {}
    release_chat = asyncio.Event()
    chat_started = asyncio.Event()
    health_state = {
        "status": 200,
        "payload": {"status": "ok", "platform": "hermes-agent", "version": "test"},
    }
    detailed_health_state = {
        "status": 200,
        "payload": {"status": "ok", "gateway_busy": False, "active_agents": 0},
    }
    models_state = {
        "status": 200,
        "payload": {"object": "list", "data": [{"id": "test-model"}]},
    }
    model_options_state = {
        "status": 200,
        "release": None,
        "requests": 0,
        "timeout_requests": 0,
        "payload": {
            "providers": [{
                "slug": "openai-codex",
                "name": "OpenAI Codex",
                "source": "built-in",
                "authenticated": True,
                "models": ["gpt-5.6"],
            }],
            "model": "gpt-5.6",
            "provider": "openai-codex",
        },
    }
    expected_auth = {"token": "upstream-secret"}
    session_state = {
        "status": 200,
        "rows": [
            {
                "id": "session-1",
                "title": None,
                "source": "api_server",
                "message_count": 0,
                "last_active": 1.0,
            }
        ],
    }
    chat_state = {
        "status": 200,
        "payload": None,
        "stream": None,
        "json_payload": None,
    }
    model_lock_state = {"release": None}
    capabilities_state = {
        "status": 404,
        "payload": {},
    }
    audio_transcription_state = {
        "status": 200,
        "payload": {"text": "HTTP 语音转写", "provider": "test-http"},
    }
    audio_requests: list[dict[str, object]] = []
    probe_counts = {"health_detailed": 0, "sessions": 0}

    async def health(_request):
        return web.json_response(health_state["payload"], status=int(health_state["status"]))

    def authorized(request) -> bool:
        return request.headers.get("Authorization") == f"Bearer {expected_auth['token']}"

    async def detailed_health(request):
        probe_counts["health_detailed"] += 1
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        return web.json_response(
            detailed_health_state["payload"],
            status=int(detailed_health_state["status"]),
        )

    async def models(request):
        probe_counts["models"] = probe_counts.get("models", 0) + 1
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        return web.json_response(models_state["payload"], status=int(models_state["status"]))

    async def model_options(request):
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        model_options_state["requests"] += 1
        if model_options_state["timeout_requests"] > 0:
            model_options_state["timeout_requests"] -= 1
            await asyncio.Event().wait()
        release = model_options_state.get("release")
        if isinstance(release, asyncio.Event):
            await release.wait()
        return web.json_response(model_options_state["payload"], status=int(model_options_state["status"]))

    async def sessions(request):
        probe_counts["sessions"] += 1
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        payload = session_state.get("payload")
        if not isinstance(payload, dict):
            payload = {"object": "list", "data": session_state["rows"]}
        return web.json_response(payload, status=int(session_state["status"]))

    async def create_session(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        return web.json_response({"session": {"id": "session-new", "title": None, "source": "api_server", "message_count": 0}})

    async def update_session(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        body = await request.json()
        return web.json_response({"session": {"id": request.match_info["session_id"], "title": body["title"], "source": "api_server", "message_count": 0}})

    async def delete_session(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        return web.json_response({"deleted": True})

    async def chat(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        captured_chat.clear()
        captured_chat.update(await request.json())
        chat_requests.append(dict(captured_chat))
        if chat_state.get("json_payload") is not None:
            return web.json_response(
                chat_state["json_payload"],
                status=int(chat_state["status"]),
                headers={"X-Upstream-Contract": "preserved"},
            )
        if int(chat_state["status"]) != 200:
            return web.json_response(
                chat_state.get("payload") or {"error": "upstream rejected request"},
                status=int(chat_state["status"]),
            )
        if chat_state.get("stream") is not None:
            return web.Response(
                text=str(chat_state["stream"]),
                content_type="text/event-stream",
            )
        if captured_chat.get("message") == "slow-run":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"event: run.started\ndata: {}\n\n")
            chat_started.set()
            await release_chat.wait()
            await response.write(b"event: run.completed\ndata: {}\n\n")
            return response
        if captured_model_lock:
            runtime = {
                "provider": captured_model_lock.get("provider", ""),
                "model": captured_model_lock.get("model", ""),
                "route_source": "session_model_lock",
                "model_lock": "confirmed",
                "requested": {
                    "provider": captured_model_lock.get("provider", ""),
                    "model": captured_model_lock.get("model", ""),
                },
            }
            return web.Response(
                text=(
                    f"event: run.started\ndata: {json.dumps({'runtime': runtime})}\n\n"
                    'event: assistant.delta\ndata: {"delta":"\u6a21\u578b\u56de\u590d"}\n\n'
                    f"event: run.completed\ndata: {json.dumps({'completed': True, 'runtime': runtime})}\n\n"
                    "event: done\ndata: {}\n\n"
                ),
                content_type="text/event-stream",
            )
        return web.Response(
            text=(
                "event: assistant.delta\ndata: {\"delta\":\"收到\"}\n\n"
                "event: run.completed\ndata: {}\n\n"
                "event: done\ndata: {}\n\n"
            ),
            content_type="text/event-stream",
        )

    async def session_model_lock(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        captured_model_lock.clear()
        captured_model_lock.update(await request.json())
        release = model_lock_state.get("release")
        if isinstance(release, asyncio.Event):
            await release.wait()
        return web.json_response({
            "object": "hermes.session.model_lock",
            "session_id": request.match_info["session_id"],
            "runtime": {
                "provider": captured_model_lock.get("provider", ""),
                "model": captured_model_lock.get("model", ""),
                "model_lock": "accepted",
            },
        })

    async def chat_completions(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        captured_completion.clear()
        captured_completion.update(await request.json())
        captured_completion_headers.clear()
        captured_completion_headers.update(dict(request.headers))
        return web.Response(
            text=(
                'data: {"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n\n'
                'event: hermes.tool.progress\n'
                'data: {"tool":"search","toolCallId":"tool-1","status":"running"}\n\n'
                'event: hermes.tool.progress\n'
                'data: {"tool":"search","toolCallId":"tool-1","status":"completed"}\n\n'
                'data: {"choices":[{"index":0,"delta":{"content":"\u6a21\u578b\u56de\u590d"},"finish_reason":null}]}\n\n'
                'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                'data: [DONE]\n\n'
            ),
            content_type="text/event-stream",
        )

    async def capabilities(request):
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        return web.json_response(
            capabilities_state["payload"],
            status=int(capabilities_state["status"]),
        )

    async def audio_transcriptions(request):
        if not authorized(request):
            return web.json_response({"detail": "invalid upstream credential"}, status=401)
        reader = await request.multipart()
        uploaded = b""
        filename = ""
        fields = {}
        async for field in reader:
            if field.name == "file":
                filename = field.filename or ""
                uploaded = await field.read(decode=False)
            elif field.name:
                fields[field.name] = await field.text()
        audio_requests.append({
            "authorization": request.headers.get("Authorization", ""),
            "filename": filename,
            "content": uploaded,
            "fields": fields,
        })
        return web.json_response(
            audio_transcription_state["payload"],
            status=int(audio_transcription_state["status"]),
        )

    async def messages(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        data = [
            {"id": "internal", "role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] hidden"},
            {"id": "mobile-context", "role": "system", "content": f"{MOBILE_CLIENT_CONTEXT_MARKER}\nhidden"},
            {"id": "m1", "role": "user", "content": [
                {"type": "text", "text": "看看"},
                {"type": "text", "text": "附件：photo.png\n类型：image/png\n大小：5 字节\n服务器路径：C:/private/photo.png"},
                {"type": "text", "text": "[screenshot]"},
            ]},
            {"id": "tool-1", "role": "tool", "content": "internal tool output"},
            {"id": "m2", "role": "assistant", "content": "看到了"},
        ]
        if "limit" in request.query:
            limit = int(request.query["limit"])
            offset = int(request.query.get("offset", 0))
            total = len(data)
            end = max(0, total - offset)
            start = max(0, end - limit)
            data = data[start:end]
            pagination = {"total": total, "offset": offset, "limit": limit, "has_more": start > 0}
        else:
            pagination = None
        payload = {"object": "list", "data": data}
        if pagination is not None:
            payload["pagination"] = pagination
        return web.json_response(payload)


    profile_captured_chat: dict = {}
    profile_requests: list[tuple[str, str]] = []

    def profile_authorized(request) -> bool:
        return request.headers.get("Authorization") == "Bearer profile-secret"

    async def profile_health(_request):
        return web.json_response({"status": "ok", "platform": "hermes-agent", "version": "profile-test"})

    async def profile_detailed_health(request):
        if not profile_authorized(request):
            return web.json_response({"detail": "invalid profile credential"}, status=401)
        return web.json_response({"status": "ok", "gateway_busy": False, "active_agents": 0})

    async def profile_models(request):
        if not profile_authorized(request):
            return web.json_response({"detail": "invalid profile credential"}, status=401)
        return web.json_response({"object": "list", "data": [{"id": "work", "parent": None}]})

    async def profile_sessions(request):
        profile_requests.append((request.method, request.path))
        if not profile_authorized(request):
            return web.json_response({"detail": "invalid profile credential"}, status=401)
        return web.json_response({
            "object": "list",
            "data": [{
                "id": "work-session",
                "title": "Work",
                "source": "api_server",
                "message_count": 0,
                "last_active": 2.0,
            }],
        })

    async def profile_chat(request):
        profile_requests.append((request.method, request.path))
        if not profile_authorized(request):
            return web.json_response({"detail": "invalid profile credential"}, status=401)
        profile_captured_chat.clear()
        profile_captured_chat.update(await request.json())
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(b'event: assistant.delta\ndata: {"delta":"work"}\n\n')
        await response.write(b'event: run.completed\ndata: {"completed":true}\n\n')
        await response.write_eof()
        return response

    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/health/detailed", detailed_health)
    app.router.add_get("/v1/models", models)
    app.router.add_get("/api/model/options", model_options)
    app.router.add_get("/v1/capabilities", capabilities)
    app.router.add_post("/v1/audio/transcriptions", audio_transcriptions)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_patch("/api/sessions/{session_id}", update_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_get("/api/sessions/{session_id}/messages", messages)
    app.router.add_post("/api/sessions/{session_id}/chat", chat)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", chat)
    app.router.add_post("/api/sessions/{session_id}/model", session_model_lock)
    app.router.add_post("/v1/chat/completions", chat_completions)
    app.router.add_get("/profile-a/health", profile_health)
    app.router.add_get("/profile-a/health/detailed", profile_detailed_health)
    app.router.add_get("/profile-a/v1/models", profile_models)
    app.router.add_get("/profile-a/api/sessions", profile_sessions)
    app.router.add_post("/profile-a/api/sessions/{session_id}/chat/stream", profile_chat)
    server = TestServer(app)
    client = TestClient(server)
    client.captured_chat = captured_chat
    client.chat_requests = chat_requests
    client.captured_model_lock = captured_model_lock
    client.captured_completion = captured_completion
    client.captured_completion_headers = captured_completion_headers
    client.release_chat = release_chat
    client.chat_started = chat_started
    client.health_state = health_state
    client.detailed_health_state = detailed_health_state
    client.models_state = models_state
    client.model_options_state = model_options_state
    client.model_lock_state = model_lock_state
    client.expected_auth = expected_auth
    client.session_state = session_state
    client.chat_state = chat_state
    client.capabilities_state = capabilities_state
    client.audio_transcription_state = audio_transcription_state
    client.audio_requests = audio_requests
    client.probe_counts = probe_counts
    client.profile_captured_chat = profile_captured_chat
    client.profile_requests = profile_requests
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def gateway_client(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        max_upload_bytes=1024 * 1024,
        transcribe_audio=lambda _path: {"success": True, "transcript": "测试语音", "provider": "test"},
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def failing_transcription_gateway_client(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        max_upload_bytes=1024 * 1024,
        transcribe_audio=lambda _path: {
            "success": False,
            "code": "transcription_failed",
            "message": "语音转写失败",
        },
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def speech_gateway_client(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        max_upload_bytes=1024 * 1024,
        transcribe_audio=lambda _path: {"success": True, "transcript": "测试语音", "provider": "test"},
    )
    app[SYNTHESIZE_SPEECH_KEY] = lambda text, voice: f"{voice}:{text}".encode("utf-8")
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


def create_http_transcription_gateway(tmp_path: Path, upstream_client: TestClient):
    return create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=tmp_path / "config.json",
        max_upload_bytes=1024 * 1024,
        transcribe_audio=None,
    )


async def login(client: TestClient) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"username": "nexus", "password": "test-password"},
    )
    assert response.status == 200
    return (await response.json())["access_token"]


@pytest.mark.asyncio
async def test_uninitialized_gateway_starts_in_setup_mode(tmp_path: Path):
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        status = await client.get("/api/setup/status")
        assert status.status == 200
        assert await status.json() == {"initialized": False}

        login_response = await client.post(
            "/api/auth/login",
            json={"username": "nexus", "password": "password"},
        )
        assert login_response.status == 503
        assert (await login_response.json())["error"]["code"] == "setup_required"

        protected = await client.get("/api/sessions")
        assert protected.status == 503
        assert (await protected.json())["error"]["code"] == "setup_required"


@pytest.mark.asyncio
async def test_setup_requires_one_time_bootstrap_token(tmp_path: Path, upstream_client: TestClient):
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    token = token_path.read_text(encoding="utf-8").strip()
    assert len(token) >= 32

    async with TestClient(TestServer(app)) as client:
        payload = {
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": str(upstream_client.make_url("/")).rstrip("/"),
            "hermes_api_token": "upstream-secret",
        }
        denied = await client.post("/api/setup", json=payload)
        assert denied.status == 403
        assert (await denied.json())["error"]["code"] == "invalid_bootstrap_token"

        payload["bootstrap_token"] = token
        created = await client.post("/api/setup", json=payload)
        assert created.status == 201
        assert not token_path.exists()


@pytest.mark.asyncio
async def test_concurrent_setup_with_same_token_commits_exactly_one_configuration(
    tmp_path: Path, upstream_client: TestClient,
):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    bootstrap_token = token_path.read_text(encoding="utf-8").strip()
    assert isinstance(app[SETUP_LOCK_KEY], asyncio.Lock)
    base_url = str(upstream_client.make_url("/")).rstrip("/")
    payloads = [
        {
            "username": "first-admin",
            "password": "first-password",
            "hermes_api_url": base_url,
            "hermes_api_token": "first-upstream-key",
            "bootstrap_token": bootstrap_token,
        },
        {
            "username": "second-admin",
            "password": "second-password",
            "hermes_api_url": base_url,
            "hermes_api_token": "second-upstream-key",
            "bootstrap_token": bootstrap_token,
        },
    ]

    async with TestClient(TestServer(app)) as client:
        responses = await asyncio.gather(*(
            client.post("/api/setup", json=payload) for payload in payloads
        ))
        statuses = [response.status for response in responses]
        assert statuses.count(201) == 1
        assert sum(status in {409, 503} for status in statuses) == 1

    winner = payloads[statuses.index(201)]
    saved_config = json.loads(config_path.read_text(encoding="utf-8"))
    saved_account = json.loads(account_path.read_text(encoding="utf-8"))
    assert saved_config["hermes_api_token"] == winner["hermes_api_token"]
    assert saved_account["username"] == winner["username"]


@pytest.mark.asyncio
async def test_setup_rolls_back_when_account_commit_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    token = token_path.read_text(encoding="utf-8").strip()
    original_write = gateway_app._secure_exclusive_write

    def fail_account_commit(path, content, path_guard=None):
        if path == account_path:
            raise OSError("injected account commit failure")
        return original_write(path, content, path_guard)

    monkeypatch.setattr(gateway_app, "_secure_exclusive_write", fail_account_commit)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token,
        })

    assert response.status == 502
    assert not config_path.exists()
    assert not account_path.exists()
    assert token_path.read_text(encoding="utf-8").strip() == token
    assert app[GATEWAY_CONFIG_KEY].initialized is False
    assert app[GATEWAY_CONFIG_KEY].setup_available is True


@pytest.mark.asyncio
async def test_setup_rolls_back_when_bootstrap_token_cannot_be_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    token = token_path.read_text(encoding="utf-8").strip()
    original_unlink = Path.unlink

    def fail_token_unlink(path, *args, **kwargs):
        if path == token_path:
            raise PermissionError("injected token unlink failure")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_token_unlink)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token,
        })

    assert response.status == 502
    assert not config_path.exists()
    assert not account_path.exists()
    assert token_path.read_text(encoding="utf-8").strip() == token
    assert app[GATEWAY_CONFIG_KEY].initialized is False
    assert app[GATEWAY_CONFIG_KEY].setup_available is True


@pytest.mark.asyncio
async def test_setup_rollback_does_not_delete_replaced_foreign_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    token = token_path.read_text(encoding="utf-8").strip()
    original_write = gateway_app._secure_exclusive_write

    def replace_config_before_account_failure(path, content, path_guard=None):
        if path == account_path:
            config_path.unlink()
            config_path.write_text("foreign-config", encoding="utf-8")
            raise OSError("injected account commit failure")
        return original_write(path, content, path_guard)

    monkeypatch.setattr(gateway_app, "_secure_exclusive_write", replace_config_before_account_failure)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token,
        })

    assert response.status == 502
    assert config_path.read_text(encoding="utf-8") == "foreign-config"
    assert not account_path.exists()
    assert token_path.exists()


def test_setup_rollback_preserves_file_when_owned_contents_are_replaced(tmp_path: Path):
    import nexus_gateway.app as gateway_app

    config_path = tmp_path / "config.json"
    identity = gateway_app._secure_exclusive_write(config_path, "owned-config")
    config_path.write_text("foreign-config", encoding="utf-8")

    gateway_app._unlink_owned_file(config_path, identity)

    assert config_path.read_text(encoding="utf-8") == "foreign-config"


def test_existing_config_and_account_are_read_from_verified_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    config_path.write_text(json.dumps({
        "hermes_api_url": "http://example.test",
        "hermes_api_token": "secret",
        "session_secret": "s" * 32,
    }), encoding="utf-8")
    account_path.write_text(json.dumps({
        "username": "admin",
        "password": "legacy-password",
        "revision": 1,
    }), encoding="utf-8")
    original_read_text = Path.read_text

    def reject_unverified_read(path, *args, **kwargs):
        if path in {config_path, account_path}:
            raise AssertionError("configuration must be read from its verified descriptor")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_unverified_read)
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert app[GATEWAY_CONFIG_KEY].initialized is True


@pytest.mark.asyncio
async def test_two_app_instances_share_one_bootstrap_claim(
    tmp_path: Path,
):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    kwargs = dict(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    first_app = create_app(**kwargs)
    second_app = create_app(**kwargs)
    token = token_path.read_text(encoding="utf-8").strip()
    payload = {
        "username": "admin",
        "password": "strong-password",
        "hermes_api_url": "http://127.0.0.1:9",
        "hermes_api_token": "upstream-key",
        "bootstrap_token": token,
    }

    async with TestClient(TestServer(first_app)) as first, TestClient(TestServer(second_app)) as second:
        responses = await asyncio.gather(
            first.post("/api/setup", json=payload),
            second.post("/api/setup", json=payload),
        )

    assert sum(response.status == 201 for response in responses) == 1
    assert config_path.exists()
    assert account_path.exists()
    assert not token_path.exists()


@pytest.mark.asyncio
async def test_setup_writes_execute_while_application_setup_lock_is_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    original_write = gateway_app._secure_exclusive_write
    observed = []

    def checked_write(*args, **kwargs):
        observed.append(app[SETUP_LOCK_KEY].locked())
        return original_write(*args, **kwargs)

    monkeypatch.setattr(gateway_app, "_secure_exclusive_write", checked_write)
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token_path.read_text(encoding="utf-8").strip(),
        })
    assert response.status == 201
    assert observed == [True, True]


@pytest.mark.asyncio
async def test_bootstrap_token_directory_fails_closed_without_blocking(tmp_path: Path):
    token_path = tmp_path / "bootstrap.token"
    token_path.mkdir()
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        status = await asyncio.wait_for(client.get("/api/setup/status"), timeout=1)
        assert await status.json() == {"initialized": False, "setup_available": False}
        response = await client.post("/api/setup", json={})
        assert response.status == 503
        assert (await response.json())["error"]["code"] == "configuration_error"


@pytest.mark.skipif(os.name == "nt", reason="POSIX special files are unavailable on Windows")
@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_bootstrap_token_rejects_posix_non_regular_files_without_blocking(tmp_path: Path, kind: str):
    token_path = tmp_path / "bootstrap.token"
    if kind == "symlink":
        target = tmp_path / "target.token"
        target.write_text("do-not-follow", encoding="utf-8")
        token_path.symlink_to(target)
    else:
        os.mkfifo(token_path)

    started = time.monotonic()
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    assert time.monotonic() - started < 1
    assert app[GATEWAY_CONFIG_KEY].setup_available is False


def test_bootstrap_token_creation_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    token_path = tmp_path / "bootstrap.token"
    generated = iter(("A" * 32, "B" * 32))
    token_calls = 0
    calls_lock = threading.Lock()

    def coordinated_token(_length: int) -> str:
        nonlocal token_calls
        token = next(generated)
        with calls_lock:
            token_calls += 1
        if token.startswith("A"):
            time.sleep(0.1)
        return token

    monkeypatch.setattr("nexus_gateway.app.secrets.token_urlsafe", coordinated_token)

    def build_app(storage_name: str) -> None:
        create_app(
            username=None,
            password=None,
            session_secret=None,
            upstream_url=None,
            upstream_token=None,
            storage_dir=tmp_path / storage_name,
            credentials_path=tmp_path / f"{storage_name}.account.json",
            config_path=tmp_path / f"{storage_name}.config.json",
            bootstrap_token_path=token_path,
            transcribe_audio=lambda _path: {"success": False, "transcript": ""},
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(build_app, name) for name in ("one", "two")]
        for future in futures:
            future.result()

    assert token_calls == 1
    assert token_path.read_text(encoding="utf-8") in {"A" * 32, "B" * 32}


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable on Windows")
def test_bootstrap_token_is_created_with_owner_only_permissions(tmp_path: Path):
    token_path = tmp_path / "bootstrap.token"

    create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable on Windows")
def test_existing_bootstrap_token_permissions_are_tightened(tmp_path: Path):
    token_path = tmp_path / "bootstrap.token"
    token_path.write_text("existing-token", encoding="utf-8")
    token_path.chmod(0o666)

    create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert token_path.read_text(encoding="utf-8") == "existing-token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable on Windows")
def test_existing_bootstrap_token_fails_closed_when_permissions_cannot_be_tightened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    token_path = tmp_path / "bootstrap.token"
    token_path.write_text("existing-token", encoding="utf-8")
    token_path.chmod(0o666)
    original_chmod = gateway_app.os.chmod

    def denied_chmod(path, mode, *, dir_fd=None, follow_symlinks=True):
        if Path(path) == token_path:
            raise PermissionError("permission change denied")
        return original_chmod(path, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(gateway_app.os, "chmod", denied_chmod)
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        config_path=tmp_path / "config.json",
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert app[GATEWAY_CONFIG_KEY].setup_available is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks are unavailable on Windows")
@pytest.mark.parametrize("file_name", ["config.json", "account.json"])
def test_existing_config_and_account_symlinks_fail_closed(tmp_path: Path, file_name: str):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    config_content = json.dumps({
        "hermes_api_url": "http://example.test",
        "hermes_api_token": "secret",
        "session_secret": "s" * 32,
    })
    account_content = json.dumps({
        "username": "admin",
        "password": "legacy-password",
        "revision": 1,
    })
    config_path.write_text(config_content, encoding="utf-8")
    account_path.write_text(account_content, encoding="utf-8")
    link_path = tmp_path / file_name
    target_path = tmp_path / f"real-{file_name}"
    link_path.replace(target_path)
    link_path.symlink_to(target_path)

    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert app[GATEWAY_CONFIG_KEY].initialized is False
    assert app[GATEWAY_CONFIG_KEY].setup_available is False


@pytest.mark.parametrize("field", ["storage", "credentials", "config", "bootstrap"])
def test_create_app_rejects_paths_outside_nexus_data_directory(tmp_path: Path, field: str):
    data_dir = tmp_path / "nexus-data"
    outside = tmp_path / "outside"
    paths = {
        "storage_dir": data_dir / "media",
        "credentials_path": data_dir / "account.json",
        "config_path": data_dir / "config.json",
        "bootstrap_token_path": data_dir / "bootstrap.token",
    }
    argument = {
        "storage": "storage_dir",
        "credentials": "credentials_path",
        "config": "config_path",
        "bootstrap": "bootstrap_token_path",
    }[field]
    paths[argument] = outside

    with pytest.raises(ValueError, match="inside the Nexus data directory"):
        create_app(
            username=None,
            password=None,
            session_secret=None,
            upstream_url=None,
            upstream_token=None,
            data_dir=data_dir,
            **paths,
        )

    assert not outside.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory symlinks are unavailable on Windows CI")
def test_create_app_rejects_media_directory_symlink_outside_data_root(tmp_path: Path):
    data_dir = tmp_path / "nexus-data"
    data_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (data_dir / "media").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="inside the Nexus data directory"):
        create_app(
            username=None,
            password=None,
            session_secret=None,
            upstream_url=None,
            upstream_token=None,
            data_dir=data_dir,
            storage_dir=data_dir / "media",
            credentials_path=data_dir / "account.json",
            config_path=data_dir / "config.json",
            bootstrap_token_path=data_dir / "bootstrap.token",
        )


@pytest.mark.parametrize("file_name", ["config.json", "account.json", "bootstrap.token"])
def test_existing_nexus_data_hard_links_fail_closed_without_chmod(tmp_path: Path, file_name: str):
    external = tmp_path / "external"
    external.write_text("external-content", encoding="utf-8")
    external.chmod(0o644)
    original_mode = stat.S_IMODE(external.stat().st_mode)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    os.link(external, data_dir / file_name)

    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        data_dir=data_dir,
        storage_dir=data_dir / "media",
        credentials_path=data_dir / "account.json",
        config_path=data_dir / "config.json",
        bootstrap_token_path=data_dir / "bootstrap.token",
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert app[GATEWAY_CONFIG_KEY].initialized is False
    assert app[GATEWAY_CONFIG_KEY].setup_available is False
    assert stat.S_IMODE(external.stat().st_mode) == original_mode
    assert external.read_text(encoding="utf-8") == "external-content"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable on Windows")
def test_existing_config_and_account_permissions_are_tightened(tmp_path: Path):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    config_path.write_text(json.dumps({
        "hermes_api_url": "http://example.test",
        "hermes_api_token": "secret",
        "session_secret": "s" * 32,
    }), encoding="utf-8")
    account_path.write_text(json.dumps({
        "username": "admin",
        "password": "legacy-password",
        "revision": 1,
    }), encoding="utf-8")
    config_path.chmod(0o644)
    account_path.chmod(0o644)

    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    assert app[GATEWAY_CONFIG_KEY].initialized is True
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(account_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
@pytest.mark.parametrize("broken_file", ["config.json", "account.json"])
async def test_existing_broken_state_fails_closed_instead_of_reopening_setup(tmp_path: Path, broken_file: str):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    config_path.write_text("{broken" if broken_file == "config.json" else json.dumps({
        "hermes_api_url": "http://example.test",
        "hermes_api_token": "secret",
        "session_secret": "s" * 32,
    }), encoding="utf-8")
    account_path.write_text("{broken" if broken_file == "account.json" else json.dumps({
        "username": "admin",
        "password": "legacy-password",
        "revision": 1,
    }), encoding="utf-8")
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=tmp_path / "bootstrap.token",
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        status = await client.get("/api/setup/status")
        assert await status.json() == {"initialized": False, "setup_available": False}
        setup_response = await client.post("/api/setup", json={})
        assert setup_response.status == 503
        assert (await setup_response.json())["error"]["code"] == "configuration_error"


@pytest.mark.asyncio
async def test_setup_creates_admin_and_hermes_configuration(tmp_path: Path, upstream_client: TestClient):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        bootstrap_token = (tmp_path / "bootstrap.token").read_text(encoding="utf-8").strip()
        response = await client.post(
            "/api/setup",
            json={
                "username": "admin",
                "password": "strong-password",
                "hermes_api_url": str(upstream_client.make_url("/")).rstrip("/"),
                "hermes_api_token": "upstream-secret",
                "bootstrap_token": bootstrap_token,
            },
        )
        assert response.status == 201
        body = await response.json()
        assert body == {"initialized": True, "username": "admin"}
        assert "upstream-secret" not in json.dumps(body)

        assert (await (await client.get("/api/setup/status")).json()) == {"initialized": True}
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        assert saved["hermes_api_url"] == str(upstream_client.make_url("/")).rstrip("/")
        assert saved["hermes_api_token"] == "upstream-secret"
        assert len(saved["session_secret"]) >= 32
        assert json.loads(account_path.read_text(encoding="utf-8"))["username"] == "admin"

        login_response = await client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "strong-password"},
        )
        assert login_response.status == 200
        token = (await login_response.json())["access_token"]
        sessions = await client.get("/api/sessions", headers={"Authorization": f"Bearer {token}"})
        assert sessions.status == 200
        assert (await sessions.json())["data"][0]["id"] == "session-1"

        repeated = await client.post("/api/setup", json={})
        assert repeated.status == 409

    restarted_app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(restarted_app)) as restarted:
        assert (await (await restarted.get("/api/setup/status")).json()) == {"initialized": True}
        login_response = await restarted.post(
            "/api/auth/login",
            json={"username": "admin", "password": "strong-password"},
        )
        assert login_response.status == 200


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits are unavailable on Windows")
@pytest.mark.asyncio
async def test_setup_persists_config_and_account_with_owner_only_permissions(tmp_path: Path):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token_path.read_text(encoding="utf-8").strip(),
        })
    assert response.status == 201
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(account_path.stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_setup_secure_writes_leave_no_temporary_files(tmp_path: Path):
    config_path = tmp_path / "config.json"
    account_path = tmp_path / "account.json"
    token_path = tmp_path / "bootstrap.token"
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        bootstrap_token_path=token_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/setup", json={
            "username": "admin",
            "password": "strong-password",
            "hermes_api_url": "http://127.0.0.1:9",
            "hermes_api_token": "upstream-key",
            "bootstrap_token": token_path.read_text(encoding="utf-8").strip(),
        })
    assert response.status == 201
    assert not list(tmp_path.glob(".*.tmp"))


async def auth_headers(client: TestClient) -> dict[str, str]:
    return {"Authorization": f"Bearer {await login(client)}"}


async def configure_work_profile(gateway_client: TestClient, upstream_client: TestClient) -> dict[str, str]:
    headers = await auth_headers(gateway_client)
    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
            "hermes_api_token": "",
            "profiles": [{
                "id": "work",
                "name": "工作",
                "hermes_api_url": str(upstream_client.make_url("/profile-a")).rstrip("/"),
                "hermes_api_token": "profile-secret",
            }],
        },
    )
    assert response.status == 200, await response.text()
    return {**headers, "X-Nexus-Hermes-Profile": "work"}


@pytest.mark.asyncio
async def test_protected_routes_require_device_token(gateway_client: TestClient):
    response = await gateway_client.get("/api/sessions")
    assert response.status == 401


def test_run_tracker_persists_snapshot_and_marks_orphaned_run_interrupted(tmp_path: Path):
    state_file = tmp_path / "runs.json"
    tracker = RunTracker(state_file)
    tracker.start("session-1", "run-1")
    tracker.consume_sse("session-1", b"event: assistant.delta\ndata: {\"delta\":\"Hello\"}\n\n")
    tracker.consume_sse("session-1", b"event: tool.started\ndata: {\"tool_name\":\"search\"}\n\n")

    active = tracker.status("session-1")
    assert active["phase"] == "tool"
    assert active["snapshot"] == "Hello"
    assert state_file.is_file()

    restored = RunTracker(state_file).status("session-1")
    assert restored["status"] == "interrupted"
    assert restored["active"] is False
    assert restored["phase"] == "interrupted"
    assert restored["snapshot"] == "Hello"


@pytest.mark.asyncio
async def test_run_tracker_disconnects_slow_subscriber_with_bounded_queue():
    tracker = RunTracker()
    queue = tracker.subscribe("session-1")

    for index in range(tracker.SUBSCRIBER_QUEUE_LIMIT):
        await tracker.publish("session-1", f"event: delta\ndata: {index}\n\n".encode())
    started_at = time.monotonic()
    await tracker.publish("session-1", b"event: delta\ndata: overflow\n\n")

    assert time.monotonic() - started_at < 0.1
    assert queue.qsize() == 2
    error_event = await queue.get()
    assert b"slow_client" in error_event
    assert await queue.get() is None
    assert "session-1" not in tracker.subscribers


def test_run_tracker_rejects_second_subscriber_for_same_session():
    tracker = RunTracker()
    first = tracker.subscribe("session-1")

    with pytest.raises(RuntimeError, match="subscriber limit"):
        tracker.subscribe("session-1")

    tracker.unsubscribe("session-1", first)
    replacement = tracker.subscribe("session-1")
    assert replacement is not first


@pytest.mark.asyncio
async def test_gateway_cleanup_cancels_and_awaits_detached_runs(
    tmp_path: Path,
    upstream_client: TestClient,
):
    cancelled = asyncio.Event()

    async def detached_run():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "account.json",
        min_free_disk_bytes=0,
    )
    tracker = app[RUN_TRACKER_KEY]
    async with TestClient(TestServer(app)):
        task = asyncio.create_task(detached_run())
        tracker.start("session-cleanup", "run-cleanup")
        tracker.tasks["session-cleanup"] = task
        await asyncio.sleep(0)

    assert cancelled.is_set()
    assert task.done()
    assert task.cancelled()
    status = tracker.status("session-cleanup")
    assert status["status"] == "interrupted"
    assert status["active"] is False


@pytest.mark.asyncio
async def test_external_run_probe_timeout_returns_idle_without_hanging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app
    from aiohttp import ClientTimeout

    release = asyncio.Event()

    async def never_responds(_request):
        await release.wait()
        return web.json_response({"status": "ok"})

    upstream = web.Application()
    upstream.router.add_get("/health/detailed", never_responds)
    upstream.router.add_get("/api/sessions", never_responds)
    monkeypatch.setattr(
        gateway_app,
        "EXTERNAL_RUN_PROBE_TIMEOUT",
        ClientTimeout(total=0.02),
    )

    async with TestClient(TestServer(upstream)) as upstream_client:
        app = create_app(
            username="nexus",
            password="test-password",
            session_secret="test-session-secret",
            upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
            upstream_token="upstream-secret",
            storage_dir=tmp_path / "media",
            credentials_path=tmp_path / "account.json",
            min_free_disk_bytes=0,
        )
        async with TestClient(TestServer(app)) as client:
            response = await asyncio.wait_for(
                client.get(
                    "/api/sessions/session-1/run",
                    headers=await auth_headers(client),
                ),
                timeout=0.5,
            )
            payload = await response.json()
            assert response.status == 200
            assert payload["status"] == "idle"
            assert payload["active"] is False
    release.set()


@pytest.mark.asyncio
async def test_gateway_keeps_hermes_run_alive_after_mobile_disconnect(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "slow-run"},
        headers=headers,
    )
    assert response.status == 200
    await response.content.readline()
    await upstream_client.chat_started.wait()
    response.close()

    running = await gateway_client.get("/api/sessions/session-1/run", headers=headers)
    assert running.status == 200
    running_body = await running.json()
    assert running_body["session_id"] == "session-1"
    assert str(running_body["run_id"]).startswith("run_")
    assert running_body["status"] == "running"
    assert running_body["active"] is True

    upstream_client.release_chat.set()
    for _ in range(30):
        await asyncio.sleep(0.02)
        body = await (await gateway_client.get("/api/sessions/session-1/run", headers=headers)).json()
        if body["status"] == "completed":
            break
    assert body["status"] == "completed"
    assert body["active"] is False
    await asyncio.sleep(0)
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    assert "session-1" not in tracker.tasks
    assert "session-1" not in tracker.subscribers
    assert "session-1" not in tracker.buffers
    assert "session-1" not in tracker.last_persisted


@pytest.mark.asyncio
async def test_concurrent_stream_reserves_session_before_request_body_is_complete(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = {
        **await auth_headers(gateway_client),
        "Content-Type": "application/json",
    }
    release_body = asyncio.Event()

    async def delayed_body():
        yield b'{"message":"slow-'
        await release_body.wait()
        yield b'run"}'

    first_request = asyncio.create_task(
        gateway_client.post(
            "/api/sessions/session-1/chat/stream",
            data=delayed_body(),
            headers=headers,
        )
    )
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    for _ in range(100):
        if "session-1" in tracker.reservations:
            break
        await asyncio.sleep(0.01)
    assert "session-1" in tracker.reservations

    second = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "second"},
        headers=await auth_headers(gateway_client),
    )
    assert second.status == 409
    assert (await second.json())["error"]["code"] == "run_active"

    release_body.set()
    first = await first_request
    assert first.status == 200
    await upstream_client.chat_started.wait()
    upstream_client.release_chat.set()
    await first.text()
    assert upstream_client.chat_requests == [{"message": "slow-run"}]
    assert "session-1" not in tracker.reservations


@pytest.mark.asyncio
async def test_chat_and_stream_share_atomic_session_admission_and_stop(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    first = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "slow-run"},
        headers=headers,
    )
    try:
        assert first.status == 200
        await upstream_client.chat_started.wait()

        conflicting = await gateway_client.post(
            "/api/sessions/session-1/chat/stream",
            json={"message": "must-not-run"},
            headers=headers,
        )
        assert conflicting.status == 409
        assert (await conflicting.json())["error"]["code"] == "run_active"

        status = await gateway_client.get("/api/sessions/session-1/run", headers=headers)
        status_body = await status.json()
        assert status_body["status"] == "running"
        assert status_body["active"] is True
        assert status_body["stoppable"] is True

        first.close()
        stopped = await gateway_client.post(
            "/api/sessions/session-1/run/stop",
            headers=headers,
        )
        stopped_body = await stopped.json()
        assert stopped.status == 200
        assert stopped_body["local_forwarding_cancelled"] is True
        assert stopped_body["remote_status"] == "unknown"
    finally:
        upstream_client.release_chat.set()

    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    assert tracker.status("session-1")["status"] == "interrupted"
    assert "session-1" not in tracker.tasks


@pytest.mark.asyncio
async def test_tracked_chat_preserves_json_body_status_and_headers(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.chat_state["json_payload"] = {
        "object": "hermes.chat.result",
        "answer": "preserved",
    }
    upstream_client.chat_state["status"] = 202

    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "json-contract"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 202
    assert response.headers["X-Upstream-Contract"] == "preserved"
    assert await response.json() == {
        "object": "hermes.chat.result",
        "answer": "preserved",
    }
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    assert tracker.status("session-1")["status"] == "completed"
    assert "session-1" not in tracker.buffers


@pytest.mark.asyncio
async def test_external_channel_run_is_exposed_as_non_stoppable_thinking(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 0.0
    upstream_client.detailed_health_state["payload"] = {
        "status": "ok",
        "gateway_busy": True,
        "active_agents": 0,
    }
    upstream_client.session_state["rows"] = [
        {
            "id": "qq-session",
            "source": "qq",
            "message_count": 8,
            "last_active": time.time(),
        }
    ]

    response = await gateway_client.get(
        "/api/sessions/qq-session/run",
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    body = await response.json()
    assert body["session_id"] == "qq-session"
    assert body["status"] == "running"
    assert body["active"] is True
    assert body["phase"] == "thinking"
    assert body["source"] == "hermes_gateway"
    assert body["stoppable"] is False


@pytest.mark.asyncio
async def test_external_channel_busy_falls_back_to_unique_session_when_transcript_is_unchanged(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 0.0
    upstream_client.session_state["rows"] = [
        {
            "id": "wechat-session",
            "source": "wechat",
            "message_count": 3,
            "last_active": 100.0,
        }
    ]
    headers = await auth_headers(gateway_client)

    baseline = await gateway_client.get("/api/sessions/wechat-session/run", headers=headers)
    assert (await baseline.json())["status"] == "idle"

    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    running = await gateway_client.get("/api/sessions/wechat-session/run", headers=headers)
    body = await running.json()

    assert body["status"] == "running"
    assert body["active"] is True
    assert body["source"] == "hermes_gateway"


@pytest.mark.asyncio
async def test_external_channel_run_completes_then_returns_to_idle(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 0.0
    observer.completion_seconds = 0.01
    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    upstream_client.session_state["rows"] = [
        {
            "id": "qq-session",
            "source": "qq",
            "message_count": 1,
            "last_active": time.time(),
        }
    ]
    headers = await auth_headers(gateway_client)

    running = await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    assert (await running.json())["status"] == "running"

    upstream_client.detailed_health_state["payload"]["gateway_busy"] = False
    completed = await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    completed_body = await completed.json()
    assert completed_body["status"] == "completed"
    assert completed_body["active"] is False
    assert completed_body["stoppable"] is False

    await asyncio.sleep(0.02)
    idle = await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    idle_body = await idle.json()
    assert idle_body["status"] == "idle"
    assert idle_body["active"] is False


@pytest.mark.asyncio
async def test_local_nexus_run_status_has_priority_over_external_observer(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    tracker.start("session-1", "run-local")
    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    before = dict(upstream_client.probe_counts)

    response = await gateway_client.get(
        "/api/sessions/session-1/run",
        headers=await auth_headers(gateway_client),
    )

    body = await response.json()
    assert body["run_id"] == "run-local"
    assert body["source"] == "nexus_gateway"
    assert body["stoppable"] is True
    assert upstream_client.probe_counts == before


@pytest.mark.asyncio
async def test_stop_run_waits_for_cancelled_task_cleanup(gateway_client: TestClient):
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    cleanup_finished = asyncio.Event()

    async def active_run():
        tracker.start("session-1", "run-stop-test")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            tracker.finish(
                "session-1",
                "interrupted",
                "Nexus local forwarding was cancelled; Hermes remote run status is unknown.",
            )
            raise
        finally:
            await asyncio.sleep(0.01)
            cleanup_finished.set()

    task = asyncio.create_task(active_run())
    tracker.tasks["session-1"] = task
    await asyncio.sleep(0)

    response = await gateway_client.post(
        "/api/sessions/session-1/run/stop",
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    body = await response.json()
    assert body == {
        "session_id": "session-1",
        "stopped": True,
        "local_forwarding_cancelled": True,
        "remote_status": "unknown",
        "remote_stopped": False,
    }
    assert cleanup_finished.is_set()
    assert task.done()
    status = tracker.status("session-1")
    assert status["active"] is False
    assert status["status"] == "interrupted"


@pytest.mark.asyncio
async def test_generic_stream_error_keeps_run_failed(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.chat_state["stream"] = (
        'event: error\ndata: {"message":"provider failed at C:/private/upstream-secret"}\n\n'
        "event: done\ndata: {}\n\n"
    )
    headers = await auth_headers(gateway_client)

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "fail"},
        headers=headers,
    )
    assert response.status == 200
    stream = await response.text()
    assert "hermes_upstream_error" in stream
    assert "上游服务暂时不可用" in stream
    assert "provider failed" not in stream
    assert "C:/private" not in stream
    assert "upstream-secret" not in stream

    status = await gateway_client.get("/api/sessions/session-1/run", headers=headers)
    payload = await status.json()
    assert payload["status"] == "failed"
    assert payload["active"] is False
    assert payload["message"] == "上游服务暂时不可用"


@pytest.mark.asyncio
async def test_truncated_stream_reports_error_and_keeps_run_failed(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.chat_state["stream"] = (
        'event: assistant.delta\ndata: {"delta":"partial"}\n\n'
    )
    headers = await auth_headers(gateway_client)

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "truncate"},
        headers=headers,
    )
    stream = await response.text()
    assert "partial" in stream
    assert "event: error" in stream
    assert "Hermes 响应流意外结束" in stream

    status = await gateway_client.get("/api/sessions/session-1/run", headers=headers)
    payload = await status.json()
    assert payload["status"] == "failed"
    assert payload["active"] is False
    assert payload["message"] == "Hermes 响应流意外结束"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_selection",
    [
        {},
        {"provider": "openai-codex", "model": "gpt-5.6"},
    ],
)
async def test_unseparated_oversized_sse_event_is_bounded_and_sanitized(
    gateway_client: TestClient,
    upstream_client: TestClient,
    model_selection: dict,
):
    import nexus_gateway.app as gateway_app

    secret = "C:/private/upstream-secret"
    upstream_client.chat_state["stream"] = (
        "event: assistant.delta\ndata: "
        + secret
        + ("x" * gateway_app.SSE_EVENT_BUFFER_LIMIT)
    )
    headers = await auth_headers(gateway_client)

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "oversized", **model_selection},
        headers=headers,
    )
    stream = await response.text()
    assert "upstream_event_too_large" in stream
    assert "Hermes 响应事件过大" in stream
    assert secret not in stream
    assert len(stream) < 2048

    status = await gateway_client.get("/api/sessions/session-1/run", headers=headers)
    payload = await status.json()
    assert payload["status"] == "failed"
    assert payload["active"] is False


@pytest.mark.asyncio
async def test_external_observer_health_failure_clears_synthetic_active_state(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 0.0
    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    upstream_client.session_state["rows"] = [
        {
            "id": "qq-session",
            "source": "qq",
            "message_count": 1,
            "last_active": time.time(),
        }
    ]
    headers = await auth_headers(gateway_client)

    running = await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    assert (await running.json())["active"] is True

    upstream_client.detailed_health_state["status"] = 503
    failed_probe = await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    body = await failed_probe.json()
    assert body["status"] == "idle"
    assert body["active"] is False


@pytest.mark.asyncio
async def test_external_observer_does_not_guess_between_equally_likely_channels(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 0.0
    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    upstream_client.session_state["rows"] = [
        {"id": "qq-session", "source": "qq", "message_count": 1, "last_active": 100.0},
        {"id": "wechat-session", "source": "wechat", "message_count": 1, "last_active": 100.0},
    ]

    response = await gateway_client.get(
        "/api/sessions/qq-session/run",
        headers=await auth_headers(gateway_client),
    )

    body = await response.json()
    assert body["status"] == "idle"
    assert body["active"] is False


@pytest.mark.asyncio
async def test_external_observer_short_cache_avoids_duplicate_upstream_probes(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    observer = gateway_client.server.app[EXTERNAL_RUN_OBSERVER_KEY]
    observer.cache_seconds = 60.0
    upstream_client.detailed_health_state["payload"]["gateway_busy"] = True
    upstream_client.session_state["rows"] = [
        {"id": "qq-session", "source": "qq", "message_count": 1, "last_active": time.time()}
    ]
    headers = await auth_headers(gateway_client)

    await gateway_client.get("/api/sessions/qq-session/run", headers=headers)
    first_counts = dict(upstream_client.probe_counts)
    await gateway_client.get("/api/sessions/qq-session/run", headers=headers)

    assert first_counts == {"health_detailed": 1, "sessions": 1}
    assert upstream_client.probe_counts == first_counts


@pytest.mark.asyncio
async def test_health_combines_gateway_and_upstream_state(gateway_client: TestClient):
    response = await gateway_client.get("/health")
    assert response.status == 200
    body = await response.json()
    assert body["status"] == "ok"
    assert body["gateway"] == "nexus-mobile-gateway"
    assert body["upstream"]["platform"] == "hermes-agent"


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [401, 403])
async def test_health_rejects_publicly_healthy_hermes_when_api_key_is_invalid(
    gateway_client: TestClient,
    upstream_client: TestClient,
    upstream_status: int,
):
    upstream_client.detailed_health_state.update({
        "status": upstream_status,
        "payload": {"detail": "bad upstream-secret at http://private-hermes.local"},
    })

    response = await gateway_client.get("/health")

    assert response.status == 503
    body = await response.json()
    assert body["status"] == "degraded"
    assert body["upstream"] == {
        "status": "auth_failed",
        "http_status": upstream_status,
        "platform": "hermes-agent",
        "version": "test",
    }
    assert body["error"] == {
        "code": "hermes_auth_failed",
        "message": "Hermes API Server Key 无效或无权访问，请在 Nexus 配置中检查 Hermes API 地址和 API Server Key",
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "upstream-secret" not in serialized
    assert "private-hermes.local" not in serialized


@pytest.mark.asyncio
async def test_health_falls_back_to_sessions_when_legacy_hermes_lacks_newer_probes(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.detailed_health_state["status"] = 404
    upstream_client.models_state["status"] = 404

    response = await gateway_client.get("/health")

    assert response.status == 200
    body = await response.json()
    assert body["status"] == "ok"
    assert body["upstream"]["authenticated"] is True
    assert upstream_client.probe_counts["health_detailed"] == 1
    assert upstream_client.probe_counts["models"] == 1
    assert upstream_client.probe_counts["sessions"] == 1


@pytest.mark.asyncio
async def test_health_returns_actionable_sanitized_error_when_hermes_is_unavailable(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.health_state["status"] = 503
    upstream_client.health_state["payload"] = {
        "status": "degraded",
        "version": "test",
        "detail": "sensitive-upstream-detail",
    }

    response = await gateway_client.get("/health")

    assert response.status == 503
    body = await response.json()
    assert body["status"] == "degraded"
    assert body["upstream"] == {"status": "degraded", "http_status": 503, "version": "test"}
    assert body["error"]["code"] == "hermes_unavailable"
    assert "\u65e0\u6cd5\u8bbf\u95ee Hermes API" in body["error"]["message"]
    assert "host.docker.internal" in body["error"]["message"]
    serialized = json.dumps(body, ensure_ascii=False)
    assert "sensitive-upstream-detail" not in serialized
    assert "upstream-secret" not in serialized
    assert str(upstream_client.make_url("/")).rstrip("/") not in serialized


def test_fnos_host_unavailable_message_uses_loopback_guidance(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEXUS_DEPLOYMENT_MODE", "fnos-host")

    message = _hermes_unavailable_message()

    assert "127.0.0.1:8642" in message
    assert "host.docker.internal" not in message


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(gateway_client: TestClient):
    response = await gateway_client.post(
        "/api/auth/login",
        json={"username": "nexus", "password": "wrong"},
    )
    assert response.status == 401


@pytest.mark.asyncio
async def test_login_supports_unicode_credentials(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="Nexus",
        password="月光密码-123",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/auth/login",
            json={"username": "Nexus", "password": "月光密码-123"},
        )
        assert response.status == 200
        assert (await response.json())["access_token"]


@pytest.mark.asyncio
async def test_legacy_plaintext_account_is_migrated_to_scrypt_after_login(tmp_path: Path, upstream_client: TestClient):
    account_path = tmp_path / "account.json"
    config_path = tmp_path / "config.json"
    account_path.write_text(json.dumps({
        "username": "legacy-admin",
        "password": "legacy-password",
        "revision": 2,
    }), encoding="utf-8")
    config_path.write_text(json.dumps({
        "hermes_api_url": str(upstream_client.make_url("/")).rstrip("/"),
        "hermes_api_token": "upstream-secret",
        "session_secret": "s" * 32,
    }), encoding="utf-8")
    app = create_app(
        username=None,
        password=None,
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=account_path,
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post("/api/auth/login", json={
            "username": "legacy-admin", "password": "legacy-password",
        })
        assert response.status == 200

    saved = json.loads(account_path.read_text(encoding="utf-8"))
    assert "password" not in saved
    assert saved["password_scheme"] == "scrypt"
    assert saved["password_salt"]
    assert saved["password_hash"]


@pytest.mark.asyncio
async def test_changed_password_is_stored_as_scrypt_not_plaintext(gateway_client: TestClient):
    token = await login(gateway_client)
    changed = await gateway_client.put(
        "/api/admin/account",
        json={"current_password": "test-password", "username": "nexus", "password": "new-password-123"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert changed.status == 200
    account_path = gateway_client.server.app[CREDENTIALS_PATH_KEY]
    saved = json.loads(account_path.read_text(encoding="utf-8"))
    assert "password" not in saved
    assert saved["password_scheme"] == "scrypt"


@pytest.mark.asyncio
async def test_admin_hermes_config_never_returns_api_key(gateway_client: TestClient):
    response = await gateway_client.get(
        "/api/admin/hermes-config",
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    raw = await response.text()
    body = json.loads(raw)
    assert body == {
        "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
        "key_configured": True,
        "profiles": [],
    }
    assert "hermes_api_token" not in body
    assert "upstream-secret" not in raw


@pytest.mark.asyncio
async def test_admin_hermes_config_rejects_wrong_nexus_password_without_writing(
    gateway_client: TestClient,
):
    config_path = gateway_client.server.app[CONFIG_PATH_KEY]
    before = config_path.read_bytes()
    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=await auth_headers(gateway_client),
        json={
            "current_password": "wrong-password",
            "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
            "hermes_api_token": "candidate-secret",
        },
    )

    assert response.status == 401
    assert (await response.json())["error"]["code"] == "invalid_current_password"
    assert config_path.read_bytes() == before
    assert gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_token == "upstream-secret"


@pytest.mark.asyncio
async def test_admin_hermes_config_rejects_bad_key_without_overwriting_old_config(
    gateway_client: TestClient,
):
    config_path = gateway_client.server.app[CONFIG_PATH_KEY]
    before = config_path.read_bytes()
    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=await auth_headers(gateway_client),
        json={
            "current_password": "test-password",
            "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
            "hermes_api_token": "invalid-candidate-secret",
        },
    )

    assert response.status == 422
    raw = await response.text()
    assert json.loads(raw)["error"]["code"] == "hermes_auth_failed"
    assert "invalid-candidate-secret" not in raw
    assert "upstream-secret" not in raw
    assert config_path.read_bytes() == before
    assert gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_token == "upstream-secret"


@pytest.mark.asyncio
async def test_admin_hermes_config_atomically_updates_verified_key_without_expiring_login(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    upstream_client.expected_auth["token"] = "replacement-secret"
    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
            "hermes_api_token": "replacement-secret",
        },
    )

    assert response.status == 200
    raw = await response.text()
    body = json.loads(raw)
    assert body["status"] == "ok"
    assert body["key_configured"] is True
    assert "replacement-secret" not in raw
    config = gateway_client.server.app[GATEWAY_CONFIG_KEY]
    assert config.upstream_token == "replacement-secret"
    saved = json.loads(gateway_client.server.app[CONFIG_PATH_KEY].read_text(encoding="utf-8"))
    assert saved["hermes_api_token"] == "replacement-secret"
    assert saved["session_secret"] == config.session_secret

    still_logged_in = await gateway_client.get("/api/sessions", headers=headers)
    assert still_logged_in.status == 200


@pytest.mark.asyncio
async def test_admin_hermes_config_blank_key_keeps_existing_verified_key(gateway_client: TestClient):
    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=await auth_headers(gateway_client),
        json={
            "current_password": "test-password",
            "hermes_api_url": gateway_client.server.app[GATEWAY_CONFIG_KEY].upstream_url,
            "hermes_api_token": "",
        },
    )

    assert response.status == 200
    saved = json.loads(gateway_client.server.app[CONFIG_PATH_KEY].read_text(encoding="utf-8"))
    assert saved["hermes_api_token"] == "upstream-secret"


@pytest.mark.asyncio
async def test_admin_hermes_profiles_are_verified_persisted_and_never_return_keys(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await configure_work_profile(gateway_client, upstream_client)

    response = await gateway_client.get("/api/admin/hermes-config", headers=headers)
    assert response.status == 200
    raw = await response.text()
    body = json.loads(raw)
    assert body["profiles"] == [{
        "id": "work",
        "name": "工作",
        "is_default": False,
        "hermes_api_url": str(upstream_client.make_url("/profile-a")).rstrip("/"),
        "key_configured": True,
    }]
    assert "profile-secret" not in raw
    assert "upstream-secret" not in raw

    saved = json.loads(gateway_client.server.app[CONFIG_PATH_KEY].read_text(encoding="utf-8"))
    assert saved["hermes_profiles"][0]["id"] == "work"
    assert saved["hermes_profiles"][0]["hermes_api_token"] == "profile-secret"


@pytest.mark.asyncio
async def test_profile_directory_and_proxy_use_selected_original_hermes_api(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await configure_work_profile(gateway_client, upstream_client)

    directory = await gateway_client.get("/api/hermes/personas?refresh=true", headers=headers)
    assert directory.status == 200
    payload = await directory.json()
    profiles = payload["data"]
    assert profiles == [
        {
            "id": "default",
            "name": "test-model",
            "profile_name": "test-model",
            "connection_id": "default",
            "connection_name": "Hermes 默认（default）",
            "is_default": True,
            "available": True,
            "state": "ok",
            "source": "hermes_profile_api",
        },
        {
            "id": "work",
            "name": "work",
            "profile_name": "work",
            "connection_id": "work",
            "connection_name": "工作",
            "is_default": False,
            "available": True,
            "state": "ok",
            "source": "hermes_profile_api",
        },
    ]
    assert payload["notice"] is None
    assert payload["discovery"]["directory_complete"] is False

    legacy_directory = await gateway_client.get("/api/hermes/profiles", headers=headers)
    assert legacy_directory.status == 200
    assert [item["profile_name"] for item in (await legacy_directory.json())["data"]] == ["test-model", "work"]

    connections = await gateway_client.get("/api/hermes/connections", headers=headers)
    assert connections.status == 200
    assert [item["id"] for item in (await connections.json())["data"]] == ["default", "work"]

    sessions = await gateway_client.get("/api/sessions", headers=headers)
    assert sessions.status == 200
    assert [item["id"] for item in (await sessions.json())["data"]] == ["work-session"]
    assert ("GET", "/profile-a/api/sessions") in upstream_client.profile_requests

    chat = await gateway_client.post(
        "/api/sessions/work-session/chat/stream",
        headers=headers,
        json={"message": "profile chat", "persona_model": "legacy-wrong-field"},
    )
    assert chat.status == 200
    assert "work" in await chat.text()
    assert upstream_client.profile_captured_chat == {"message": "profile chat"}


@pytest.mark.asyncio
async def test_persona_directory_reads_the_active_profile_name_from_original_hermes(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.models_state["payload"] = {
        "object": "list",
        "data": [
            {"id": "主人格", "object": "model", "parent": None},
            {"id": "gpt-5.6-sol", "object": "model", "parent": "主人格"},
        ],
    }
    headers = await auth_headers(gateway_client)

    response = await gateway_client.get("/api/hermes/personas?refresh=true", headers=headers)

    assert response.status == 200
    raw = await response.text()
    payload = json.loads(raw)
    assert payload["data"][0]["name"] == "主人格"
    assert payload["data"][0]["profile_name"] == "主人格"
    assert payload["data"][0]["connection_id"] == "default"
    assert payload["data"][0]["available"] is True
    assert "gpt-5.6-sol" not in [item["name"] for item in payload["data"]]
    assert "upstream-secret" not in raw
    assert "主人格" in payload["notice"]


@pytest.mark.asyncio
async def test_persona_directory_keeps_configured_connection_when_identity_refresh_fails(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.models_state["status"] = 503
    headers = await auth_headers(gateway_client)

    response = await gateway_client.get("/api/hermes/personas?refresh=true", headers=headers)

    assert response.status == 200
    payload = await response.json()
    assert payload["data"] == [{
        "id": "default",
        "name": "Hermes 默认（default）",
        "profile_name": None,
        "connection_id": "default",
        "connection_name": "Hermes 默认（default）",
        "is_default": True,
        "available": False,
        "state": "unavailable",
        "source": "hermes_profile_api",
    }]
    assert "无法读取" in payload["notice"]


@pytest.mark.asyncio
async def test_persona_directory_marks_upstream_key_rejection_without_leaking_key(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.expected_auth["token"] = "different-upstream-secret"
    headers = await auth_headers(gateway_client)

    response = await gateway_client.get("/api/hermes/personas?refresh=true", headers=headers)

    assert response.status == 200
    raw = await response.text()
    payload = json.loads(raw)
    assert payload["data"][0]["state"] == "auth_failed"
    assert payload["data"][0]["available"] is False
    assert "upstream-secret" not in raw
    assert "different-upstream-secret" not in raw


@pytest.mark.asyncio
async def test_persona_directory_caches_successful_read_only_identity_probe(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    before = upstream_client.probe_counts.get("models", 0)

    first = await gateway_client.get("/api/hermes/personas", headers=headers)
    second = await gateway_client.get("/api/hermes/personas", headers=headers)

    assert first.status == 200
    assert second.status == 200
    assert upstream_client.probe_counts.get("models", 0) == before + 1


@pytest.mark.asyncio
async def test_run_tracker_status_is_isolated_for_same_session_id_across_profiles(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    profile_headers = await configure_work_profile(gateway_client, upstream_client)
    default_headers = {key: value for key, value in profile_headers.items() if key != "X-Nexus-Hermes-Profile"}
    tracker = gateway_client.server.app[RUN_TRACKER_KEY]
    tracker.start("shared-session", "run-default")
    tracker.start("profile:work:shared-session", "run-work")

    default_status = await gateway_client.get(
        "/api/sessions/shared-session/run",
        headers=default_headers,
    )
    work_status = await gateway_client.get(
        "/api/sessions/shared-session/run",
        headers=profile_headers,
    )

    default_payload = await default_status.json()
    work_payload = await work_status.json()
    assert default_payload["run_id"] == "run-default"
    assert work_payload["run_id"] == "run-work"
    assert default_payload["session_id"] == "shared-session"
    assert work_payload["session_id"] == "shared-session"


@pytest.mark.asyncio
async def test_default_hermes_url_change_requires_key_to_prevent_secret_forwarding(
    gateway_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    config = gateway_client.server.app[GATEWAY_CONFIG_KEY]
    original_url = config.upstream_url

    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": f"{original_url}/moved",
            "hermes_api_token": "",
        },
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "missing_hermes_token"
    assert config.upstream_url == original_url
    assert config.upstream_token == "upstream-secret"


@pytest.mark.asyncio
async def test_profile_blank_key_is_retained_only_when_profile_url_is_unchanged(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await configure_work_profile(gateway_client, upstream_client)
    config = gateway_client.server.app[GATEWAY_CONFIG_KEY]
    original_url = config.profiles["work"].upstream_url

    retained = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": config.upstream_url,
            "hermes_api_token": "",
            "profiles": [{
                "id": "work",
                "name": "工作助手",
                "hermes_api_url": original_url,
                "hermes_api_token": "",
            }],
        },
    )
    assert retained.status == 200, await retained.text()
    assert config.profiles["work"].upstream_token == "profile-secret"
    assert config.profiles["work"].name == "工作助手"

    rejected = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": config.upstream_url,
            "hermes_api_token": "",
            "profiles": [{
                "id": "work",
                "name": "工作助手",
                "hermes_api_url": f"{original_url}/moved",
                "hermes_api_token": "",
            }],
        },
    )
    assert rejected.status == 400
    assert (await rejected.json())["error"]["code"] == "missing_hermes_profile_token"
    assert config.profiles["work"].upstream_url == original_url
    assert config.profiles["work"].upstream_token == "profile-secret"


@pytest.mark.asyncio
async def test_profile_can_be_removed_from_persisted_directory(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await configure_work_profile(gateway_client, upstream_client)
    config = gateway_client.server.app[GATEWAY_CONFIG_KEY]

    response = await gateway_client.put(
        "/api/admin/hermes-config",
        headers=headers,
        json={
            "current_password": "test-password",
            "hermes_api_url": config.upstream_url,
            "hermes_api_token": "",
            "profiles": [],
        },
    )

    assert response.status == 200, await response.text()
    directory = await gateway_client.get("/api/hermes/profiles", headers=headers)
    assert [item["id"] for item in (await directory.json())["data"]] == ["default"]
    saved = json.loads(gateway_client.server.app[CONFIG_PATH_KEY].read_text(encoding="utf-8"))
    assert "hermes_profiles" not in saved


@pytest.mark.asyncio
async def test_saved_profiles_are_restored_after_gateway_restart(
    tmp_path: Path,
    upstream_client: TestClient,
):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({
        "hermes_api_url": str(upstream_client.make_url("/")).rstrip("/"),
        "hermes_api_token": "upstream-secret",
        "session_secret": "restart-session-secret",
        "hermes_profiles": [{
            "id": "work",
            "name": "工作",
            "hermes_api_url": str(upstream_client.make_url("/profile-a")).rstrip("/"),
            "hermes_api_token": "profile-secret",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret=None,
        upstream_url=None,
        upstream_token=None,
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=config_path,
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )

    async with TestClient(TestServer(app)) as client:
        headers = await auth_headers(client)
        directory = await client.get("/api/hermes/profiles", headers=headers)
        assert [item["id"] for item in (await directory.json())["data"]] == ["default", "work"]
        profile_headers = {**headers, "X-Nexus-Hermes-Profile": "work"}
        sessions = await client.get("/api/sessions", headers=profile_headers)
        assert sessions.status == 200
        assert [item["id"] for item in (await sessions.json())["data"]] == ["work-session"]


@pytest.mark.asyncio
async def test_connection_header_selects_the_configured_original_hermes_profile_api(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    legacy_headers = await configure_work_profile(gateway_client, upstream_client)
    headers = {
        key: value
        for key, value in legacy_headers.items()
        if key != "X-Nexus-Hermes-Profile"
    }
    headers["X-Nexus-Hermes-Connection"] = "work"

    response = await gateway_client.get("/api/sessions", headers=headers)

    assert response.status == 200
    assert [item["id"] for item in (await response.json())["data"]] == ["work-session"]


@pytest.mark.asyncio
async def test_unknown_profile_header_fails_before_contacting_upstream(gateway_client: TestClient):
    headers = await auth_headers(gateway_client)
    headers["X-Nexus-Hermes-Profile"] = "missing"

    response = await gateway_client.get("/api/sessions", headers=headers)

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "unknown_hermes_profile"


@pytest.mark.asyncio
async def test_login_rate_limits_repeated_failures_from_same_ip(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=tmp_path / "config.json",
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
        login_rate_limit=3,
        login_rate_window_seconds=60,
    )
    async with TestClient(TestServer(app)) as client:
        for _ in range(3):
            response = await client.post(
                "/api/auth/login",
                json={"username": "nexus", "password": "wrong-password"},
            )
            assert response.status == 401
        throttled = await client.post(
            "/api/auth/login",
            json={"username": "nexus", "password": "wrong-password"},
        )
        assert throttled.status == 429
        body = await throttled.json()
        assert body["error"]["code"] == "login_throttled"
        assert "retry_after" in body["error"]
        assert body["error"]["retry_after"] > 0


@pytest.mark.asyncio
async def test_login_rate_limit_allows_success_before_threshold(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=tmp_path / "config.json",
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
        login_rate_limit=5,
        login_rate_window_seconds=60,
    )
    async with TestClient(TestServer(app)) as client:
        for _ in range(4):
            response = await client.post(
                "/api/auth/login",
                json={"username": "nexus", "password": "wrong"},
            )
            assert response.status == 401
        ok = await client.post(
            "/api/auth/login",
            json={"username": "nexus", "password": "test-password"},
        )
        assert ok.status == 200


@pytest.mark.asyncio
async def test_login_rate_limit_resets_after_window(tmp_path: Path, upstream_client: TestClient):
    window = 2.0
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        config_path=tmp_path / "config.json",
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
        login_rate_limit=2,
        login_rate_window_seconds=window,
    )
    async with TestClient(TestServer(app)) as client:
        for _ in range(2):
            response = await client.post(
                "/api/auth/login",
                json={"username": "nexus", "password": "wrong"},
            )
            assert response.status == 401
        blocked = await client.post(
            "/api/auth/login",
            json={"username": "nexus", "password": "wrong"},
        )
        assert blocked.status == 429
        await asyncio.sleep(window + 0.1)
        retry = await client.post(
            "/api/auth/login",
            json={"username": "nexus", "password": "wrong"},
        )
        assert retry.status == 401


@pytest.mark.asyncio
async def test_admin_can_change_credentials_and_invalidate_old_tokens(gateway_client: TestClient):
    old_token = await login(gateway_client)
    changed = await gateway_client.put(
        "/api/admin/account",
        json={
            "current_password": "test-password",
            "username": "new-nexus",
            "password": "new-password-123",
        },
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert changed.status == 200
    body = await changed.json()
    assert body["username"] == "new-nexus"
    assert body["access_token"]

    old_access = await gateway_client.get(
        "/api/sessions",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert old_access.status == 401

    old_login = await gateway_client.post(
        "/api/auth/login",
        json={"username": "nexus", "password": "test-password"},
    )
    assert old_login.status == 401

    new_login = await gateway_client.post(
        "/api/auth/login",
        json={"username": "new-nexus", "password": "new-password-123"},
    )
    assert new_login.status == 200


@pytest.mark.asyncio
async def test_web_admin_requires_login_and_lists_files(gateway_client: TestClient):
    page = await gateway_client.get("/")
    assert page.status == 200
    html = await page.text()
    assert "Nexus 管理中心" in html
    assert "初始化 Nexus" in html
    assert 'id="setupForm"' in html
    assert 'id="setupHermesUrl"' in html
    assert 'id="setupHermesToken"' in html
    assert 'href="/assets/styles.css"' in html
    assert 'src="/assets/app.js"' in html

    assert "function sendChat" not in html

    styles = await gateway_client.get("/assets/styles.css")
    script = await gateway_client.get("/assets/app.js")
    assert styles.status == 200
    assert styles.content_type == "text/css"
    assert script.status == 200
    assert script.content_type == "text/javascript"
    javascript = await script.text()
    assert "网页聊天" not in html
    assert 'data-page="chat"' not in html
    assert 'id="chatForm"' not in html
    assert "文件管理" in html
    assert "语音管理" in html
    assert "账号安全" in html
    assert "系统状态" in html
    assert "createSession" not in javascript
    assert "renameSession" not in javascript
    assert "deleteSession" not in javascript
    assert "sendChat" not in javascript
    assert "renderMarkdown" not in javascript
    assert "uploadAttachment" not in javascript
    assert "/api/sessions" not in javascript
    assert "/chat/stream" not in javascript
    assert "uploadManagedFile" in javascript
    assert "/api/uploads" in javascript

    unauthorized = await gateway_client.get("/api/admin/files")
    assert unauthorized.status == 401

    response = await gateway_client.get(
        "/api/admin/files",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    assert (await response.json())["data"] == []


@pytest.mark.asyncio
async def test_web_root_describes_http_origin_and_reverse_proxy(tmp_path: Path):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:9",
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        transcribe_audio=lambda _path: {"success": False, "transcript": ""},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/", allow_redirects=False)
        assert response.status == 200
        html = await response.text()
        assert "HTTP 源站" in html
        assert "反向代理" in html
        assert "上传正式证书" not in html
        assert "Strict-Transport-Security" not in response.headers

        headers = await auth_headers(client)
        ca_response = await client.get("/nexus-local-ca.crt", headers=headers)
        assert ca_response.status == 404

        tls_status = await client.get("/api/admin/tls", headers=headers)
        assert tls_status.status == 404
        tls_upload = await client.put(
            "/api/admin/tls",
            json={"certificate_chain": "must-not-be-forwarded", "private_key": "must-not-be-forwarded"},
            headers=headers,
        )
        assert tls_upload.status == 404


@pytest.mark.asyncio
async def test_security_headers_are_suitable_for_an_http_origin():
    async def handler(_request):
        return web.Response(text="ok")

    request = make_mocked_request("GET", "/")
    response = await security_headers(request, handler)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Strict-Transport-Security" not in response.headers


@pytest.mark.asyncio
async def test_admin_overview_combines_gateway_files_and_hermes_sessions(gateway_client: TestClient):
    response = await gateway_client.get(
        "/api/admin/overview",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    body = await response.json()
    assert body["status"] == "ok"
    assert body["session_count"] == 1
    assert body["file_count"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("upstream_status", "expected_status"),
    [(401, "auth_failed"), (403, "auth_failed"), (503, "degraded")],
)
async def test_admin_overview_distinguishes_hermes_auth_and_availability_failures(
    gateway_client: TestClient,
    upstream_client: TestClient,
    upstream_status: int,
    expected_status: str,
):
    upstream_client.session_state["status"] = upstream_status

    response = await gateway_client.get(
        "/api/admin/overview",
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    body = await response.json()
    assert body["status"] == expected_status
    assert body["session_count"] == 0


@pytest.mark.asyncio
async def test_existing_hermes_api_is_proxied_with_server_side_key(gateway_client: TestClient):
    headers = await auth_headers(gateway_client)
    response = await gateway_client.get("/api/sessions", headers=headers)
    assert response.status == 200
    assert (await response.json())["data"][0]["id"] == "session-1"

    created = await gateway_client.post("/api/sessions", json={}, headers=headers)
    assert created.status == 200
    assert (await created.json())["session"]["id"] == "session-new"

    renamed = await gateway_client.patch("/api/sessions/session-1", json={"title": "新名称"}, headers=headers)
    assert renamed.status == 200
    assert (await renamed.json())["session"]["title"] == "新名称"

    deleted = await gateway_client.delete("/api/sessions/session-1", headers=headers)
    assert deleted.status == 200
    assert (await deleted.json())["deleted"] is True


def test_visible_message_text_removes_embedded_screenshot_marker(tmp_path: Path):
    store = MediaStore(tmp_path / "media", 1024)

    assert store._visible_message_text("图片测试\n[screenshot]") == "图片测试"
    assert store._visible_message_text("[screenshot]") == ""


def test_visible_message_text_removes_attachment_metadata_appended_to_body(tmp_path: Path):
    store = MediaStore(tmp_path / "media", 1024)
    content = (
        "请处理这个文件\n"
        "附件：rustdesk.apk\n"
        "类型：application/vnd.android.package-archive\n"
        "大小：26871021 字节\n"
        "服务器路径：C:\\private\\rustdesk.apk"
    )

    assert store._visible_message_text(content) == "请处理这个文件"


@pytest.mark.asyncio
async def test_message_history_can_return_the_latest_page(gateway_client: TestClient):
    response = await gateway_client.get(
        "/api/sessions/session-1/messages?limit=1&offset=0",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    body = await response.json()
    assert [item["content"] for item in body["data"]] == ["看到了"]
    assert body["pagination"] == {"total": 2, "offset": 0, "limit": 1, "has_more": True}


@pytest.mark.asyncio
async def test_public_message_pagination_excludes_internal_user_and_tool_records(gateway_client: TestClient):
    response = await gateway_client.get(
        "/api/sessions/session-1/messages?limit=10&offset=0",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    body = await response.json()
    assert [item["role"] for item in body["data"]] == ["user", "assistant"]
    assert [item["id"] for item in body["data"]] == ["m1", "m2"]
    assert all(MOBILE_CLIENT_CONTEXT_MARKER not in str(item.get("content", "")) for item in body["data"])
    assert body["pagination"] == {"total": 2, "offset": 0, "limit": 10, "has_more": False}


@pytest.mark.asyncio
async def test_session_history_restores_uploaded_image_metadata(
    gateway_client: TestClient,
    tmp_path: Path,
):
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    file_id = "a" * 32
    image_path = media_root / f"{file_id}.png"
    image_path.write_bytes(b"image")
    (media_root / f"{file_id}.json").write_text(json.dumps({
        "id": file_id,
        "name": "photo.png",
        "mime_type": "image/png",
        "size": 5,
        "sha256": "test",
        "created_at": 1.0,
        "server_path": str(image_path),
    }), encoding="utf-8")
    index = media_root / "session_media.json"
    index.write_text(json.dumps({"session-1": [{"file_id": file_id}]}), encoding="utf-8")

    response = await gateway_client.get(
        "/api/sessions/session-1/messages",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    messages = (await response.json())["data"]
    assert all(not str(message.get("content", "")).startswith("[CONTEXT COMPACTION") for message in messages)
    assert messages[0]["content"] == "看看"
    assert messages[0]["nexus_images"] == [{
        "id": file_id,
        "url": f"/api/files/{file_id}",
        "name": "photo.png",
    }]


@pytest.mark.asyncio
async def test_session_history_restores_uploaded_file_metadata(
    gateway_client: TestClient,
    tmp_path: Path,
):
    media_root = tmp_path / "media"
    media_root.mkdir(exist_ok=True)
    file_id = "b" * 32
    file_path = media_root / f"{file_id}.txt"
    file_path.write_text("document", encoding="utf-8")
    (media_root / f"{file_id}.json").write_text(json.dumps({
        "id": file_id,
        "name": "document.txt",
        "mime_type": "text/plain",
        "size": 8,
        "sha256": "test",
        "created_at": 1.0,
        "server_path": str(file_path),
    }), encoding="utf-8")
    index = media_root / "session_media.json"
    index.write_text(json.dumps({"session-1": [{"file_id": file_id}]}), encoding="utf-8")

    response = await gateway_client.get(
        "/api/sessions/session-1/messages",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    messages = (await response.json())["data"]
    assert messages[0]["nexus_files"] == [{
        "id": file_id,
        "url": f"/api/files/{file_id}",
        "name": "document.txt",
        "mime_type": "text/plain",
        "size": 8,
    }]


@pytest.mark.asyncio
async def test_upload_and_download_use_same_gateway_port(gateway_client: TestClient):
    form = FormData()
    form.add_field("file", io.BytesIO(b"hello gateway"), filename="test.txt", content_type="text/plain")

    upload = await gateway_client.post("/api/uploads", data=form, headers=await auth_headers(gateway_client))
    assert upload.status == 201
    item = (await upload.json())["file"]
    assert item["name"] == "test.txt"
    assert item["category"] == "files"
    assert item["date"]
    assert item["download_url"] == f"/api/files/{item['id']}"
    assert "server_path" not in item

    metadata = await gateway_client.get(f"/api/files/{item['id']}/metadata", headers=await auth_headers(gateway_client))
    assert metadata.status == 200
    metadata_file = (await metadata.json())["file"]
    assert metadata_file["name"] == "test.txt"
    assert metadata_file["size"] == len(b"hello gateway")

    download = await gateway_client.get(item["download_url"], headers=await auth_headers(gateway_client))
    assert download.status == 200
    assert await download.read() == b"hello gateway"
    assert download.headers["Content-Disposition"].startswith("attachment;")


@pytest.mark.asyncio
async def test_audio_upload_is_transcribed_on_server(gateway_client: TestClient):
    form = FormData()
    form.add_field("file", io.BytesIO(b"audio-bytes"), filename="voice.m4a", content_type="audio/mp4")
    response = await gateway_client.post(
        "/api/audio/transcriptions",
        data=form,
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    body = await response.json()
    assert body["transcript"] == "测试语音"
    assert body["file"]["mime_type"] == "audio/mp4"
    assert body["file"]["category"] == "audio"
    assert "server_path" not in body["file"]

    files = await gateway_client.get("/api/admin/files", headers=await auth_headers(gateway_client))
    audio = await gateway_client.get("/api/admin/audio", headers=await auth_headers(gateway_client))
    assert (await files.json())["data"] == []
    assert len((await audio.json())["data"]) == 1


@pytest.mark.asyncio
async def test_ephemeral_audio_is_removed_after_transcription(gateway_client: TestClient):
    form = FormData()
    form.add_field("file", io.BytesIO(b"audio-bytes"), filename="call.wav", content_type="audio/wav")
    form.add_field("ephemeral", "true")

    response = await gateway_client.post(
        "/api/audio/transcriptions",
        data=form,
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    audio = await gateway_client.get("/api/admin/audio", headers=await auth_headers(gateway_client))
    assert (await audio.json())["data"] == []


@pytest.mark.asyncio
async def test_ephemeral_audio_is_removed_when_transcription_fails(
    failing_transcription_gateway_client: TestClient,
):
    form = FormData()
    form.add_field("ephemeral", "true")
    form.add_field("file", io.BytesIO(b"audio-bytes"), filename="call.wav", content_type="audio/wav")

    response = await failing_transcription_gateway_client.post(
        "/api/audio/transcriptions",
        data=form,
        headers=await auth_headers(failing_transcription_gateway_client),
    )

    assert response.status == 503
    audio = await failing_transcription_gateway_client.get(
        "/api/admin/audio",
        headers=await auth_headers(failing_transcription_gateway_client),
    )
    assert (await audio.json())["data"] == []


@pytest.mark.asyncio
async def test_ephemeral_audio_is_removed_when_transcriber_raises(
    tmp_path: Path,
    upstream_client: TestClient,
):
    def failed_transcriber(_path: str):
        raise RuntimeError("private transcriber failure")

    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        transcribe_audio=failed_transcriber,
    )
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("ephemeral", "true")
        form.add_field("file", io.BytesIO(b"audio-bytes"), filename="call.wav", content_type="audio/wav")
        response = await client.post(
            "/api/audio/transcriptions",
            data=form,
            headers=await auth_headers(client),
        )

        assert response.status == 503
        assert (await response.json())["error"]["code"] == "transcription_failed"
        assert client.app[MEDIA_STORE_KEY].list_files("audio") == []


@pytest.mark.asyncio
async def test_ephemeral_audio_is_removed_when_transcription_times_out(
    tmp_path: Path,
    upstream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    monkeypatch.setattr(gateway_app, "TRANSCRIPTION_TIMEOUT_SECONDS", 0.01)
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        transcribe_audio=lambda _path: time.sleep(0.05) or {
            "success": True,
            "transcript": "late",
        },
    )
    async with TestClient(TestServer(app)) as client:
        store = client.app[MEDIA_STORE_KEY]
        real_delete = store.delete
        delete_attempts = 0

        def delete_after_simulated_windows_lock(file_id: str) -> bool:
            nonlocal delete_attempts
            delete_attempts += 1
            if delete_attempts == 1:
                raise PermissionError("simulated open Windows file handle")
            return real_delete(file_id)

        store.delete = delete_after_simulated_windows_lock
        form = FormData()
        form.add_field("ephemeral", "true")
        form.add_field("file", io.BytesIO(b"audio-bytes"), filename="call.wav", content_type="audio/wav")
        response = await client.post(
            "/api/audio/transcriptions",
            data=form,
            headers=await auth_headers(client),
        )

        assert response.status == 504
        assert (await response.json())["error"]["code"] == "transcription_timeout"
        cleanup_tasks = tuple(client.app[EPHEMERAL_CLEANUP_TASKS_KEY])
        assert cleanup_tasks
        await asyncio.wait_for(asyncio.gather(*cleanup_tasks), timeout=1)
        assert delete_attempts >= 2
        assert client.app[MEDIA_STORE_KEY].list_files("audio") == []


@pytest.mark.asyncio
async def test_ephemeral_audio_is_removed_when_transcription_is_cancelled(tmp_path: Path):
    import nexus_gateway.app as gateway_app

    store = MediaStore(tmp_path / "media", max_upload_bytes=1024, min_free_disk_bytes=0)
    transcriber_started = threading.Event()
    release_transcriber = threading.Event()

    def blocking_transcriber(_path: str):
        transcriber_started.set()
        release_transcriber.wait(timeout=1)
        return {"success": True, "transcript": "late"}

    class Field:
        def __init__(self, name: str, *, value: str = "", data: bytes = b""):
            self.name = name
            self.filename = "call.wav" if name == "file" else None
            self.headers = {"Content-Type": "audio/wav"}
            self.value = value
            self.data = data
            self.read = False

        async def text(self) -> str:
            return self.value

        async def read_chunk(self, size: int) -> bytes:
            if self.read:
                return b""
            self.read = True
            return self.data

    class Request:
        app = {
            MEDIA_STORE_KEY: store,
            TRANSCRIBE_AUDIO_KEY: blocking_transcriber,
            EPHEMERAL_CLEANUP_TASKS_KEY: set(),
        }

        async def multipart(self):
            fields = [Field("ephemeral", value="true"), Field("file", data=b"audio")]

            class Reader:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if not fields:
                        raise StopAsyncIteration
                    return fields.pop(0)

            return Reader()

    task = asyncio.create_task(gateway_app.transcribe_upload(Request()))
    try:
        assert await asyncio.to_thread(transcriber_started.wait, 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.list_files("audio") != []
        release_transcriber.set()
        cleanup_tasks = tuple(Request.app[EPHEMERAL_CLEANUP_TASKS_KEY])
        assert cleanup_tasks
        await asyncio.wait_for(asyncio.gather(*cleanup_tasks), timeout=1)
        assert store.list_files("audio") == []
    finally:
        release_transcriber.set()


@pytest.mark.asyncio
@pytest.mark.parametrize(("voice_id", "edge_voice"), [
    ("xiaoxiao", "zh-CN-XiaoxiaoNeural"),
    ("xiaoyi", "zh-CN-XiaoyiNeural"),
    ("yunxi", "zh-CN-YunxiNeural"),
    ("yunjian", "zh-CN-YunjianNeural"),
])
async def test_speech_endpoint_returns_selected_voice_audio(
    speech_gateway_client: TestClient,
    voice_id: str,
    edge_voice: str,
):
    response = await speech_gateway_client.post(
        "/api/audio/speech",
        json={"text": "你好 Nexus", "voice": voice_id},
        headers=await auth_headers(speech_gateway_client),
    )

    assert response.status == 200
    assert response.content_type == "audio/mpeg"
    assert response.headers["X-Nexus-Voice"] == voice_id
    assert await response.read() == f"{edge_voice}:你好 Nexus".encode("utf-8")


@pytest.mark.asyncio
async def test_speech_endpoint_rejects_unknown_voice(gateway_client: TestClient):
    response = await gateway_client.post(
        "/api/audio/speech",
        json={"text": "你好", "voice": "unknown"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "unsupported_voice"


@pytest.mark.asyncio
async def test_speech_endpoint_rejects_oversized_synthesizer_output(
    tmp_path: Path,
    upstream_client: TestClient,
):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        synthesize_speech_audio=lambda _text, _voice: b"123456789",
        speech_max_audio_bytes=8,
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/audio/speech",
            json={"text": "测试", "voice": "xiaoxiao"},
            headers=await auth_headers(client),
        )
        payload = await response.json()

    assert response.status == 502
    assert payload["error"]["code"] == "speech_response_too_large"


@pytest.mark.asyncio
async def test_speech_endpoint_limits_concurrent_synthesis(
    tmp_path: Path,
    upstream_client: TestClient,
):
    active = 0
    maximum_active = 0
    started_count = 0
    two_started = asyncio.Event()
    release = asyncio.Event()

    async def synthesize(_text: str, _voice: str) -> bytes:
        nonlocal active, maximum_active, started_count
        active += 1
        started_count += 1
        maximum_active = max(maximum_active, active)
        if started_count >= 2:
            two_started.set()
        try:
            await release.wait()
            return b"audio"
        finally:
            active -= 1

    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        credentials_path=tmp_path / "auth.json",
        synthesize_speech_audio=synthesize,
        speech_max_concurrency=2,
    )
    async with TestClient(TestServer(app)) as client:
        headers = await auth_headers(client)
        requests = [
            asyncio.create_task(client.post(
                "/api/audio/speech",
                json={"text": f"测试 {index}", "voice": "xiaoxiao"},
                headers=headers,
            ))
            for index in range(3)
        ]
        try:
            await asyncio.wait_for(two_started.wait(), timeout=1)
            await asyncio.sleep(0.05)
            assert started_count == 2
            assert maximum_active == 2
        finally:
            release.set()
        responses = await asyncio.gather(*requests)
        assert [response.status for response in responses] == [200, 200, 200]
        assert [await response.read() for response in responses] == [b"audio"] * 3
        assert maximum_active == 2


@pytest.mark.asyncio
async def test_packaged_gateway_reports_transcription_unavailable_without_http_capability(
    tmp_path: Path,
    upstream_client: TestClient,
):
    upstream_client.capabilities_state.update({"status": 404, "payload": {}})
    app = create_http_transcription_gateway(tmp_path, upstream_client)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("file", io.BytesIO(b"audio-bytes"), filename="voice.m4a", content_type="audio/mp4")
        response = await client.post(
            "/api/audio/transcriptions",
            data=form,
            headers=await auth_headers(client),
        )

        raw = await response.text()
        assert response.status == 501
        assert json.loads(raw)["error"]["code"] == "transcription_unavailable"
        assert "upstream-secret" not in raw
        assert upstream_client.audio_requests == []


@pytest.mark.asyncio
async def test_packaged_gateway_does_not_leak_key_when_capability_auth_fails(
    tmp_path: Path,
    upstream_client: TestClient,
):
    upstream_client.expected_auth["token"] = "different-secret"
    app = create_http_transcription_gateway(tmp_path, upstream_client)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("file", io.BytesIO(b"audio-bytes"), filename="voice.m4a", content_type="audio/mp4")
        response = await client.post(
            "/api/audio/transcriptions",
            data=form,
            headers=await auth_headers(client),
        )

        raw = await response.text()
        assert response.status == 502
        assert json.loads(raw)["error"]["code"] == "hermes_auth_failed"
        assert "upstream-secret" not in raw
        assert "different-secret" not in raw


@pytest.mark.asyncio
async def test_packaged_gateway_uses_only_capability_advertised_http_transcription(
    tmp_path: Path,
    upstream_client: TestClient,
):
    upstream_client.capabilities_state.update({
        "status": 200,
        "payload": {
            "features": {"audio_api": True},
            "endpoints": {"audio_transcriptions": {"path": "/v1/audio/transcriptions"}},
        },
    })
    app = create_http_transcription_gateway(tmp_path, upstream_client)
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("file", io.BytesIO(b"audio-bytes"), filename="voice.m4a", content_type="audio/mp4")
        response = await client.post(
            "/api/audio/transcriptions",
            data=form,
            headers=await auth_headers(client),
        )

        assert response.status == 200, await response.text()
        payload = await response.json()
        assert payload["transcript"] == "HTTP 语音转写"
        assert payload["provider"] == "test-http"
        assert upstream_client.audio_requests == [{
            "authorization": "Bearer upstream-secret",
            "filename": "voice.m4a",
            "content": b"audio-bytes",
            "fields": {
                "language": "zh",
                "prompt": (
                    "普通话对话，请使用简体中文，并根据语义添加逗号、句号和问号。"
                    "专有名词：Nexus、Hermes、Android、Gateway、API、模型、会话、定时任务。"
                ),
                "temperature": "0",
            },
        }]


def mobile_client_context() -> dict[str, object]:
    return {
        "platform": "android",
        "form_factor": "phone",
        "supports_direct_local_paths": False,
        "supports_drag_and_drop": False,
    }


@pytest.mark.asyncio
async def test_mobile_client_context_is_consumed_and_forwarded_as_ephemeral_system_message(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "手机操作", "client_context": mobile_client_context()},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == {
        "message": "手机操作",
        "system_message": MOBILE_CLIENT_SYSTEM_MESSAGE,
    }
    assert "client_context" not in upstream_client.captured_chat


@pytest.mark.asyncio
async def test_mobile_client_context_merges_existing_system_message(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "保留提示",
            "system_message": "原有系统提示",
            "client_context": mobile_client_context(),
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat["system_message"] == (
        f"{MOBILE_CLIENT_SYSTEM_MESSAGE}\n\n原有系统提示"
    )


@pytest.mark.asyncio
async def test_mobile_client_context_is_applied_to_non_streaming_chat_proxy(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "普通请求", "client_context": mobile_client_context()},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == {
        "message": "普通请求",
        "system_message": MOBILE_CLIENT_SYSTEM_MESSAGE,
    }


@pytest.mark.asyncio
async def test_non_mobile_chat_proxy_keeps_the_existing_request_body(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    body = {"message": "兼容旧客户端", "system_message": "既有提示", "custom": {"enabled": True}}
    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json=body,
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == body


@pytest.mark.asyncio
async def test_mobile_client_context_is_forwarded_on_selected_native_model_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "选择模型",
            "provider": "openai-codex",
            "model": "gpt-5.6",
            "client_context": mobile_client_context(),
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_model_lock == {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "require_model_lock": False,
    }
    assert upstream_client.captured_chat == {
        "message": "选择模型",
        "system_message": MOBILE_CLIENT_SYSTEM_MESSAGE,
    }
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_mobile_file_attachment_is_labeled_as_phone_origin(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    form = FormData()
    form.add_field("file", io.BytesIO("手机资料".encode()), filename="phone-note.txt", content_type="text/plain")
    upload = await gateway_client.post(
        "/api/uploads",
        data=form,
        headers=await auth_headers(gateway_client),
    )
    file_id = (await upload.json())["file"]["id"]

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "读取附件",
            "attachment_ids": [file_id],
            "client_context": mobile_client_context(),
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    attachment_text = upstream_client.captured_chat["message"][1]["text"]
    assert "来源：Nexus Android 手机端附件" in attachment_text
    assert "手机资料" in attachment_text
    assert "服务器路径" not in attachment_text


@pytest.mark.asyncio
async def test_selected_provider_and_model_use_native_hermes_session_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "hello",
            "persona_model": "profile-a",
            "provider": "openai-codex",
            "model": "gpt-5.6",
            "model_options": {"reasoning_effort": "medium"},
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    stream = await response.text()
    assert "event: assistant.delta" in stream
    assert "\u6a21\u578b\u56de\u590d" in stream
    assert '"route_source": "session_model_lock"' in stream
    completed_block = next(
        block for block in stream.split("\n\n")
        if block.startswith("event: run.completed")
    )
    assert '"model": "gpt-5.6"' in completed_block
    assert stream.rstrip().endswith("event: done\ndata: {}")
    assert upstream_client.captured_model_lock == {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "require_model_lock": False,
        "model_options": {
            "reasoning_effort": "medium",
        },
    }
    assert upstream_client.captured_chat == {
        "message": "hello",
        "model_options": {"reasoning_effort": "medium"},
    }
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_selected_model_catalog_timeout_is_first_safe_sse_error(
    gateway_client: TestClient,
    upstream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    release = asyncio.Event()
    upstream_client.model_options_state["release"] = release
    monkeypatch.setattr(
        gateway_app,
        "MODEL_CATALOG_TIMEOUT",
        gateway_app.ClientTimeout(total=0.05, connect=0.02, sock_read=0.02),
    )
    try:
        response = await gateway_client.post(
            "/api/sessions/session-1/chat/stream",
            json={"message": "timeout", "provider": "openai-codex", "model": "gpt-5.6"},
            headers=await auth_headers(gateway_client),
        )
        first_event = (await asyncio.wait_for(
            response.content.readuntil(b"\n\n"),
            timeout=1,
        )).decode("utf-8")
        stream = first_event + await response.text()
    finally:
        release.set()

    assert response.status == 200
    assert first_event.startswith("event: error\n")
    assert "model_catalog_timeout" in first_event
    assert "读取 Hermes 模型列表超时" in first_event
    assert "upstream-secret" not in stream
    assert upstream_client.captured_model_lock == {}
    assert upstream_client.captured_chat == {}
    status = gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")
    assert status["status"] == "failed"
    assert status["active"] is False
    assert upstream_client.model_options_state["requests"] == 2


@pytest.mark.asyncio
async def test_selected_model_catalog_retries_one_cold_start_timeout(
    gateway_client: TestClient,
    upstream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    upstream_client.model_options_state["timeout_requests"] = 1
    monkeypatch.setattr(
        gateway_app,
        "MODEL_CATALOG_TIMEOUT",
        gateway_app.ClientTimeout(total=0.05, connect=0.02, sock_read=0.02),
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "retry", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    stream = await response.text()

    assert response.status == 200
    assert "event: assistant.delta" in stream
    assert "model_catalog_timeout" not in stream
    assert upstream_client.model_options_state["requests"] == 2
    assert upstream_client.captured_model_lock["model"] == "gpt-5.6"
    assert upstream_client.captured_chat == {"message": "retry"}


@pytest.mark.asyncio
async def test_selected_model_lock_timeout_is_first_safe_sse_error(
    gateway_client: TestClient,
    upstream_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    release = asyncio.Event()
    upstream_client.model_lock_state["release"] = release
    monkeypatch.setattr(
        gateway_app,
        "MODEL_LOCK_TIMEOUT",
        gateway_app.ClientTimeout(total=0.05, connect=0.02, sock_read=0.02),
    )
    try:
        response = await gateway_client.post(
            "/api/sessions/session-1/chat/stream",
            json={"message": "timeout", "provider": "openai-codex", "model": "gpt-5.6"},
            headers=await auth_headers(gateway_client),
        )
        first_event = (await asyncio.wait_for(
            response.content.readuntil(b"\n\n"),
            timeout=1,
        )).decode("utf-8")
        stream = first_event + await response.text()
    finally:
        release.set()

    assert response.status == 200
    assert first_event.startswith("event: error\n")
    assert "model_lock_timeout" in first_event
    assert "Hermes 应用所选模型超时" in first_event
    assert "upstream-secret" not in stream
    assert upstream_client.captured_model_lock["model"] == "gpt-5.6"
    assert upstream_client.captured_chat == {}
    status = gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")
    assert status["status"] == "failed"
    assert status["active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actual_runtime",
    [
        {},
        {"provider": "openai-codex", "model": "wrong-model"},
        {"provider": "wrong-provider", "model": "gpt-5.6"},
    ],
)
async def test_selected_model_stream_rejects_missing_or_mismatched_actual_runtime(
    gateway_client: TestClient,
    upstream_client: TestClient,
    actual_runtime: dict,
):
    runtime = {
        "route_source": "session_model_lock",
        "model_lock": "confirmed",
        "requested": {"provider": "openai-codex", "model": "gpt-5.6"},
        **actual_runtime,
    }
    upstream_client.chat_state["stream"] = (
        f"event: run.started\ndata: {json.dumps({'runtime': runtime})}\n\n"
        'event: assistant.delta\ndata: {"delta":"must not escape"}\n\n'
        'event: run.completed\ndata: {"completed":true}\n\n'
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "verify", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    stream = await response.text()

    assert response.status == 200
    assert stream.split("\n\n", 1)[0].startswith("event: error\n")
    assert "model_lock_failed" in stream
    assert "must not escape" not in stream
    assert "run.completed" not in stream
    status = gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")
    assert status["status"] == "failed"
    assert status["active"] is False


@pytest.mark.asyncio
async def test_selected_model_stream_rechecks_completed_runtime(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    started_runtime = {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "route_source": "session_model_lock",
        "model_lock": "confirmed",
        "requested": {"provider": "openai-codex", "model": "gpt-5.6"},
    }
    completed_runtime = {**started_runtime, "model": "wrong-model"}
    upstream_client.chat_state["stream"] = (
        f"event: run.started\ndata: {json.dumps({'runtime': started_runtime})}\n\n"
        'event: assistant.delta\ndata: {"delta":"partial"}\n\n'
        f"event: run.completed\ndata: {json.dumps({'runtime': completed_runtime})}\n\n"
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "verify", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    stream = await response.text()

    assert "partial" in stream
    assert "model_lock_failed" in stream
    assert "event: run.completed" not in stream
    assert gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")["status"] == "failed"


@pytest.mark.asyncio
async def test_selected_model_accepts_hermes_pending_actual_runtime_after_lock_confirmation(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    pending_runtime = {
        "provider": "",
        "model": "",
        "route_source": "session_model_lock",
        "model_lock": "accepted",
        "requested": {"provider": "openai-codex", "model": "gpt-5.6"},
    }
    upstream_client.chat_state["stream"] = (
        f"event: run.started\ndata: {json.dumps({'runtime': pending_runtime})}\n\n"
        'event: assistant.delta\ndata: {"delta":"confirmed reply"}\n\n'
        'event: assistant.completed\ndata: {"content":"confirmed reply"}\n\n'
        'event: run.completed\ndata: {"completed":true}\n\n'
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "verify", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    stream = await response.text()

    assert response.status == 200
    assert "confirmed reply" in stream
    assert "model_lock_failed" not in stream
    assert gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")["status"] == "completed"


@pytest.mark.asyncio
async def test_selected_model_pending_runtime_still_requires_exact_requested_model(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    pending_runtime = {
        "provider": "",
        "model": "",
        "route_source": "session_model_lock",
        "model_lock": "accepted",
        "requested": {"provider": "openai-codex", "model": "wrong-model"},
    }
    upstream_client.chat_state["stream"] = (
        f"event: run.started\ndata: {json.dumps({'runtime': pending_runtime})}\n\n"
        'event: assistant.delta\ndata: {"delta":"must not escape"}\n\n'
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "verify", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    stream = await response.text()

    assert "model_lock_failed" in stream
    assert "must not escape" not in stream
    assert gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")["status"] == "failed"


@pytest.mark.asyncio
async def test_user_config_provider_alias_is_completed_after_real_model_output(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.model_options_state["payload"] = {
        "providers": [{
            "slug": "alight",
            "name": "Alight",
            "source": "user-config",
            "is_user_defined": True,
            "authenticated": True,
            "models": ["gpt-5.6-terra", "gpt-5.6-sol"],
        }],
        "model": "gpt-5.6-sol",
        "provider": "custom:alight",
    }
    upstream_client.chat_state["stream"] = (
        'event: run.started\ndata: {"runtime":{"route_source":"session_model_lock",'
        '"model_lock":"accepted","provider":"custom","model":"gpt-5.6-terra",'
        '"requested":{"provider":"alight",'
        '"model":"gpt-5.6-terra"}}}\n\n'
        'event: assistant.delta\ndata: {"delta":"terra reply"}\n\n'
        'event: error\ndata: {"message":"confirmed model lock runtime mismatch: '
        'expected provider=alight model=gpt-5.6-terra; actual provider=custom '
        'model=gpt-5.6-terra"}\n\n'
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello", "provider": "alight", "model": "gpt-5.6-terra"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    stream = await response.text()
    assert "event: error" not in stream
    assert "terra reply" in stream
    assert stream.count("event: run.completed") == 1
    assert '"provider":"custom"' in stream
    assert '"model":"gpt-5.6-terra"' in stream
    assert '"requested":{"provider":"alight","model":"gpt-5.6-terra"}' in stream
    assert upstream_client.captured_model_lock == {
        "provider": "alight",
        "model": "gpt-5.6-terra",
        "require_model_lock": False,
    }
    assert upstream_client.captured_chat == {"message": "hello"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_user_config_runtime_model_mismatch_remains_an_error(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.model_options_state["payload"] = {
        "providers": [{
            "slug": "alight",
            "source": "user-config",
            "authenticated": True,
            "models": ["gpt-5.6-terra", "gpt-5.6-sol"],
        }],
        "model": "gpt-5.6-sol",
        "provider": "custom:alight",
    }
    upstream_client.chat_state["stream"] = (
        'event: run.started\ndata: {"runtime":{"route_source":"session_model_lock",'
        '"model_lock":"accepted","provider":"custom","model":"gpt-5.6-sol",'
        '"requested":{"provider":"alight",'
        '"model":"gpt-5.6-terra"}}}\n\n'
        'event: assistant.delta\ndata: {"delta":"wrong model reply"}\n\n'
        'event: error\ndata: {"message":"confirmed model lock runtime mismatch: '
        'expected provider=alight model=gpt-5.6-terra; actual provider=custom '
        'model=gpt-5.6-sol"}\n\n'
        'event: done\ndata: {}\n\n'
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello", "provider": "alight", "model": "gpt-5.6-terra"},
        headers=await auth_headers(gateway_client),
    )

    stream = await response.text()
    assert "event: error" in stream
    assert "model_lock_failed" in stream
    assert "Hermes 未确认所选模型" in stream
    assert "actual provider=custom model=gpt-5.6-sol" not in stream
    assert "event: run.completed" not in stream


@pytest.mark.asyncio
async def test_unknown_selected_model_is_rejected_before_lock_or_chat(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.model_options_state["payload"] = {
        "providers": [{
            "slug": "alight",
            "source": "user-config",
            "authenticated": True,
            "models": ["gpt-5.6-terra"],
        }],
        "model": "gpt-5.6-terra",
        "provider": "custom:alight",
    }

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello", "provider": "alight", "model": "invalid-model"},
        headers=await auth_headers(gateway_client),
    )

    stream = await response.text()
    assert "event: error" in stream
    assert '"code": "model_not_available"' in stream
    assert "event: run.completed" not in stream
    assert upstream_client.captured_model_lock == {}
    assert upstream_client.captured_chat == {}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_non_streaming_unknown_model_is_rejected_before_lock_or_chat(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.model_options_state["payload"] = {
        "providers": [{
            "slug": "alight",
            "source": "user-config",
            "authenticated": True,
            "models": ["gpt-5.6-terra"],
        }],
        "model": "gpt-5.6-terra",
        "provider": "custom:alight",
    }

    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "hello", "provider": "alight", "model": "invalid-model"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "model_not_available"
    assert upstream_client.captured_model_lock == {}
    assert upstream_client.captured_chat == {}


@pytest.mark.asyncio
async def test_session_model_lock_is_forwarded_and_acknowledged(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    selection = {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "require_model_lock": True,
        "model_options": {"reasoning_effort": "high"},
    }
    response = await gateway_client.post(
        "/api/sessions/session-1/model",
        json=selection,
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    payload = await response.json()
    assert upstream_client.captured_model_lock == selection
    assert payload["runtime"]["provider"] == "openai-codex"
    assert payload["runtime"]["model"] == "gpt-5.6"


@pytest.mark.asyncio
async def test_reasoning_effort_is_applied_to_selected_session_model(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "reason",
            "provider": "openai-codex",
            "model": "gpt-5.6",
            "model_options": {"reasoning_effort": "HIGH"},
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_model_lock["model_options"] == {
        "reasoning_effort": "high",
    }
    assert upstream_client.captured_chat["model_options"] == {"reasoning_effort": "high"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_reasoning_effort_is_forwarded_to_native_session_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "native reason", "reasoning_effort": "minimal"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == {
        "message": "native reason",
        "model_options": {"reasoning_effort": "minimal"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"level": "high"}, ["high"], "unsupported"])
async def test_invalid_reasoning_effort_returns_400_without_contacting_upstream(
    gateway_client: TestClient,
    upstream_client: TestClient,
    value,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "invalid reason", "model_options": {"reasoning_effort": value}},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "invalid_reasoning_effort"
    assert upstream_client.captured_completion == {}
    assert upstream_client.captured_chat == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"message": "provider only", "provider": "openai-codex"}, "invalid_model_selection"),
        (
            {
                "message": "bad lock",
                "provider": "openai-codex",
                "model": "gpt-5.6",
                "require_model_lock": "true",
            },
            "invalid_model_selection",
        ),
        ({"message": "bad options", "model_options": ["medium"]}, "invalid_model_options"),
    ],
)
async def test_invalid_native_model_config_returns_400_without_upstream_request(
    gateway_client: TestClient,
    upstream_client: TestClient,
    body,
    code,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json=body,
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == code
    assert upstream_client.captured_chat == {}


@pytest.mark.asyncio
async def test_non_streaming_chat_forwards_native_model_selection(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={
            "message": "non-streaming",
            "provider": "openai-codex",
            "model": "gpt-5.6",
            "model_options": {"reasoning_effort": "medium"},
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_model_lock == {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "model_options": {"reasoning_effort": "medium"},
        "require_model_lock": False,
    }
    assert upstream_client.captured_chat == {
        "message": "non-streaming",
        "model_options": {"reasoning_effort": "medium"},
    }


@pytest.mark.asyncio
async def test_selected_model_chat_preserves_verified_json_response(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    runtime = {
        "provider": "openai-codex",
        "model": "gpt-5.6",
        "route_source": "session_model_lock",
        "model_lock": "confirmed",
        "requested": {"provider": "openai-codex", "model": "gpt-5.6"},
    }
    upstream_client.chat_state.update({
        "status": 202,
        "json_payload": {"answer": "verified", "runtime": runtime},
    })

    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "json", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 202
    assert response.headers["X-Upstream-Contract"] == "preserved"
    assert await response.json() == {"answer": "verified", "runtime": runtime}
    assert gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "runtime",
    [
        None,
        {
            "provider": "openai-codex",
            "model": "wrong-model",
            "route_source": "session_model_lock",
            "model_lock": "confirmed",
            "requested": {"provider": "openai-codex", "model": "gpt-5.6"},
        },
    ],
)
async def test_selected_model_chat_rejects_unverified_json_runtime(
    gateway_client: TestClient,
    upstream_client: TestClient,
    runtime,
):
    upstream_client.chat_state["json_payload"] = {
        "answer": "must-not-escape",
        "runtime": runtime,
        "debug": "upstream-secret at C:/private",
    }

    response = await gateway_client.post(
        "/api/sessions/session-1/chat",
        json={"message": "json", "provider": "openai-codex", "model": "gpt-5.6"},
        headers=await auth_headers(gateway_client),
    )
    payload = await response.json()

    assert response.status == 502
    assert payload["error"]["code"] == "model_lock_failed"
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "must-not-escape" not in serialized
    assert "upstream-secret" not in serialized
    assert "C:/private" not in serialized
    status = gateway_client.server.app[RUN_TRACKER_KEY].status("session-1")
    assert status["status"] == "failed"
    assert status["active"] is False


@pytest.mark.asyncio
async def test_model_only_legacy_request_stays_on_native_session_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "legacy", "model": "route-legacy"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == {"message": "legacy", "model": "route-legacy"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_chat_without_model_keeps_native_session_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "native"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    assert "\u6536\u5230" in await response.text()
    assert upstream_client.captured_chat == {"message": "native"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_selection",
    [
        {},
        {"provider": "openai-codex", "model": "gpt-5.6"},
    ],
)
async def test_provider_setup_cli_error_is_translated_for_mobile(
    gateway_client: TestClient,
    upstream_client: TestClient,
    model_selection: dict,
):
    upstream_client.chat_state["stream"] = (
        "event: error\n"
        "data: {\"message\":\"No LLM provider configured. Run 'hermes model' to select a provider, "
        "or run 'hermes setup' for first-time configuration.\"}\n\n"
        "event: done\ndata: {}\n\n"
    )

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello", **model_selection},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    stream = await response.text()
    assert "Hermes 尚未配置可用的调用模型，请先在 Hermes 中完成模型配置" in stream
    assert '"code":"model_not_configured"' in stream
    assert "hermes model" not in stream
    assert "hermes setup" not in stream


@pytest.mark.asyncio
async def test_chat_without_model_does_not_guess_or_override_hermes_default(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.model_options_state["payload"] = {
        "providers": [{
            "slug": "cpa",
            "is_current": True,
            "is_user_defined": True,
            "authenticated": True,
            "models": ["gpt-5.6-terra", "gpt-5.6-sol"],
        }],
        "model": "gpt-5.6-terra",
        "provider": "custom:cpa",
    }

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "default model"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    assert "收到" in await response.text()
    assert upstream_client.captured_chat == {"message": "default model"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_legacy_persona_model_is_not_forwarded_as_an_inference_model(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "persona", "persona_model": "profile-a"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_chat == {"message": "persona"}
    assert upstream_client.captured_completion == {}


@pytest.mark.asyncio
async def test_chat_attachment_ids_become_hermes_readable_content(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    image_form = FormData()
    image_form.add_field("file", io.BytesIO(b"\x89PNG\r\n\x1a\nimage"), filename="photo.png", content_type="image/png")
    image_upload = await gateway_client.post("/api/uploads", data=image_form, headers=await auth_headers(gateway_client))
    image_id = (await image_upload.json())["file"]["id"]

    text_form = FormData()
    text_form.add_field("file", io.BytesIO("资料内容".encode()), filename="note.txt", content_type="text/plain")
    text_upload = await gateway_client.post("/api/uploads", data=text_form, headers=await auth_headers(gateway_client))
    text_file = (await text_upload.json())["file"]

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "请处理附件", "attachment_ids": [image_id, text_file["id"]]},
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    assert "收到" in await response.text()

    captured = upstream_client.captured_chat
    assert "attachment_ids" not in captured
    parts = captured["message"]
    assert parts[0] == {"type": "text", "text": "请处理附件"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert "服务器路径" not in parts[2]["text"]
    assert str(gateway_client.server.app[MEDIA_STORE_KEY].root) not in parts[2]["text"]
    assert "资料内容" in parts[2]["text"]
    assert "下载地址：http://" in parts[2]["text"]
    assert "signature=" in parts[2]["text"]


@pytest.mark.asyncio
async def test_binary_attachment_gives_hermes_a_signed_read_only_download(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    form = FormData()
    form.add_field(
        "file",
        io.BytesIO(b"nexus-binary-content"),
        filename="archive.bin",
        content_type="application/octet-stream",
    )
    upload = await gateway_client.post(
        "/api/uploads",
        data=form,
        headers=await auth_headers(gateway_client),
    )
    file_id = (await upload.json())["file"]["id"]

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "检查二进制附件", "attachment_ids": [file_id]},
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    await response.text()

    attachment_text = upstream_client.captured_chat["message"][1]["text"]
    download_url = next(
        line.removeprefix("下载地址：")
        for line in attachment_text.splitlines()
        if line.startswith("下载地址：")
    )
    assert file_id in download_url
    assert "读取说明：这是短时有效的只读附件地址" in attachment_text

    signed_path = download_url[download_url.index("/api/files/"):]
    download = await gateway_client.get(signed_path)
    assert download.status == 200
    assert await download.read() == b"nexus-binary-content"
    assert download.headers["Content-Type"] == "application/octet-stream"

    tampered_path = signed_path.replace("signature=", "signature=0")
    rejected = await gateway_client.get(tampered_path)
    assert rejected.status == 401


@pytest.mark.asyncio
async def test_chat_rejects_too_many_attachments_before_lookup(gateway_client: TestClient):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "too many",
            "attachment_ids": [f"{index:032x}" for index in range(17)],
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "too_many_attachments"


@pytest.mark.asyncio
async def test_chat_rejects_duplicate_attachment_before_reading_image(
    gateway_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    form = FormData()
    form.add_field("file", io.BytesIO(b"image"), filename="duplicate.png", content_type="image/png")
    upload = await gateway_client.post(
        "/api/uploads",
        data=form,
        headers=await auth_headers(gateway_client),
    )
    file_id = (await upload.json())["file"]["id"]
    original_read_bytes = Path.read_bytes
    image_reads = 0

    def guarded_read_bytes(path: Path):
        nonlocal image_reads
        if path.suffix == ".png":
            image_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "duplicate", "attachment_ids": [file_id, file_id]},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "duplicate_attachment_id"
    assert image_reads == 0


@pytest.mark.asyncio
async def test_chat_rejects_aggregate_attachment_size_before_reading_images(
    gateway_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
):
    import nexus_gateway.app as gateway_app

    headers = await auth_headers(gateway_client)
    file_ids = []
    for name in ("one.png", "two.png"):
        form = FormData()
        form.add_field("file", io.BytesIO(b"1234"), filename=name, content_type="image/png")
        upload = await gateway_client.post("/api/uploads", data=form, headers=headers)
        file_ids.append((await upload.json())["file"]["id"])
    monkeypatch.setattr(gateway_app, "MAX_CHAT_ATTACHMENT_BYTES", 6)
    original_read_bytes = Path.read_bytes
    image_reads = 0

    def guarded_read_bytes(path: Path):
        nonlocal image_reads
        if path.suffix == ".png":
            image_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read_bytes)
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "large", "attachment_ids": file_ids},
        headers=headers,
    )

    assert response.status == 413
    assert (await response.json())["error"]["code"] == "attachments_too_large"
    assert image_reads == 0


@pytest.mark.asyncio
async def test_image_selected_as_file_stays_a_file_in_chat_and_history(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    form = FormData()
    form.add_field("file", io.BytesIO(b"png-file"), filename="diagram.png", content_type="image/png")
    upload = await gateway_client.post("/api/uploads", data=form, headers=headers)
    file_id = (await upload.json())["file"]["id"]

    sent = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "", "attachment_ids": [file_id], "attachment_kinds": {file_id: "file"}},
        headers=headers,
    )
    assert sent.status == 200
    await sent.text()

    assert upstream_client.captured_chat["message"][0]["type"] == "text"
    media_store = gateway_client.server.app[MEDIA_STORE_KEY]
    restored = media_store.enrich_session_messages(
        "session-1",
        {"data": [{"id": "u1", "role": "user", "content": upstream_client.captured_chat["message"]}]},
    )["data"][0]
    assert restored.get("nexus_images", []) == []
    assert restored["nexus_files"][0]["id"] == file_id


@pytest.mark.asyncio
async def test_same_text_attachments_restore_in_send_order(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    headers = await auth_headers(gateway_client)
    file_ids = []
    for name, content in (("one.txt", b"one"), ("two.txt", b"two")):
        form = FormData()
        form.add_field("file", io.BytesIO(content), filename=name, content_type="text/plain")
        uploaded = await gateway_client.post("/api/uploads", data=form, headers=headers)
        file_ids.append((await uploaded.json())["file"]["id"])
        sent = await gateway_client.post(
            "/api/sessions/session-1/chat/stream",
            json={"message": "相同文字", "attachment_ids": [file_ids[-1]]},
            headers=headers,
        )
        assert sent.status == 200
        await sent.text()

    media_store = gateway_client.server.app[MEDIA_STORE_KEY]
    payload = {
        "data": [
            {"id": "u1", "role": "user", "content": "相同文字"},
            {"id": "a1", "role": "assistant", "content": "一"},
            {"id": "u2", "role": "user", "content": "相同文字"},
        ]
    }
    restored = media_store.enrich_session_messages("session-1", payload)["data"]
    assert restored[0]["nexus_files"][0]["id"] == file_ids[0]
    assert restored[2]["nexus_files"][0]["id"] == file_ids[1]


def test_failed_chat_can_discard_the_pending_media_index(tmp_path: Path):
    store = MediaStore(tmp_path / "media", 1024)
    index = store.root / "session_media.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({
        "session-1": [
            {"marker": "first", "file_ids": ["one"]},
            {"marker": "second", "file_ids": ["two"]},
        ]
    }), encoding="utf-8")

    store.discard_last_session_media("session-1", ["two"])

    assert json.loads(index.read_text(encoding="utf-8")) == {
        "session-1": [{"marker": "first", "file_ids": ["one"]}]
    }


def test_session_media_index_rejects_hardlink_without_touching_external_file(tmp_path: Path):
    store = MediaStore(tmp_path / "media", 1024, min_free_disk_bytes=0)
    payload = store.root / "payload.txt"
    payload.write_text("payload", encoding="utf-8")
    item = StoredFile(
        id="a" * 32,
        name="payload.txt",
        mime_type="text/plain",
        size=7,
        sha256="0" * 64,
        created_at=time.time(),
        server_path=str(payload),
    )
    (store.root / f"{item.id}.json").write_text(
        json.dumps(item.__dict__), encoding="utf-8"
    )
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    os.link(external, store.root / "session_media.json")

    with pytest.raises(OSError, match="single-link"):
        store.record_session_media("session-1", [item.id])

    assert external.read_text(encoding="utf-8") == "{}"


def test_media_store_rejects_hardlinked_payload(tmp_path: Path):
    store = MediaStore(tmp_path / "media", 1024, min_free_disk_bytes=0)
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    payload = store.root / "payload.txt"
    os.link(external, payload)
    item = StoredFile(
        id="b" * 32,
        name="payload.txt",
        mime_type="text/plain",
        size=7,
        sha256="0" * 64,
        created_at=time.time(),
        server_path=str(payload),
    )
    (store.root / f"{item.id}.json").write_text(
        json.dumps(item.__dict__), encoding="utf-8"
    )

    assert store.get(item.id) is None


def test_run_status_atomic_write_does_not_follow_hardlink(tmp_path: Path):
    external = tmp_path / "outside.json"
    external.write_text("{}", encoding="utf-8")
    state_file = tmp_path / "run_status.json"
    os.link(external, state_file)
    tracker = RunTracker(state_file)

    with pytest.raises(OSError, match="hard-linked"):
        tracker.start("session-1", "run-1")

    assert external.read_text(encoding="utf-8") == "{}"
    assert state_file.read_text(encoding="utf-8") == "{}"


def test_atomic_write_rejects_runtime_data_root_replacement(tmp_path: Path):
    import nexus_gateway.app as gateway_app

    data_root = tmp_path / "nexus-data"
    original_root = tmp_path / "nexus-data.original"
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:9",
        upstream_token="upstream-secret",
        data_dir=data_root,
        storage_dir=data_root / "media",
    )
    data_root.rename(original_root)
    data_root.mkdir()
    try:
        with pytest.raises(OSError, match="identity changed"):
            gateway_app._secure_atomic_write(
                data_root / "run_status.json",
                "{}",
                app[gateway_app.MANAGED_PATH_GUARD_KEY],
            )
        assert not (data_root / "run_status.json").exists()
    finally:
        data_root.rmdir()
        original_root.rename(data_root)


def test_atomic_write_rejects_runtime_media_junction_replacement(tmp_path: Path):
    import nexus_gateway.app as gateway_app

    data_root = tmp_path / "nexus-data"
    media_root = data_root / "media"
    original_media = data_root / "media.original"
    outside = tmp_path / "outside-media"
    outside.mkdir()
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:9",
        upstream_token="upstream-secret",
        data_dir=data_root,
        storage_dir=media_root,
    )
    media_root.rename(original_media)
    if os.name == "nt":
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "$null = New-Item -ItemType Junction -Path $env:NEXUS_TEST_LINK -Target $env:NEXUS_TEST_TARGET",
            ],
            env={
                **os.environ,
                "NEXUS_TEST_LINK": str(media_root),
                "NEXUS_TEST_TARGET": str(outside),
            },
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, result.stderr
    else:
        media_root.symlink_to(outside, target_is_directory=True)
    try:
        with pytest.raises(OSError, match="redirected|identity changed"):
            gateway_app._secure_atomic_write(
                media_root / "session_media.json",
                "{}",
                app[gateway_app.MANAGED_PATH_GUARD_KEY],
            )
        assert list(outside.iterdir()) == []
    finally:
        if os.name == "nt":
            os.rmdir(media_root)
        else:
            media_root.unlink()
        original_media.rename(media_root)


@pytest.mark.asyncio
async def test_upload_rejects_oversized_file(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        max_upload_bytes=4,
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("file", io.BytesIO(b"12345"), filename="large.bin", content_type="application/octet-stream")
        response = await client.post("/api/uploads", data=form, headers=await auth_headers(client))
        assert response.status == 413


class CoordinatedUploadField:
    def __init__(
        self,
        content: bytes,
        started: asyncio.Barrier,
        release: asyncio.Event | None = None,
        after_chunk: asyncio.Barrier | None = None,
    ):
        self.filename = "concurrent.bin"
        self.headers = {"Content-Type": "application/octet-stream"}
        self._content = content
        self._started = started
        self._release = release
        self._after_chunk = after_chunk
        self._read = False

    async def read_chunk(self, size: int) -> bytes:
        if self._read:
            if self._after_chunk is not None:
                await self._after_chunk.wait()
            return b""
        self._read = True
        await self._started.wait()
        if self._release is not None:
            await self._release.wait()
        return self._content


class ChunkedUploadField:
    def __init__(self, chunks: list[bytes]):
        self.filename = "chunked.bin"
        self.headers = {"Content-Type": "application/octet-stream"}
        self._chunks = iter(chunks)

    async def read_chunk(self, size: int) -> bytes:
        return next(self._chunks, b"")


@pytest.mark.asyncio
async def test_concurrent_uploads_reserve_total_storage_quota(tmp_path: Path):
    store = MediaStore(
        tmp_path / "media",
        max_upload_bytes=10,
        max_total_storage_bytes=6,
        min_free_disk_bytes=0,
    )
    started = asyncio.Barrier(2)

    results = await asyncio.gather(
        store.save(CoordinatedUploadField(b"1234", started)),
        store.save(CoordinatedUploadField(b"5678", started)),
        return_exceptions=True,
    )

    assert sum(isinstance(result, StoredFile) for result in results) == 1
    failures = [result for result in results if isinstance(result, web.HTTPInsufficientStorage)]
    assert len(failures) == 1
    assert json.loads(failures[0].text)["error"]["code"] == "storage_quota_exceeded"
    assert sum(item["size"] for item in store.list_files()) == 4


@pytest.mark.asyncio
async def test_concurrent_uploads_reserve_disk_low_water_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "nexus_gateway.app.shutil.disk_usage",
        lambda _path: shutil._ntuple_diskusage(total=100, used=94, free=6),
    )
    store = MediaStore(
        tmp_path / "media",
        max_upload_bytes=10,
        max_total_storage_bytes=0,
        min_free_disk_bytes=2,
    )
    release_first = asyncio.Event()

    class HeldUploadField:
        filename = "first.bin"
        headers = {"Content-Type": "application/octet-stream"}
        reads = 0

        async def read_chunk(self, size: int) -> bytes:
            self.reads += 1
            if self.reads == 1:
                return b"1234"
            await release_first.wait()
            return b""

    first = asyncio.create_task(store.save(HeldUploadField()))
    while store._reserved_upload_bytes != 4:
        await asyncio.sleep(0)

    second = await asyncio.gather(
        store.save(ChunkedUploadField([b"5678"])),
        return_exceptions=True,
    )
    release_first.set()
    saved = await first

    assert isinstance(saved, StoredFile)
    assert isinstance(second[0], web.HTTPInsufficientStorage)
    assert json.loads(second[0].text)["error"]["code"] == "disk_space_low"


@pytest.mark.asyncio
async def test_failed_upload_releases_reserved_capacity(tmp_path: Path):
    store = MediaStore(
        tmp_path / "media",
        max_upload_bytes=6,
        max_total_storage_bytes=6,
        min_free_disk_bytes=0,
    )

    with pytest.raises(web.HTTPRequestEntityTooLarge):
        await store.save(ChunkedUploadField([b"1234", b"567"]))

    saved = await store.save(ChunkedUploadField([b"abcdef"]))
    assert saved.size == 6


@pytest.mark.asyncio
async def test_cancelled_upload_releases_reserved_capacity(tmp_path: Path):
    store = MediaStore(
        tmp_path / "media",
        max_upload_bytes=10,
        max_total_storage_bytes=6,
        min_free_disk_bytes=0,
    )
    first_chunk_read = asyncio.Event()
    release = asyncio.Event()

    class CancellableField:
        def __init__(self):
            self.filename = "cancelled.bin"
            self.headers = {"Content-Type": "application/octet-stream"}
            self.reads = 0

        async def read_chunk(self, size: int) -> bytes:
            self.reads += 1
            if self.reads == 1:
                first_chunk_read.set()
                return b"1234"
            await release.wait()
            return b""

    task = asyncio.create_task(store.save(CancellableField()))
    await first_chunk_read.wait()
    while store._reserved_upload_bytes != 4:
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    saved = await store.save(ChunkedUploadField([b"abcdef"]))
    assert saved.size == 6


@pytest.mark.asyncio
async def test_upload_rejects_when_total_storage_quota_would_be_exceeded(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        max_upload_bytes=10,
        max_total_storage_bytes=6,
        min_free_disk_bytes=0,
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        headers = await auth_headers(client)
        first = FormData()
        first.add_field("file", io.BytesIO(b"1234"), filename="first.bin")
        assert (await client.post("/api/uploads", data=first, headers=headers)).status == 201
        second = FormData()
        second.add_field("file", io.BytesIO(b"567"), filename="second.bin")
        response = await client.post("/api/uploads", data=second, headers=headers)
        assert response.status == 507
        assert (await response.json())["error"]["code"] == "storage_quota_exceeded"


@pytest.mark.asyncio
async def test_upload_rejects_when_disk_free_space_reaches_low_water_mark(
    tmp_path: Path, upstream_client: TestClient, monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        "nexus_gateway.app.shutil.disk_usage",
        lambda _path: shutil._ntuple_diskusage(total=1000, used=950, free=50),
    )
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        min_free_disk_bytes=50,
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        form = FormData()
        form.add_field("file", io.BytesIO(b"x"), filename="blocked.bin")
        response = await client.post("/api/uploads", data=form, headers=await auth_headers(client))
        assert response.status == 507
        assert (await response.json())["error"]["code"] == "disk_space_low"


@pytest.mark.asyncio
async def test_transcription_failure_does_not_expose_internal_error(tmp_path: Path, upstream_client: TestClient):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url=str(upstream_client.make_url("/")).rstrip("/"),
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        min_free_disk_bytes=0,
        transcribe_audio=lambda _path: {
            "success": False,
            "error": "secret=/data/private/token and host=internal.example",
        },
    )
    form = FormData()
    form.add_field("file", io.BytesIO(b"audio"), filename="voice.m4a", content_type="audio/mp4")
    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/audio/transcriptions", data=form, headers=await auth_headers(client),
        )
        assert response.status == 503
        body = await response.json()
        assert body["error"] == {"code": "transcription_failed", "message": "语音转写失败"}
        assert "secret" not in json.dumps(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("upstream_status", [401, 403])
async def test_proxy_maps_hermes_auth_failure_without_invalidating_device_token(
    gateway_client: TestClient, upstream_client: TestClient, upstream_status: int,
):
    leaked_upstream_error = {
        "error": {
            "message": "invalid bearer upstream-secret for http://private-hermes.local",
        }
    }
    upstream_client.session_state.update({
        "status": upstream_status,
        "payload": leaked_upstream_error,
    })
    headers = await auth_headers(gateway_client)

    response = await gateway_client.get("/api/sessions", headers=headers)

    assert response.status == 502
    body = await response.json()
    assert body == {
        "error": {
            "code": "hermes_auth_failed",
            "message": "Hermes API Server Key 无效或无权访问，请在 Nexus 配置中检查 Hermes API 地址和 API Server Key",
        }
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "upstream-secret" not in serialized
    assert "private-hermes.local" not in serialized

    upstream_client.session_state.update({
        "status": 200,
        "payload": {"object": "list", "data": upstream_client.session_state["rows"]},
    })
    still_authorized = await gateway_client.get("/api/sessions", headers=headers)
    assert still_authorized.status == 200


@pytest.mark.asyncio
async def test_proxy_sanitizes_hermes_5xx_body(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    upstream_client.session_state.update({
        "status": 500,
        "payload": {
            "error": {
                "message": "failed at C:/private with bearer upstream-secret",
            }
        },
    })

    response = await gateway_client.get(
        "/api/sessions",
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 502
    body = await response.json()
    assert body == {
        "error": {
            "code": "hermes_upstream_error",
            "message": "上游服务暂时不可用",
        }
    }
    serialized = json.dumps(body, ensure_ascii=False)
    assert "C:/private" not in serialized
    assert "upstream-secret" not in serialized


@pytest.mark.asyncio
async def test_stream_reports_hermes_auth_failure_without_exposing_upstream(
    gateway_client: TestClient, upstream_client: TestClient,
):
    upstream_client.chat_state.update({
        "status": 401,
        "payload": {"detail": "bad key upstream-secret at http://private-hermes.local"},
    })
    headers = await auth_headers(gateway_client)

    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello"},
        headers=headers,
    )

    assert response.status == 200
    body = await response.text()
    assert "event: error" in body
    assert "hermes_auth_failed" in body
    assert "Hermes API Server Key 无效或无权访问，请在 Nexus 配置中检查 Hermes API 地址和 API Server Key" in body
    assert "upstream-secret" not in body
    assert "private-hermes.local" not in body

    still_authorized = await gateway_client.get("/api/sessions", headers=headers)
    assert still_authorized.status == 200


@pytest.mark.asyncio
async def test_unhandled_gateway_error_is_returned_as_sanitized_json(tmp_path: Path):
    app = create_app(
        username="nexus",
        password="test-password",
        session_secret="test-session-secret",
        upstream_url="http://127.0.0.1:1",
        upstream_token="upstream-secret",
        storage_dir=tmp_path / "media",
        min_free_disk_bytes=0,
        transcribe_audio=lambda _path: {"success": True, "transcript": "test"},
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/api/sessions", headers=await auth_headers(client))
        assert response.status == 502
        assert await response.json() == {
            "error": {"code": "gateway_error", "message": "上游服务暂时不可用"}
        }


@pytest.mark.asyncio
async def test_delete_removes_uploaded_file(gateway_client: TestClient):
    form = FormData()
    form.add_field("file", io.BytesIO(b"temporary"), filename="temp.txt", content_type="text/plain")
    upload = await gateway_client.post("/api/uploads", data=form, headers=await auth_headers(gateway_client))
    file_id = (await upload.json())["file"]["id"]

    deleted = await gateway_client.delete(f"/api/files/{file_id}", headers=await auth_headers(gateway_client))
    assert deleted.status == 200
    assert (await deleted.json())["deleted"] is True

    missing = await gateway_client.get(f"/api/files/{file_id}", headers=await auth_headers(gateway_client))
    assert missing.status == 404
