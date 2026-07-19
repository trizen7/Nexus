import asyncio
import io
import json
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp import FormData, web
from aiohttp.test_utils import TestClient, TestServer

from nexus_gateway.app import MEDIA_STORE_KEY, MediaStore, RunTracker, create_app


@pytest_asyncio.fixture
async def upstream_client():
    captured_chat: dict = {}
    release_chat = asyncio.Event()
    chat_started = asyncio.Event()

    async def health(_request):
        return web.json_response({"status": "ok", "platform": "hermes-agent", "version": "test"})

    async def sessions(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        return web.json_response({"object": "list", "data": [{"id": "session-1"}]})

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

    async def messages(request):
        assert request.headers["Authorization"] == "Bearer upstream-secret"
        data = [
            {"id": "internal", "role": "user", "content": "[CONTEXT COMPACTION — REFERENCE ONLY] hidden"},
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
    app.router.add_get("/api/sessions", sessions)
    app.router.add_post("/api/sessions", create_session)
    app.router.add_patch("/api/sessions/{session_id}", update_session)
    app.router.add_delete("/api/sessions/{session_id}", delete_session)
    app.router.add_get("/api/sessions/{session_id}/messages", messages)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", chat)
    server = TestServer(app)
    client = TestClient(server)
    client.captured_chat = captured_chat
    client.release_chat = release_chat
    client.chat_started = chat_started
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
        response = await client.post(
            "/api/setup",
            json={
                "username": "admin",
                "password": "strong-password",
                "hermes_api_url": str(upstream_client.make_url("/")).rstrip("/"),
                "hermes_api_token": "upstream-secret",
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
    assert "网页聊天" in html
    assert "文件管理" in html
    assert "语音管理" in html
    assert "账号安全" in html
    assert "系统状态" in html
    assert "createSession" in javascript
    assert "renameSession" in javascript
    assert "deleteSession" in javascript
    assert "uploadAttachment" in javascript

    unauthorized = await gateway_client.get("/api/admin/files")
    assert unauthorized.status == 401

    response = await gateway_client.get(
        "/api/admin/files",
        headers=await auth_headers(gateway_client),
    )
    assert response.status == 200
    assert (await response.json())["data"] == []


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
    assert Path(item["server_path"]).parent.name == item["date"]
    assert Path(item["server_path"]).parent.parent.name == "文件"

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
    assert Path(body["file"]["server_path"]).parent.name == body["file"]["date"]
    assert Path(body["file"]["server_path"]).parent.parent.name == "语音"

    files = await gateway_client.get("/api/admin/files", headers=await auth_headers(gateway_client))
    audio = await gateway_client.get("/api/admin/audio", headers=await auth_headers(gateway_client))
    assert (await files.json())["data"] == []
    assert len((await audio.json())["data"]) == 1


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
    assert text_file["server_path"] in parts[2]["text"]


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
