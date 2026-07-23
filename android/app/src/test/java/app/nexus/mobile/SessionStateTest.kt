package app.nexus.mobile

import app.nexus.mobile.network.HermesCronJob
import app.nexus.mobile.network.HermesCronSchedule
import app.nexus.mobile.network.HermesModel
import app.nexus.mobile.network.HermesSession
import app.nexus.mobile.network.SessionChannel
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionStateTest {
    @Test
    fun `voice transcript waits in composer instead of showing upload state`() {
        val state = voiceTranscriptState("测试语音")

        assertEquals("测试语音", state.text)
        assertEquals(false, state.uploading)
    }

    @Test
    fun `session sources map to user facing channel groups`() {
        assertEquals(SessionChannel.PC, HermesSession("pc", null, "desktop", 0, 0.0).channel)
        assertEquals(SessionChannel.API, HermesSession("api", null, "api_server", 0, 0.0).channel)
        assertEquals(SessionChannel.CRON, HermesSession("cron", null, "cron", 0, 0.0).channel)
        assertEquals(SessionChannel.WEIXIN, HermesSession("wx", null, "weixin", 0, 0.0).channel)
        assertEquals(SessionChannel.QQ, HermesSession("qq", null, "qqbot", 0, 0.0).channel)
    }

    @Test
    fun `sessions are grouped in stable channel order`() {
        val sessions = listOf(
            HermesSession("cron", "定时", "cron", 0, 3.0),
            HermesSession("api", "手机", "api_server", 0, 4.0),
            HermesSession("pc", "电脑", "desktop", 0, 5.0)
        )

        val groups = groupSessionsByChannel(sessions)

        assertEquals(listOf(SessionChannel.API, SessionChannel.PC), groups.map { it.channel })
    }

    @Test
    fun `active channel starts expanded and other groups start collapsed`() {
        val groups = listOf(
            SessionGroup(SessionChannel.API, listOf(HermesSession("api", null, "api_server", 0, 1.0))),
            SessionGroup(SessionChannel.PC, listOf(HermesSession("pc", null, "desktop", 0, 1.0)))
        )

        val expanded = initialExpandedChannels(groups, activeSessionId = "pc")

        assertEquals(setOf(SessionChannel.PC), expanded)
    }

    @Test
    fun `toggling a channel independently expands and collapses it`() {
        val once = toggleChannel(emptySet(), SessionChannel.CRON)
        val twice = toggleChannel(once, SessionChannel.CRON)

        assertEquals(setOf(SessionChannel.CRON), once)
        assertEquals(emptySet<SessionChannel>(), twice)
    }

    @Test
    fun `saved connection reconnects with encrypted token and never requires stored password`() {
        assertEquals(true, SavedConnection("https://nexus.example.com/", "nexus", "device-token", "session").isUsable)
        assertEquals(false, SavedConnection("https://nexus.example.com/", "nexus", "", null).isUsable)
        assertEquals(false, SavedConnection("", "nexus", "device-token", null).isUsable)
        assertEquals(true, SavedConnection("server", "nexus", "device-token", null).autoRefresh)
    }

    @Test
    fun `new installations do not default to a development server`() {
        assertEquals("", defaultServerUrl())
    }

    @Test
    fun `new conversation starts as a local draft without persisted session id`() {
        val draft = ConversationDraft.newDraft()

        assertEquals(true, draft.isDraft)
        assertEquals(null, draft.persistedSessionId)
    }

    @Test
    fun `persisting a draft attaches server session id`() {
        val persisted = ConversationDraft.newDraft().persistedAs("session-1")

        assertEquals(false, persisted.isDraft)
        assertEquals("session-1", persisted.persistedSessionId)
    }

    @Test
    fun `empty draft does not require a persisted session`() {
        assertEquals(false, requiresPersistedSession(activeSessionId = null, text = ""))
        assertEquals(true, requiresPersistedSession(activeSessionId = null, text = "你好"))
        assertEquals(false, requiresPersistedSession(activeSessionId = "existing", text = "你好"))
    }

    @Test
    fun `message can send with either text or an image`() {
        assertEquals(false, canSendMessage("", emptyList()))
        assertEquals(true, canSendMessage("你好", emptyList()))
        assertEquals(true, canSendMessage("", listOf("image-1")))
    }

    @Test
    fun `image bounds are valid when Android reports positive dimensions`() {
        assertEquals(true, hasValidImageBounds(4032, 3024))
        assertEquals(false, hasValidImageBounds(0, 0))
        assertEquals(false, hasValidImageBounds(-1, 1080))
    }

    @Test
    fun `authoritative refresh replaces unchanged history and appends new messages`() {
        val existing = listOf(
            app.nexus.mobile.network.ChatMessage("1", app.nexus.mobile.network.ChatRole.USER, "你好"),
            app.nexus.mobile.network.ChatMessage("2", app.nexus.mobile.network.ChatRole.ASSISTANT, "你好")
        )
        val incoming = existing +
            app.nexus.mobile.network.ChatMessage("3", app.nexus.mobile.network.ChatRole.USER, "PC 新消息")

        assertEquals(incoming, mergeAuthoritativeMessages(existing, incoming))
    }

    @Test
    fun `empty or older refresh does not erase current messages`() {
        val existing = listOf(
            app.nexus.mobile.network.ChatMessage("1", app.nexus.mobile.network.ChatRole.USER, "你好"),
            app.nexus.mobile.network.ChatMessage("2", app.nexus.mobile.network.ChatRole.ASSISTANT, "回复")
        )

        assertEquals(existing, mergeAuthoritativeMessages(existing, emptyList()))
        assertEquals(existing, mergeAuthoritativeMessages(existing, existing.take(1)))
    }

    @Test
    fun `downloadable file links are extracted from assistant text`() {
        val text = "新版已生成：https://files.example.com/Nexus-0.0.2.apk"

        assertEquals(
            listOf("https://files.example.com/Nexus-0.0.2.apk"),
            extractDownloadableLinks(text)
        )
    }

    @Test
    fun `plain web pages are not treated as downloadable files`() {
        assertEquals(emptyList<String>(), extractDownloadableLinks("官网：https://example.com/docs"))
    }

    @Test
    fun `authenticated gateway links with extension fragments are extracted`() {
        val url = "http://192.0.2.10:8787/api/files/file-1#.md"

        assertEquals(listOf(url), extractDownloadableLinks("开发计划：$url"))
        assertEquals("file-1.md", linkedDownloadFileName(url))
        assertEquals("file-1", gatewayFileId(url))
        assertEquals(url, resolveDownloadUrl("http://192.0.2.10:8787/", url))
    }

    @Test
    fun `relative gateway downloads resolve against configured server`() {
        assertEquals(
            "https://nexus.example.com/api/files/file-1",
            resolveDownloadUrl("https://nexus.example.com/", "/api/files/file-1")
        )
        assertEquals(
            "https://files.example.com/report.pdf",
            resolveDownloadUrl("https://nexus.example.com/", "https://files.example.com/report.pdf")
        )
    }

    @Test
    fun `text attachments can be embedded but binary files cannot`() {
        assertEquals(true, isTextAttachment("说明.md", "text/markdown"))
        assertEquals(true, isTextAttachment("配置.json", "application/json"))
        assertEquals(false, isTextAttachment("应用.apk", "application/vnd.android.package-archive"))
    }

    @Test
    fun `composition can send with text image or text file`() {
        assertEquals(false, canSendComposition("", emptyList(), false))
        assertEquals(true, canSendComposition("你好", emptyList(), false))
        assertEquals(true, canSendComposition("", listOf("image"), false))
        assertEquals(true, canSendComposition("", emptyList(), true))
    }

    @Test
    fun `speech recognizer errors map to friendly Chinese text`() {
        assertEquals("没有听清，请重试", friendlySpeechError(7))
        assertEquals("语音识别服务暂不可用", friendlySpeechError(4))
        assertEquals("语音识别失败，请重试", friendlySpeechError(99))
    }

    @Test
    fun `service speech errors fall back to system voice input`() {
        listOf(4, 5, 8, 10, 11, 12, 13).forEach { code ->
            assertEquals(true, shouldFallbackToSystemSpeech(code))
        }
        assertEquals(false, shouldFallbackToSystemSpeech(7))
        assertEquals(false, shouldFallbackToSystemSpeech(9))
    }

    @Test
    fun `failed send restores only inside its originating conversation`() {
        assertEquals("原始内容", restoreDraftForSession("one", "one", "", "原始内容"))
        assertEquals("后来输入", restoreDraftForSession("one", "one", "后来输入", "原始内容"))
        assertEquals("另一个会话", restoreDraftForSession("one", "two", "另一个会话", "原始内容"))
    }

    @Test
    fun `optimistic sent file is rendered as file metadata instead of a path string`() {
        val file = app.nexus.mobile.network.ChatFile(
            id = "local",
            name = "方案.pdf",
            mimeType = "application/pdf",
            size = 2048,
            uri = "content://file",
            uploadedId = "server-file",
            downloadUrl = "/api/files/server-file"
        )
        val message = optimisticUserMessage("说明文字", emptyList(), file)

        assertEquals("说明文字", message.content)
        assertEquals(listOf(file), message.files)
    }

    @Test
    fun `attachment becomes sendable only after preupload completes`() {
        assertEquals(false, AttachmentUploadState.Uploading(45).readyToSend)
        assertEquals(false, AttachmentUploadState.Failed("网络异常").readyToSend)
        assertEquals(true, AttachmentUploadState.Ready("server-file").readyToSend)
    }

    @Test
    fun `answer lifecycle exposes a clear user status`() {
        assertEquals("思考中…", AnswerStatus.THINKING.label)
        assertEquals("正在生成回答…", AnswerStatus.GENERATING.label)
        assertEquals("回答完成", AnswerStatus.COMPLETED.label)
        assertEquals("已停止", AnswerStatus.STOPPED.label)
        assertEquals("回答失败", AnswerStatus.FAILED.label)
    }

    @Test
    fun `download state preserves progress when paused and can complete`() {
        val downloading = FileDownloadState("file", DownloadStatus.DOWNLOADING, 46)
        val paused = downloading.copy(status = DownloadStatus.PAUSED)
        val completed = paused.copy(status = DownloadStatus.COMPLETED, progress = 100, localPath = "downloads/file.pdf")

        assertEquals(46, paused.progress)
        assertEquals(DownloadStatus.COMPLETED, completed.status)
        assertEquals("downloads/file.pdf", completed.localPath)
    }

    @Test
    fun `session title derives from first real user content`() {
        assertEquals("局域网打印机连接问题", deriveSessionTitle("局域网打印机连接问题\n请帮我排查"))
        assertEquals("查看项目方案.pdf", deriveSessionTitle("", "项目方案.pdf", false))
        assertEquals("图片分析", deriveSessionTitle("", null, true))
    }

    @Test
    fun `internal runtime records are hidden from conversation`() {
        assertEquals(false, isVisibleUserMessage("[CONTEXT COMPACTION — REFERENCE ONLY] internal"))
        assertEquals(false, isVisibleUserMessage("[IMPORTANT: You are running as a scheduled cron job.]"))
        assertEquals(false, isVisibleUserMessage("[System: internal state]"))
        assertEquals(true, isVisibleUserMessage("帮我查看这个文件"))
    }

    @Test
    fun `network errors do not assume wifi transport`() {
        assertEquals("网络连接中断，请稍后重试", genericConnectionInterruptedMessage())
    }

    @Test
    fun `each conversation retains an independent composer draft`() {
        val drafts = ConversationDrafts()
            .save("one", ComposerDraft("第一条", emptyList(), null))
            .save("two", ComposerDraft("第二条", emptyList(), null))

        assertEquals("第一条", drafts.load("one").text)
        assertEquals("第二条", drafts.load("two").text)
        assertEquals("", drafts.load("missing").text)
    }

    @Test
    fun `lazy list latest index targets the final bottom spacer`() {
        assertEquals(11, latestLazyListIndex(10))
        assertEquals(11, prependAnchorLazyListIndex(10))
    }

    @Test
    fun `history paging prefetches before the exact top only after user scroll`() {
        assertEquals(false, shouldLoadOlderMessages(3, true, false, false, false))
        assertEquals(true, shouldLoadOlderMessages(3, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(4, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, false, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, true, true, false, true))
    }

    @Test
    fun `run notification maps active and terminal server truth`() {
        assertEquals(RunNotificationKind.RUNNING, runNotificationKind("running", true))
        assertEquals(RunNotificationKind.COMPLETED, runNotificationKind("completed", false))
        assertEquals(RunNotificationKind.FAILED, runNotificationKind("failed", false))
        assertEquals(RunNotificationKind.STOPPED, runNotificationKind("stopped", false))
        assertEquals(RunNotificationKind.NONE, runNotificationKind("idle", false))
    }

    @Test
    fun `prepend anchor preserves the visible item index as well as its offset`() {
        assertEquals(13, prependAnchorLazyListIndex(previousFirstVisibleIndex = 3, prependedMessageCount = 10))
    }

    @Test
    fun `message cache keys are reversible and do not collide for Java hash collisions`() {
        val first = messageCacheKey("FB")
        val second = messageCacheKey("Ea")

        assertTrue(first.startsWith("message_cache_v2_"))
        assertEquals("FB", sessionIdFromMessageCacheKey(first))
        assertEquals("Ea", sessionIdFromMessageCacheKey(second))
        assertTrue(first != second)
    }

    @Test
    fun `message cache keys safely encode Unicode and separators`() {
        val sessionId = "会话/emoji-😀?a=b\\c"

        assertEquals(sessionId, sessionIdFromMessageCacheKey(messageCacheKey(sessionId)))
    }

    @Test
    fun `file selection falls back to a private copy when persistable permission fails`() {
        assertEquals(SelectedUriStorage.PERSISTED_URI, selectedUriStorage(permissionPersisted = true))
        assertEquals(SelectedUriStorage.PRIVATE_COPY, selectedUriStorage(permissionPersisted = false))
    }

    @Test
    fun `message cache excludes transient image upload bytes`() {
        val message = app.nexus.mobile.network.ChatMessage(
            "m1",
            app.nexus.mobile.network.ChatRole.USER,
            "看看",
            images = listOf(app.nexus.mobile.network.ChatImage("i1", "content://image", uploadBytes = ByteArray(1024)))
        )

        val json = encodeMessageCache(listOf(message))
        val restored = decodeMessageCache(json)

        assertEquals(0, json.count { it == 'A' })
        assertEquals("看看", restored.single().content)
        assertEquals(null, restored.single().images.single().uploadBytes)
    }

    @Test
    fun `latest message page starts at the final item and older pages prepend`() {
        val all = (1..25).map { app.nexus.mobile.network.ChatMessage("$it", app.nexus.mobile.network.ChatRole.USER, "消息$it") }
        val latest = latestMessagePage(all, 10)
        val previous = olderMessagePage(all, alreadyLoaded = latest.size, pageSize = 10)

        assertEquals((16..25).map(Int::toString), latest.map { it.id })
        assertEquals((6..15).map(Int::toString), previous.map { it.id })
        assertEquals((6..25).map(Int::toString), prependMessagePage(latest, previous).map { it.id })
    }

    @Test
    fun `stale session load cannot replace current conversation`() {
        val current = listOf(app.nexus.mobile.network.ChatMessage("current", app.nexus.mobile.network.ChatRole.USER, "当前"))
        val stale = listOf(app.nexus.mobile.network.ChatMessage("stale", app.nexus.mobile.network.ChatRole.USER, "旧请求"))

        assertEquals(current, applySessionLoadResult("one", "two", current, stale))
        assertEquals(stale, applySessionLoadResult("one", "one", current, stale))
    }

    @Test
    fun `only latest load generation may update selected conversation`() {
        assertEquals(true, acceptsSessionLoad("two", "two", 7, 7))
        assertEquals(false, acceptsSessionLoad("one", "two", 6, 7))
        assertEquals(false, acceptsSessionLoad("two", "two", 6, 7))
    }

    @Test
    fun `selecting a session changes active id without mixing messages`() {
        val first = HermesSession("one", "第一个", "desktop", 4, 2.0)
        val second = HermesSession("two", "第二个", "api_server", 2, 1.0)
        val state = SessionState(listOf(first, second), activeSessionId = first.id)

        val changed = state.select(second.id)

        assertEquals("two", changed.activeSessionId)
        assertEquals("第二个", changed.activeSession?.displayTitle)
    }

    @Test
    fun `theme mode follows system only when configured to system`() {
        assertEquals(false, shouldUseDarkTheme(ThemeMode.LIGHT, systemDark = true))
        assertEquals(true, shouldUseDarkTheme(ThemeMode.DARK, systemDark = false))
        assertEquals(true, shouldUseDarkTheme(ThemeMode.SYSTEM, systemDark = true))
        assertEquals(false, shouldUseDarkTheme(ThemeMode.SYSTEM, systemDark = false))
    }

    @Test
    fun `empty assistant placeholder is not rendered in conversation`() {
        val emptyAssistant = app.nexus.mobile.network.ChatMessage("a", app.nexus.mobile.network.ChatRole.ASSISTANT, "")
        val realAssistant = emptyAssistant.copy(content = "回答")

        assertEquals(false, shouldRenderMessageBubble(emptyAssistant))
        assertEquals(true, shouldRenderMessageBubble(realAssistant))
    }

    @Test
    fun `paging cursor advances by actual returned messages`() {
        assertEquals(13, nextLoadedMessageCount(previousLoaded = 10, returnedCount = 3))
        assertEquals(30, nextLoadedMessageCount(previousLoaded = 10, returnedCount = 20))
    }

    @Test
    fun `stopped run does not restore already submitted composer text`() {
        assertEquals("", draftTextAfterRunFailure("", "已发送", restore = false))
        assertEquals("已发送", draftTextAfterRunFailure("", "已发送", restore = true))
    }

    @Test
    fun `failed send removes only its optimistic message pair`() {
        val messages = listOf(
            app.nexus.mobile.network.ChatMessage("old", app.nexus.mobile.network.ChatRole.USER, "旧消息"),
            app.nexus.mobile.network.ChatMessage("optimistic-user", app.nexus.mobile.network.ChatRole.USER, "待发送"),
            app.nexus.mobile.network.ChatMessage("optimistic-assistant", app.nexus.mobile.network.ChatRole.ASSISTANT, "")
        )

        assertEquals(listOf("old"), removeOptimisticMessages(messages, "optimistic-user", "optimistic-assistant").map { it.id })
    }

    @Test
    fun `draft bundle persists text and uploaded attachment metadata`() {
        val bundle = PersistedDraftBundle(
            localDraftKey = "local-1",
            drafts = mapOf(
                "session-1" to PersistedComposerDraft(
                    text = "未发送内容",
                    images = listOf(PersistedDraftImage("image-1", "https://server/api/files/image-1", "image-1")),
                    file = PersistedDraftFile(
                        "file-1", "计划.md", "text/markdown", 123, "", "file-1", "/api/files/file-1"
                    )
                )
            )
        )

        val restored = decodeDraftBundle(encodeDraftBundle(bundle))

        assertEquals(bundle, restored)
        assertEquals(true, restored.drafts.getValue("session-1").images.single().toImage().uploadState.readyToSend)
        assertEquals(true, restored.drafts.getValue("session-1").file!!.toFile().uploadState.readyToSend)
    }

    @Test
    fun `run snapshot updates the live assistant instead of adding a duplicate bubble`() {
        val messages = listOf(
            app.nexus.mobile.network.ChatMessage("user", app.nexus.mobile.network.ChatRole.USER, "请处理文件"),
            app.nexus.mobile.network.ChatMessage("assistant-live", app.nexus.mobile.network.ChatRole.ASSISTANT, "处理中")
        )

        val reconciled = reconcileRunSnapshot(messages, "assistant-live", "run-1", "处理完成")

        assertEquals(listOf("user", "assistant-live"), reconciled.map { it.id })
        assertEquals("处理完成", reconciled.last().content)
    }

    @Test
    fun `run snapshot creates one recovery bubble when no live assistant exists`() {
        val once = reconcileRunSnapshot(emptyList(), null, "run-1", "恢复内容")
        val twice = reconcileRunSnapshot(once, null, "run-1", "恢复完成")

        assertEquals(1, twice.size)
        assertEquals("run-snapshot-run-1", twice.single().id)
        assertEquals("恢复完成", twice.single().content)
    }

    @Test
    fun `user stop keeps submitted instruction and removes only empty assistant placeholder`() {
        val messages = listOf(
            app.nexus.mobile.network.ChatMessage("user", app.nexus.mobile.network.ChatRole.USER, "停止测试"),
            app.nexus.mobile.network.ChatMessage("assistant", app.nexus.mobile.network.ChatRole.ASSISTANT, "")
        )

        val stopped = messagesAfterSendTermination(
            messages,
            optimisticUserId = "user",
            optimisticAssistantId = "assistant",
            termination = SendTermination.STOPPED_BY_USER
        )

        assertEquals(listOf("user"), stopped.map { it.id })
    }

    @Test
    fun `notification file name keeps extension and leaves room for status`() {
        val compact = compactNotificationFileName("rustdesk-1.4.9-aarch64-signed.apk", 26)

        assertTrue(compact.length <= 26)
        assertTrue(compact.endsWith(".apk"))
        assertTrue("…" in compact)
    }
    @Test
    fun `cron sessions are hidden from the conversation list`() {
        val sessions = listOf(
            HermesSession("api", "Mobile", "api_server", 2, 2.0),
            HermesSession("cron", "Scheduled run", "cron", 4, 3.0)
        )

        assertEquals(listOf("api"), visibleSessions(sessions).map { it.id })
        assertEquals(listOf(SessionChannel.API), groupSessionsByChannel(sessions).map { it.channel })
    }

    @Test
    fun `hidden cron active session falls back to a visible conversation`() {
        val sessions = listOf(
            HermesSession("cron", null, "cron", 0, 3.0),
            HermesSession("api", null, "api_server", 0, 2.0)
        )

        assertEquals("api", resolveVisibleActiveSessionId(sessions, "cron"))
        assertEquals(null, resolveVisibleActiveSessionId(sessions, null, chooseFirstWhenMissing = false))
    }

    @Test
    fun `persona and inference models are classified independently`() {
        val models = listOf(
            HermesModel("profile-a"),
            HermesModel("model-fast", root = "gpt-5.6-sol", parent = "profile-a")
        )

        assertEquals(listOf("profile-a"), personaModels(models).map { it.id })
        assertEquals(listOf("model-fast"), inferenceModels(models).map { it.id })
    }

    @Test
    fun `persona falls back to first profile while inference falls back to Hermes default`() {
        val personas = listOf(HermesModel("profile-a"), HermesModel("profile-b"))
        val inference = listOf(HermesModel("model-fast", parent = "profile-a"))

        assertEquals("profile-b", resolveSelectedPersonaModelId(personas, "profile-b"))
        assertEquals("profile-a", resolveSelectedPersonaModelId(personas, "missing"))
        assertEquals("profile-a", resolveSelectedPersonaModelId(personas, null))
        assertEquals(null, resolveSelectedPersonaModelId(emptyList(), "missing"))
        assertEquals("model-fast", resolveSelectedInferenceModelId(inference, "model-fast"))
        assertEquals(null, resolveSelectedInferenceModelId(inference, "missing"))
        assertEquals(null, resolveSelectedInferenceModelId(inference, null))
    }

    @Test
    fun `reasoning effort restores stored names and wire values`() {
        assertEquals(ReasoningEffort.DEFAULT, ReasoningEffort.fromStored(null))
        assertEquals(ReasoningEffort.HIGH, ReasoningEffort.fromStored("HIGH"))
        assertEquals(ReasoningEffort.XHIGH, ReasoningEffort.fromStored("xhigh"))
        assertEquals(null, ReasoningEffort.DEFAULT.wireValue)
    }

    @Test
    fun `repeat input accepts blank or positive integers only`() {
        assertEquals(true, isValidRepeatInput(""))
        assertEquals(true, isValidRepeatInput("3"))
        assertEquals(false, isValidRepeatInput("0"))
        assertEquals(false, isValidRepeatInput("-1"))
        assertEquals(false, isValidRepeatInput("three"))
        assertEquals(3, repeatCountOrNull(" 3 "))
        assertEquals(null, repeatCountOrNull(""))
    }

    @Test
    fun `cron editor preserves persisted values while editing`() {
        val job = HermesCronJob(
            id = "job-1",
            name = "Daily summary",
            prompt = "Summarize activity",
            schedule = HermesCronSchedule(kind = "cron", expression = "0 9 * * *"),
            repeatTimes = 3,
            completedRuns = 1,
            enabled = false,
            state = "paused"
        )

        val editor = CronJobEditorState.edit(job)

        assertEquals("job-1", editor.jobId)
        assertEquals("0 9 * * *", editor.schedule)
        assertEquals("3", editor.repeatText)
        assertEquals(1, editor.completedRuns)
        assertEquals(false, editor.enabled)
    }

}
