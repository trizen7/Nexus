package app.nexus.mobile.network

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import java.io.ByteArrayInputStream
import java.io.IOException
import java.net.SocketException
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HermesApiClientTest {
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer()
        server.start()
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun `login exchanges account password for access token`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"access_token\":\"session-token\",\"token_type\":\"bearer\",\"expires_in\":3600}")
        )
        val client = HermesApiClient(server.url("/").toString())

        val token = client.login("nexus", "password")

        assertEquals("session-token", token)
        val request = server.takeRequest()
        assertEquals("/api/auth/login", request.path)
        assertEquals("{\"username\":\"nexus\",\"password\":\"password\"}", request.body.readUtf8())
    }

    @Test
    fun `health returns Gateway and Hermes versions`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"status\":\"ok\",\"version\":\"0.0.8\",\"upstream\":{\"status\":\"ok\",\"version\":\"0.18.2\"}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val health = client.health()
        assertEquals("0.0.8", health.gatewayVersion)
        assertEquals("0.18.2", health.hermesVersion)
        assertEquals("Bearer test-token", server.takeRequest().getHeader("Authorization"))
    }

    @Test
    fun `listSessions parses Hermes persisted sessions`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"object":"list","data":[{"id":"session-1","title":"安卓项目","source":"desktop","message_count":12,"last_active":1784122422.0},{"id":"session-2","title":null,"source":"api_server","message_count":2,"last_active":1784122000.0}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val sessions = client.listSessions()

        assertEquals(2, sessions.size)
        assertEquals("安卓项目", sessions[0].displayTitle)
        assertEquals("新对话", sessions[1].displayTitle)
        assertEquals("/api/sessions", server.takeRequest().path)
    }

    @Test
    fun `createSession returns persisted session id`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(201)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"object\":\"hermes.session\",\"session\":{\"id\":\"mobile-1\",\"title\":null,\"source\":\"api_server\",\"message_count\":0,\"last_active\":1784123000.0}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val session = client.createSession()

        assertEquals("mobile-1", session.id)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/sessions", request.path)
        assertEquals("{}", request.body.readUtf8())
    }

    @Test
    fun `loadMessagePage requests latest slice and parses pagination`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"object":"list","data":[{"id":3,"role":"assistant","content":"最新"}],"pagination":{"total":23,"offset":0,"limit":10,"has_more":true}}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val page = client.loadMessagePage("session-1", 10, 0)

        assertEquals(listOf("最新"), page.messages.map { it.content })
        assertEquals(23, page.total)
        assertEquals(true, page.hasMore)
        assertEquals("/api/sessions/session-1/messages?limit=10&offset=0", server.takeRequest().path)
    }

    @Test
    fun `pages without canonical ids keep distinct fallback ids so older user messages are not dropped`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"object":"list","data":[{"role":"user","content":"最近提问"},{"role":"assistant","content":"最近回答"}],"pagination":{"total":4,"offset":0,"limit":2,"has_more":true}}""")
        )
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"object":"list","data":[{"role":"user","content":"之前提问"},{"role":"assistant","content":"之前回答"}],"pagination":{"total":4,"offset":2,"limit":2,"has_more":false}}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val latest = client.loadMessagePage("session-1", 2, 0)
        val older = client.loadMessagePage("session-1", 2, 2)
        val merged = app.nexus.mobile.prependMessagePage(latest.messages, older.messages)

        assertEquals(4, merged.size)
        assertEquals(listOf("之前提问", "之前回答", "最近提问", "最近回答"), merged.map { it.content })
        assertEquals(4, merged.map { it.id }.distinct().size)
    }

    @Test
    fun `session run status restores server truth after app reconnect`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"session_id":"session-1","run_id":"run-1","status":"running","active":true}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val status = client.getSessionRunStatus("session-1")

        assertEquals("session-1", status.sessionId)
        assertEquals("run-1", status.runId)
        assertEquals("running", status.status)
        assertTrue(status.active)
        assertEquals("/api/sessions/session-1/run", server.takeRequest().path)
    }

    @Test
    fun `loadMessages parses user and assistant history`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("""{"object":"list","data":[{"id":1,"role":"user","content":"你好"},{"id":2,"role":"tool","content":"{\"output\":\"raw tool log\"}"},{"id":3,"role":"assistant","content":"你好，我在。"}]}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val messages = client.loadMessages("session-1")

        assertEquals(listOf("你好", "你好，我在。"), messages.map { it.content })
        assertEquals(listOf(ChatRole.USER, ChatRole.ASSISTANT), messages.map { it.role })
    }

    @Test
    fun `loadMessages restores multimodal text and image parts`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"object":"list","data":[{"id":"m1","role":"user","content":[{"type":"text","text":"看看"},{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,AQID"}}]}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val messages = client.loadMessages("session-1")

        assertEquals("看看", messages.single().content)
        assertEquals("data:image/jpeg;base64,AQID", messages.single().images.single().dataUrl)
    }

    @Test
    fun `loadMessages restores gateway persisted file metadata`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"object":"list","data":[{"id":"m1","role":"user","content":"资料","nexus_files":[{"id":"file-1","url":"/api/files/file-1","name":"方案.pdf","mime_type":"application/pdf","size":2048}]}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val message = client.loadMessages("session-1").single()

        assertEquals("资料", message.content)
        assertEquals("file-1", message.files.single().id)
        assertEquals("方案.pdf", message.files.single().name)
        assertEquals(2048L, message.files.single().size)
        assertEquals(server.url("api/files/file-1").toString(), message.files.single().downloadUrl)
    }

    @Test
    fun `loadMessages restores gateway persisted image urls without screenshot marker`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"object":"list","data":[{"id":"m1","role":"user","content":"看看\n[screenshot]","nexus_images":[{"id":"image-1","url":"/api/files/image-1","name":"photo.jpg"}]}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val message = client.loadMessages("session-1").single()

        assertEquals("看看", message.content)
        assertEquals(server.url("api/files/image-1").toString(), message.images.single().previewUri)
    }

    @Test
    fun `renameSession patches title and parses updated session`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("{\"object\":\"hermes.session\",\"session\":{\"id\":\"session-1\",\"title\":\"新标题\",\"source\":\"desktop\",\"message_count\":2,\"last_active\":1.0}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val renamed = client.renameSession("session-1", "新标题")

        assertEquals("新标题", renamed.displayTitle)
        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/sessions/session-1", request.path)
        assertEquals("{\"title\":\"新标题\"}", request.body.readUtf8())
    }

    @Test
    fun `deleteSession deletes persisted session`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody("{\"object\":\"hermes.session.deleted\",\"id\":\"session-1\",\"deleted\":true}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        assertEquals(true, client.deleteSession("session-1"))
        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/api/sessions/session-1", request.path)
    }

    @Test
    fun `default client allows long Hermes agent runs`() {
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        assertEquals(300_000, client.readTimeoutMillis)
    }

    @Test
    fun `cancelled coroutine does not expose internal error text`() {
        val message = friendlyNetworkError(kotlinx.coroutines.CancellationException("StandaloneCoroutine was cancelled"))

        assertEquals("操作已取消", message)
    }

    @Test
    fun `network abort is translated to a user friendly message`() {
        val message = friendlyNetworkError(SocketException("Software caused connection abort"))

        assertEquals("网络连接中断，请稍后重试", message)
    }

    @Test
    fun `login maps unauthorized response to credential error`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"error\":{\"code\":\"invalid_credentials\",\"message\":\"bad credentials\"}}")
        )
        val client = HermesApiClient(server.url("/").toString())

        val error = runCatching { client.login("nexus", "wrong") }.exceptionOrNull()

        assertTrue(error is HermesHttpException)
        assertEquals("登录失败：账号或密码错误", friendlyNetworkError(error!!))
        assertFalse(requiresPasswordReauthentication(error))
    }

    @Test
    fun `expired device token asks for password again`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(401)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"error\":{\"code\":\"unauthorized\",\"message\":\"expired\"}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "expired-token")

        val error = runCatching { client.listSessions() }.exceptionOrNull()

        assertEquals("登录已失效，请重新输入密码", friendlyNetworkError(error!!))
        assertTrue(requiresPasswordReauthentication(error))
    }

    @Test
    fun `reverse proxy certificate error includes actionable recovery`() {
        val message = friendlyNetworkError(
            javax.net.ssl.SSLHandshakeException("Trust anchor for certification path not found"),
            "https://nexus.example.com"
        )

        assertTrue(message.contains("反向代理证书"))
        assertTrue(message.contains("服务器域名"))
        assertFalse(message.contains("Debug APK"))
    }

    @Test
    fun `unknown host points local users to HTTP product test port`() {
        val message = friendlyNetworkError(java.net.UnknownHostException("nexus.local"))

        assertTrue(message.contains("http://"))
        assertTrue(message.contains("18787"))
    }

    @Test
    fun `loadMessages hides internal runtime user records`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setBody(
                    """{"object":"list","data":[{"id":"internal","role":"user","content":"[CONTEXT COMPACTION — REFERENCE ONLY] hidden"},{"id":"real","role":"user","content":"真实消息"},{"id":"assistant","role":"assistant","content":"收到"}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val messages = client.loadMessages("session-1")

        assertEquals(listOf("真实消息", "收到"), messages.map { it.content })
    }

    @Test
    fun `other network errors keep a useful localized message`() {
        val message = friendlyNetworkError(IOException("unexpected end of stream"))

        assertEquals("网络连接异常，请稍后重试", message)
    }

    @Test
    fun `transcribeAudio uploads recording and returns server transcript`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"transcript\":\"你好Nexus\",\"file\":{\"id\":\"voice-1\",\"name\":\"voice.m4a\",\"mime_type\":\"audio/mp4\",\"size\":3,\"download_url\":\"/api/files/voice-1\"}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val result = client.transcribeAudio("voice.m4a", byteArrayOf(1, 2, 3))

        assertEquals("你好Nexus", result.transcript)
        assertEquals("voice-1", result.file.id)
        val request = server.takeRequest()
        assertEquals("/api/audio/transcriptions", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
    }

    @Test
    fun `uploadStream throttles progress callbacks for large files`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(201)
                .setBody("""{"file":{"id":"large","name":"large.bin","mime_type":"application/octet-stream","size":1048576,"download_url":"/api/files/large"}}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")
        val content = ByteArray(1024 * 1024)
        val progress = mutableListOf<Int>()

        client.uploadStream("large.bin", "application/octet-stream", content.size.toLong(), { ByteArrayInputStream(content) }, progress::add)

        assertEquals(100, progress.last())
        assertTrue(progress.size <= 22)
    }

    @Test
    fun `uploadStream reports progress without requiring a byte array`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(201)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"object\":\"nexus.file\",\"file\":{\"id\":\"file-stream\",\"name\":\"大文件.bin\",\"mime_type\":\"application/octet-stream\",\"size\":5,\"download_url\":\"/api/files/file-stream\"}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")
        val progress = mutableListOf<Int>()

        val uploaded = client.uploadStream(
            name = "大文件.bin",
            mimeType = "application/octet-stream",
            size = 5,
            openStream = { ByteArrayInputStream(byteArrayOf(1, 2, 3, 4, 5)) },
            onProgress = progress::add
        )

        assertEquals("file-stream", uploaded.id)
        assertEquals(100, progress.last())
        assertEquals("POST", server.takeRequest().method)
    }

    @Test
    fun `uploadFile sends multipart content and parses gateway file id`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(201)
                .setHeader("Content-Type", "application/json")
                .setBody("{\"object\":\"nexus.file\",\"file\":{\"id\":\"file-1\",\"name\":\"资料.pdf\",\"mime_type\":\"application/pdf\",\"size\":3,\"download_url\":\"/api/files/file-1\"}}")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val uploaded = client.uploadFile("资料.pdf", "application/pdf", byteArrayOf(1, 2, 3))

        assertEquals("file-1", uploaded.id)
        assertEquals("资料.pdf", uploaded.name)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/uploads", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
        val body = request.body.readUtf8()
        assert(body.contains("name=\"file\""))
        assert(body.contains("filename=\"资料.pdf\""))
    }

    @Test
    fun `streamChat sends uploaded attachment ids`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody("event: run.completed\ndata: {\"completed\":true}\n\n")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        client.streamChat("mobile-session", "处理文件", attachmentIds = listOf("file-1"))

        val json = com.google.gson.JsonParser.parseString(server.takeRequest().body.readUtf8()).asJsonObject
        assertEquals("file-1", json.getAsJsonArray("attachment_ids")[0].asString)
        assertAndroidPhoneContext(json)
    }

    @Test
    fun `streamChat sends text and images as multimodal session message`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody("event: run.completed\ndata: {\"completed\":true}\n\n")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")
        val image = ChatImage(
            id = "image-1",
            previewUri = "content://image/1",
            dataUrl = "data:image/jpeg;base64,AQID"
        )

        client.streamChat("mobile-session", "看看这张图", listOf(image))

        val body = server.takeRequest().body.readUtf8()
        val json = com.google.gson.JsonParser.parseString(body).asJsonObject
        val parts = json.getAsJsonArray("message")
        assertEquals("text", parts[0].asJsonObject.get("type").asString)
        assertEquals("看看这张图", parts[0].asJsonObject.get("text").asString)
        assertEquals("image_url", parts[1].asJsonObject.get("type").asString)
        assertEquals(
            "data:image/jpeg;base64,AQID",
            parts[1].asJsonObject.getAsJsonObject("image_url").get("url").asString
        )
    }

    @Test
    fun `streamChat emits text deltas`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody(
                    "event: assistant.delta\n" +
                        "data: {\"delta\":\"连接\"}\n\n" +
                        "event: assistant.delta\n" +
                        "data: {\"delta\":\"成功\"}\n\n" +
                        "event: run.completed\n" +
                        "data: {\"completed\":true}\n\n"
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val events = client.streamChat("mobile-session", "你好")

        assertEquals(
            listOf(
                HermesStreamEvent.TextDelta("连接"),
                HermesStreamEvent.TextDelta("成功"),
                HermesStreamEvent.Completed
            ),
            events
        )
        val request = server.takeRequest()
        assertEquals("/api/sessions/mobile-session/chat/stream", request.path)
        assertEquals("Bearer test-token", request.getHeader("Authorization"))
    }

    @Test
    fun `listModels parses selectable model ids`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody("""{"object":"list","data":[{"id":"profile-a","root":"profile-a","owned_by":"local","parent":null},{"id":"model-fast","root":"gpt-5.6-sol","owned_by":"remote","parent":"profile-a"}]}""")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val models = client.listModels()

        assertEquals(listOf("profile-a", "model-fast"), models.map { it.id })
        assertEquals("profile-a", models.first().root)
        assertEquals("local", models.first().ownedBy)
        assertEquals(null, models.first().parent)
        assertEquals("profile-a", models.last().parent)
        assertEquals(true, models.first().isPersona)
        assertEquals(true, models.last().isInferenceModel)
        assertEquals("/v1/models", server.takeRequest().path)
    }

    @Test
    fun `listCronJobs parses schedule repeat and paused state`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "application/json")
                .setBody(
                    """{"jobs":[{"id":"job-1","name":"Daily","prompt":"Summarize","schedule":{"kind":"cron","expr":"0 9 * * *","display":"Daily at 09:00"},"repeat":{"times":3,"completed":1},"enabled":false,"state":"paused","next_run_at":"2026-07-23T01:00:00Z","last_status":"completed"},{"id":"job-2","name":"Once","prompt":"Check","schedule":"2026-08-01T00:00:00Z","repeat":1,"enabled":true}]}"""
                )
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        val jobs = client.listCronJobs()

        assertEquals(2, jobs.size)
        assertEquals("0 9 * * *", jobs[0].schedule.editableValue)
        assertEquals("Daily at 09:00", jobs[0].schedule.displayValue)
        assertEquals(3, jobs[0].repeatTimes)
        assertEquals(1, jobs[0].completedRuns)
        assertEquals(true, jobs[0].isPaused)
        assertEquals(1, jobs[1].repeatTimes)
        assertEquals("/api/jobs?include_disabled=true", server.takeRequest().path)
    }

    @Test
    fun `create and update cron jobs use Hermes payload shapes`() = runTest {
        val createdJob = """{"job":{"id":"job-1","name":"Daily","prompt":"Summarize","schedule":{"kind":"cron","expr":"0 9 * * *"},"repeat":{"times":3,"completed":0},"enabled":true,"state":"scheduled"}}"""
        val updatedJob = """{"job":{"id":"job-1","name":"Morning","prompt":"Review","schedule":{"kind":"cron","expr":"0 8 * * *"},"repeat":{"times":5,"completed":2},"enabled":true,"state":"scheduled"}}"""
        server.enqueue(MockResponse().setResponseCode(201).setHeader("Content-Type", "application/json").setBody(createdJob))
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(updatedJob))
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        client.createCronJob("Daily", "0 9 * * *", "Summarize", 3)
        client.updateCronJob("job-1", "Morning", "0 8 * * *", "Review", 5, 2)

        val createRequest = server.takeRequest()
        assertEquals("POST", createRequest.method)
        assertEquals("/api/jobs", createRequest.path)
        val createJson = com.google.gson.JsonParser.parseString(createRequest.body.readUtf8()).asJsonObject
        assertEquals("local", createJson.get("deliver").asString)
        assertEquals(3, createJson.get("repeat").asInt)

        val updateRequest = server.takeRequest()
        assertEquals("PATCH", updateRequest.method)
        assertEquals("/api/jobs/job-1", updateRequest.path)
        val repeat = com.google.gson.JsonParser.parseString(updateRequest.body.readUtf8())
            .asJsonObject.getAsJsonObject("repeat")
        assertEquals(5, repeat.get("times").asInt)
        assertEquals(2, repeat.get("completed").asInt)
    }

    @Test
    fun `cron job actions use dedicated Hermes endpoints`() = runTest {
        val jobBody = """{"job":{"id":"job-1","name":"Daily","prompt":"Summarize","schedule":{"kind":"cron","expr":"0 9 * * *"},"enabled":true,"state":"scheduled"}}"""
        repeat(3) {
            server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody(jobBody))
        }
        server.enqueue(MockResponse().setResponseCode(200).setHeader("Content-Type", "application/json").setBody("{\"ok\":true}"))
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        client.pauseCronJob("job-1")
        client.resumeCronJob("job-1")
        client.runCronJob("job-1")
        assertEquals(true, client.deleteCronJob("job-1"))

        assertEquals("POST /api/jobs/job-1/pause", requestSignature(server.takeRequest()))
        assertEquals("POST /api/jobs/job-1/resume", requestSignature(server.takeRequest()))
        assertEquals("POST /api/jobs/job-1/run", requestSignature(server.takeRequest()))
        assertEquals("DELETE /api/jobs/job-1", requestSignature(server.takeRequest()))
    }

    @Test
    fun `streamChat sends persona and inference models independently`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody("event: run.completed\ndata: {\"completed\":true}\n\n")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        client.streamChat(
            "mobile-session",
            "hello",
            personaModel = "profile-a",
            inferenceModel = "gpt-5.6-sol",
            reasoningEffort = "high"
        )

        val body = com.google.gson.JsonParser.parseString(server.takeRequest().body.readUtf8()).asJsonObject
        assertEquals("profile-a", body.get("persona_model").asString)
        assertEquals("gpt-5.6-sol", body.get("inference_model").asString)
        assertEquals("high", body.get("reasoning_effort").asString)
        assertEquals(false, body.has("model"))
        assertAndroidPhoneContext(body)
    }

    @Test
    fun `streamChat omits reasoning depth by default`() = runTest {
        server.enqueue(
            MockResponse()
                .setResponseCode(200)
                .setHeader("Content-Type", "text/event-stream")
                .setBody("event: run.completed\ndata: {\"completed\":true}\n\n")
        )
        val client = HermesApiClient(server.url("/").toString(), "test-token")

        client.streamChat("mobile-session", "hello")

        val body = com.google.gson.JsonParser.parseString(server.takeRequest().body.readUtf8()).asJsonObject
        assertFalse(body.has("reasoning_effort"))
        assertAndroidPhoneContext(body)
    }

    private fun assertAndroidPhoneContext(body: com.google.gson.JsonObject) {
        val context = body.getAsJsonObject("client_context")
        assertEquals("android", context.get("platform").asString)
        assertEquals("phone", context.get("form_factor").asString)
        assertFalse(context.get("supports_direct_local_paths").asBoolean)
        assertFalse(context.get("supports_drag_and_drop").asBoolean)
    }

    private fun requestSignature(request: okhttp3.mockwebserver.RecordedRequest): String =
        "${request.method} ${request.path}"

}
