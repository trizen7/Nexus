import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
import shutil
import stat
import threading
import time
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from nexus_gateway.app import (
    CREDENTIALS_PATH_KEY,
    EXTERNAL_RUN_OBSERVER_KEY,
    GATEWAY_CONFIG_KEY,
    MEDIA_STORE_KEY,
    MOBILE_CLIENT_CONTEXT_MARKER,
    MOBILE_CLIENT_SYSTEM_MESSAGE,
    RUN_TRACKER_KEY,
    SETUP_LOCK_KEY,
    MediaStore,
    RunTracker,
    StoredFile,
    create_app,
    security_headers,
)


@pytest_asyncio.fixture
async def upstream_client():
    captured_chat: dict = {}
    captured_completion: dict = {}
    captured_completion_headers: dict[str, str] = {}
    release_chat = asyncio.Event()
    chat_started = asyncio.Event()
    detailed_health_state = {
        "status": 200,
        "payload": {"status": "ok", "gateway_busy": False, "active_agents": 0},
    }
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
    probe_counts = {"health_detailed": 0, "sessions": 0}

    async def health(_request):
        return web.json_response({"status": "ok", "platform": "hermes-agent", "version": "test"})

    async def detailed_health(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        probe_counts["health_detailed"] += 1
        return web.json_response(
            detailed_health_state["payload"],
            status=int(detailed_health_state["status"]),
        )

    async def sessions(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        probe_counts["sessions"] += 1
        return web.json_response(
            {"object": "list", "data": session_state["rows"]},
            status=int(session_state["status"]),
        )

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
        if captured_chat.get("message") == "slow-run":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"event: run.started\ndata: {}\n\n")
            chat_started.set()
            await release_chat.wait()
            await response.write(b"event: run.completed\ndata: {}\n\n")
            return response
        return web.Response(
            text="event: assistant.delta\ndata: {\"delta\":\"收到\"}\n\n",
            content_type="text/event-stream",
        )

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


    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_get("/health/detailed", detailed_health)
    app.router.add_get("/api/sessions", sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_patch("/api/sessions/{session_id}", update_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_get("/api/sessions/{session_id}/messages", messages)
    app.router.add_post("/api/sessions/{session_id}/chat", chat)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", chat)
    app.router.add_post("/v1/chat/completions", chat_completions)
    server = TestServer(app)
    client = TestClient(server)
    client.captured_chat = captured_chat
    client.captured_completion = captured_completion
    client.captured_completion_headers = captured_completion_headers
    client.release_chat = release_chat
    client.chat_started = chat_started
    client.detailed_health_state = detailed_health_state
    client.session_state = session_state
    client.probe_counts = probe_counts
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

    def fail_account_commit(path, content):
        if path == account_path:
            raise OSError("injected account commit failure")
        return original_write(path, content)

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

    def replace_config_before_account_failure(path, content):
        if path == account_path:
            config_path.unlink()
            config_path.write_text("foreign-config", encoding="utf-8")
            raise OSError("injected account commit failure")
        return original_write(path, content)

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
async def test_mobile_client_context_becomes_system_role_on_inference_route(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={
            "message": "选择模型",
            "inference_model": "route-fast",
            "client_context": mobile_client_context(),
        },
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_completion["messages"] == [
        {"role": "system", "content": MOBILE_CLIENT_SYSTEM_MESSAGE},
        {"role": "user", "content": "选择模型"},
    ]
    assert "client_context" not in upstream_client.captured_completion


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
async def test_inference_model_uses_openai_route_and_adapts_stream(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "hello", "persona_model": "profile-a", "inference_model": "route-fast"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    stream = await response.text()
    assert "event: run.started" in stream
    assert "event: tool.started" in stream
    assert "event: tool.completed" in stream
    assert "event: assistant.delta" in stream
    assert "\u6a21\u578b\u56de\u590d" in stream
    assert "event: run.completed" in stream
    assert stream.count("event: run.completed") == 1
    assert stream.rstrip().endswith("event: done\ndata: {}")

    captured = upstream_client.captured_completion
    assert captured == {
        "model": "route-fast",
        "stream": True,
        "messages": [{"role": "user", "content": "hello"}],
    }
    assert upstream_client.captured_completion_headers["X-Hermes-Session-Id"] == "session-1"


@pytest.mark.asyncio
async def test_reasoning_effort_is_forwarded_to_selected_inference_model(
    gateway_client: TestClient,
    upstream_client: TestClient,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "reason", "inference_model": "route-reason", "reasoning_effort": "HIGH"},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 200
    await response.text()
    assert upstream_client.captured_completion["reasoning_effort"] == "high"


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
    assert upstream_client.captured_chat == {"message": "native reason", "reasoning_effort": "minimal"}


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [{"level": "high"}, ["high"], "unsupported"])
async def test_invalid_reasoning_effort_returns_400_without_contacting_upstream(
    gateway_client: TestClient,
    upstream_client: TestClient,
    value,
):
    response = await gateway_client.post(
        "/api/sessions/session-1/chat/stream",
        json={"message": "invalid reason", "inference_model": "route", "reasoning_effort": value},
        headers=await auth_headers(gateway_client),
    )

    assert response.status == 400
    assert (await response.json())["error"]["code"] == "invalid_reasoning_effort"
    assert upstream_client.captured_completion == {}
    assert upstream_client.captured_chat == {}


@pytest.mark.asyncio
async def test_legacy_model_field_remains_an_inference_route(
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
    assert upstream_client.captured_completion["model"] == "route-legacy"
    assert upstream_client.captured_chat == {}


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
async def test_persona_model_stays_on_native_session_route(
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
    assert upstream_client.captured_chat == {"message": "persona", "model": "profile-a"}
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
