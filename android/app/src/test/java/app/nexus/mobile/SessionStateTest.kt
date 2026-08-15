package app.nexus.mobile

import android.speech.SpeechRecognizer
import app.nexus.mobile.network.HermesCronJob
import app.nexus.mobile.network.HermesCronSchedule
import app.nexus.mobile.network.HermesModel
import app.nexus.mobile.network.HermesHttpException
import app.nexus.mobile.network.HermesProfile
import app.nexus.mobile.network.HermesSession
import app.nexus.mobile.network.SessionChannel
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SessionStateTest {
    @Test
    fun `voice preview hides text already committed as latest user message`() {
        assertNull(visibleVoicePreview("上一句话", true, false, "上一句话"))
        assertEquals("新的半句话", visibleVoicePreview("新的半句话", true, false, "上一句话"))
        assertNull(visibleVoicePreview("正在聆听…", true, false, null))
        assertNull(visibleVoicePreview("未提交内容", false, false, null))
    }

    @Test
    fun `voice stop window merges consecutive utterances without overwriting order`() {
        val first = VoiceSendPayload("profile-a", "session-a", "A")
        val second = VoiceSendPayload("profile-a", "session-a", "B")

        val merged = mergeVoiceSendPayload(mergeVoiceSendPayload(null, first), second)

        assertEquals("A\nB", merged?.text)
        assertEquals("profile-a", merged?.profileId)
        assertEquals("session-a", merged?.sessionId)
    }

    @Test
    fun `voice payload from another conversation cannot replace pending text`() {
        val current = VoiceSendPayload("profile-a", "session-a", "A")

        val merged = mergeVoiceSendPayload(current, VoiceSendPayload("profile-b", "session-b", "B"))

        assertEquals(current, merged)
    }

    @Test
    fun `voice payload is released only once after run becomes inactive`() {
        val pending = mergeVoiceSendPayload(
            VoiceSendPayload("profile-a", "session-a", "A"),
            VoiceSendPayload("profile-a", "session-a", "B"),
        )

        val stillStopping = releaseVoiceSendPayload(pending, runInactive = false)
        val released = releaseVoiceSendPayload(stillStopping.pending, runInactive = true)
        val repeated = releaseVoiceSendPayload(released.pending, runInactive = true)

        assertEquals(null, stillStopping.outgoing)
        assertEquals("A\nB", released.outgoing?.text)
        assertEquals(null, repeated.outgoing)
    }

    @Test
    fun `stopping hangup recovers pending while dispatched owned payload is excluded`() {
        val pending = VoiceSendPayload("profile-a", "session-a", "pending")
        val deferred = VoiceSendPayload("profile-a", "session-a", "deferred")
        val owned = VoiceSendPayload("profile-a", "session-a", "already sent")

        val recoverable = VoiceSendSlots(pending, deferred, owned).recoverablePayloads()

        assertEquals(listOf("pending", "deferred"), recoverable.map(VoiceSendPayload::text))
        assertEquals(false, recoverable.contains(owned))
    }

    @Test
    fun `switching sessions restores pending text only to its original conversation`() {
        val sessionA = VoiceDraftIdentity("profile-a", "session-a")
        val sessionB = VoiceDraftIdentity("profile-a", "session-b")
        val drafts = mapOf(sessionA to "A草稿", sessionB to "B草稿")

        val recovered = recoverVoiceDraftTexts(
            drafts,
            listOf(VoiceSendPayload("profile-a", "session-a", "A待发语音")),
        )

        assertEquals("A草稿\nA待发语音", recovered[sessionA])
        assertEquals("B草稿", recovered[sessionB])
    }

    @Test
    fun `logout recovery appends once after reconnect restores the original draft`() {
        val payloads = VoiceSendSlots(
            pending = VoiceSendPayload("profile-a", "session-a", "第一段"),
            deferred = VoiceSendPayload("profile-a", "session-a", "第二段"),
        ).recoverablePayloads()
        val slot = PostLogoutVoiceRecoverySlot(
            serverUrl = "https://gateway.example/",
            username = "nexus",
            payloads = payloads,
        )

        assertEquals(
            "profile-a",
            slot.preferredProfileId("https://gateway.example/", "nexus"),
        )
        assertEquals(
            "session-a",
            slot.preferredSessionId(
                "https://gateway.example/",
                "nexus",
                "profile-a",
                setOf("session-a"),
            ),
        )
        val restored = consumePostLogoutVoiceRecovery(
            slot = slot,
            serverUrl = "https://gateway.example/",
            username = "nexus",
            profileId = "profile-a",
            sessionId = "session-a",
            restoredText = "手动草稿",
        )

        assertEquals(true, restored.consumed)
        assertEquals("手动草稿\n第一段\n第二段", restored.restoredText)
        assertEquals(null, restored.slot)

        val repeated = consumePostLogoutVoiceRecovery(
            slot = restored.slot,
            serverUrl = "https://gateway.example/",
            username = "nexus",
            profileId = "profile-a",
            sessionId = "session-a",
            restoredText = restored.restoredText,
        )
        assertEquals(false, repeated.consumed)
        assertEquals("手动草稿\n第一段\n第二段", repeated.restoredText)
    }

    @Test
    fun `logout recovery never crosses account or profile boundaries`() {
        val slot = PostLogoutVoiceRecoverySlot(
            serverUrl = "https://gateway.example/",
            username = "account-a",
            payloads = listOf(VoiceSendPayload("profile-a", "session-a", "私有语音")),
        )

        val otherAccount = consumePostLogoutVoiceRecovery(
            slot = slot,
            serverUrl = "https://gateway.example/",
            username = "account-b",
            profileId = "profile-a",
            sessionId = "session-a",
            restoredText = "账号B草稿",
        )
        val otherProfile = consumePostLogoutVoiceRecovery(
            slot = slot,
            serverUrl = "https://gateway.example/",
            username = "account-a",
            profileId = "profile-b",
            sessionId = "session-a",
            restoredText = "人格B草稿",
        )

        assertEquals(false, otherAccount.consumed)
        assertEquals("账号B草稿", otherAccount.restoredText)
        assertEquals(slot, otherAccount.slot)
        assertEquals(false, otherProfile.consumed)
        assertEquals("人格B草稿", otherProfile.restoredText)
        assertEquals(slot, otherProfile.slot)
    }

    @Test
    fun `missing original session can recover only into a new draft in the same profile`() {
        val slot = PostLogoutVoiceRecoverySlot(
            serverUrl = "https://gateway.example/",
            username = "nexus",
            payloads = listOf(VoiceSendPayload("profile-a", "deleted-session", "待恢复语音")),
        )

        assertEquals(
            true,
            slot.needsNewSession(
                "https://gateway.example/",
                "nexus",
                "profile-a",
                setOf("other-session"),
            ),
        )
        val recovered = consumePostLogoutVoiceRecovery(
            slot = slot,
            serverUrl = "https://gateway.example/",
            username = "nexus",
            profileId = "profile-a",
            sessionId = null,
            restoredText = "新草稿",
            allowMissingSessionRetarget = true,
        )

        assertEquals(true, recovered.consumed)
        assertEquals("新草稿\n待恢复语音", recovered.restoredText)
        assertEquals(null, recovered.slot)
    }

    @Test
    @OptIn(ExperimentalCoroutinesApi::class)
    fun `inactive polling cannot start before delayed stop post finishes`() = runTest {
        val stopResponse = CompletableDeferred<Unit>()
        val events = mutableListOf<String>()
        val confirmation = async {
            stopThenConfirmInactive(
                stopRequest = {
                    events += "stop-post-started"
                    stopResponse.await()
                    events += "stop-post-finished"
                    true
                },
                awaitInactive = {
                    events += "inactive-polled"
                    true
                },
            )
        }

        runCurrent()
        assertEquals(listOf("stop-post-started"), events)
        stopResponse.complete(Unit)
        runCurrent()

        assertEquals(true, confirmation.await())
        assertEquals(listOf("stop-post-started", "stop-post-finished", "inactive-polled"), events)
    }

    @Test
    fun `recoverable voice text preserves an existing composer draft`() {
        assertEquals("手动草稿\n待发送语音", appendRecoverableVoiceText("手动草稿", "待发送语音"))
    }

    @Test
    @OptIn(ExperimentalCoroutinesApi::class)
    fun `delayed stop waits until public run status becomes inactive`() = runTest {
        val statuses = ArrayDeque(
            listOf(
                RunActivityState(active = true, status = "running"),
                RunActivityState(active = true, status = "stopping"),
                RunActivityState(active = false, status = "stopped"),
            )
        )
        var polls = 0

        val ready = awaitInactiveRun(maxAttempts = 5) {
            polls += 1
            statuses.removeFirstOrNull()
        }

        assertEquals(true, ready)
        assertEquals(3, polls)
        assertEquals(200L, testScheduler.currentTime)
    }

    @Test
    @OptIn(ExperimentalCoroutinesApi::class)
    fun `stop timeout does not claim the run is ready`() = runTest {
        var polls = 0

        val ready = awaitInactiveRun(maxAttempts = 3) {
            polls += 1
            RunActivityState(active = true, status = "run_active")
        }

        assertEquals(false, ready)
        assertEquals(3, polls)
        assertEquals(200L, testScheduler.currentTime)
    }

    @Test
    fun `speech result uses highest available confidence`() {
        val result = bestSpeechResult(
            listOf("错误候选", "正确候选", "低分候选"),
            floatArrayOf(0.25f, 0.91f, 0.1f),
        )

        assertEquals("正确候选", result)
    }

    @Test
    fun `speech result falls back to first nonblank candidate without confidence`() {
        assertEquals("有效候选", bestSpeechResult(listOf(" ", "有效候选"), null))
        assertEquals(null, bestSpeechResult(emptyList(), null))
    }

    @Test
    fun `speech timeout and no match preserve the last partial transcript`() {
        assertEquals("临时识别文字", partialSpeechFallback(6, " 临时识别文字 "))
        assertEquals("临时识别文字", partialSpeechFallback(7, "临时识别文字"))
        assertEquals(null, partialSpeechFallback(7, " "))
        assertEquals(null, partialSpeechFallback(3, "临时识别文字"))
    }

    @Test
    fun `shortened final speech result keeps a complete partial prefix`() {
        assertEquals("不对，不是这样的", completeSpeechResult("对，不是这样的", "不对，不是这样的"))
        assertEquals("最终结果", completeSpeechResult("最终结果", "不相关的临时结果"))
        assertEquals("临时结果", completeSpeechResult(null, "临时结果"))
    }

    @Test
    fun speechChunkerEmitsPunctuationBoundedSegment() {
        val chunk = nextSpeechChunk("你好，这是第一句。这里是第二句", flush = false)

        assertEquals("你好，这是第一句。", chunk?.text)
        assertEquals(9, chunk?.consumedChars)
    }

    @Test
    fun speechChunkerWaitsForUsefulStreamingSegment() {
        assertEquals(null, nextSpeechChunk("这是短句", flush = false))
        assertEquals("这是短句", nextSpeechChunk("这是短句", flush = true)?.text)
    }

    @Test
    fun `speech chunker combines a short opening sentence for smoother playback`() {
        assertEquals(null, nextSpeechChunk("好的。", flush = false))
        assertEquals(
            "好的。下面继续说明。",
            nextSpeechChunk("好的。下面继续说明。还有后续", flush = false)?.text,
        )
    }

    @Test
    fun `authoritative assistant id replacement keeps speech playback position`() {
        val cursor = AssistantSpeechCursor("temporary-id", 3, "同一条完整回复。", 8)

        val reconciled = reconcileAssistantSpeechCursor(cursor, "server-id", 3, "同一条完整回复。")

        assertEquals("server-id", reconciled.messageId)
        assertEquals(8, reconciled.consumedChars)
    }

    @Test
    fun `new assistant turn replays even when content matches previous reply`() {
        val cursor = AssistantSpeechCursor("previous-id", 3, "好的。", 3)

        val reconciled = reconcileAssistantSpeechCursor(cursor, "next-id", 4, "好的。")

        assertEquals(0, reconciled.consumedChars)
    }

    @Test
    fun `authoritative assistant extension only speaks new suffix`() {
        val cursor = AssistantSpeechCursor("temporary-id", 3, "已经完成", 4)

        val reconciled = reconcileAssistantSpeechCursor(cursor, "server-id", 3, "已经完成。")

        assertEquals(4, reconciled.consumedChars)
    }

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
    fun `composition can send with text images or multiple files`() {
        assertEquals(false, canSendComposition("", emptyList(), emptyList()))
        assertEquals(true, canSendComposition("你好", emptyList(), emptyList()))
        assertEquals(true, canSendComposition("", listOf("image"), emptyList()))
        assertEquals(true, canSendComposition("", emptyList(), listOf("file-1", "file-2")))
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
    fun `voice call retries recoverable recognition errors`() {
        listOf(1, 2, 3, 5, 6, 7, 8, 10, 11).forEach { code ->
            assertEquals(true, shouldAutoRetrySpeech(code, microphonePermissionGranted = true))
        }
        assertEquals(false, shouldAutoRetrySpeech(4, microphonePermissionGranted = true))
        assertEquals(true, shouldAutoRetrySpeech(9, microphonePermissionGranted = true))
        assertEquals(false, shouldAutoRetrySpeech(9, microphonePermissionGranted = false))
        assertEquals("没有听清，请再说一次", speechRetryPrompt(7))
        assertEquals(null, speechRetryPrompt(6))
        assertEquals(null, speechRetryPrompt(7, speechDetected = false))
        assertEquals(120L, voiceListenRestartDelayMillis(7))
        assertEquals(500L, voiceListenRestartDelayMillis(3))
        assertEquals(1_000L, voiceListenRestartDelayMillis(2))
    }

    @Test
    fun `voice call surfaces local initialization failure instead of retrying forever`() {
        assertEquals(
            false,
            shouldAutoRetryPhoneSpeech(
                LOCAL_SPEECH_ERROR_INITIALIZATION,
                microphonePermissionGranted = true,
            ),
        )
        assertEquals(true, shouldAutoRetryPhoneSpeech(5, microphonePermissionGranted = true))
        assertEquals(
            true,
            shouldAutoRetryPhoneSpeech(
                SPEECH_RECOGNIZER_CALLBACK_TIMEOUT,
                microphonePermissionGranted = true,
            ),
        )
        assertEquals(false, shouldStopSpeechWatchdogRetries(2))
        assertEquals(true, shouldStopSpeechWatchdogRetries(3))
        assertEquals(true, shouldFinishInlineSpeechOnRelease(started = true, stillListening = true))
        assertEquals(false, shouldFinishInlineSpeechOnRelease(started = true, stillListening = false))
        assertEquals(false, shouldFinishInlineSpeechOnRelease(started = false, stillListening = true))
    }

    @Test
    fun `silent recognition endings reuse the active recognizer`() {
        assertEquals(false, shouldRecreateSpeechRecognizer(6))
        assertEquals(false, shouldRecreateSpeechRecognizer(7))
        listOf(3, 4, 5, 8, 11).forEach { code ->
            assertEquals(true, shouldRecreateSpeechRecognizer(code))
        }
    }

    @Test
    fun `playback barge in requires a sustained rise above route noise`() {
        val detector = PlaybackBargeInDetector()

        assertEquals(false, detector.observe(rmsDb = 0f, elapsedMillis = 0L, speakerphone = false))
        assertEquals(false, detector.observe(rmsDb = 3f, elapsedMillis = 20L, speakerphone = false))
        assertEquals(false, detector.observe(rmsDb = 3f, elapsedMillis = 130L, speakerphone = false))
        assertEquals(true, detector.observe(rmsDb = 3f, elapsedMillis = 190L, speakerphone = false))

        detector.reset()
        assertEquals(false, detector.observe(rmsDb = 2f, elapsedMillis = 0L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 20L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 130L, speakerphone = true))
        assertEquals(true, detector.observe(rmsDb = 5f, elapsedMillis = 190L, speakerphone = true))
    }

    @Test
    fun `steady loud startup establishes the playback noise baseline without triggering`() {
        val detector = PlaybackBargeInDetector()

        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 0L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 100L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 170L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 300L, speakerphone = true))
    }

    @Test
    fun `ambient baseline learned before playback detects speech from the first playback frame`() {
        val detector = PlaybackBargeInDetector()
        repeat(6) { detector.learnAmbient(rmsDb = 1f, speakerphone = true) }

        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 20L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 130L, speakerphone = true))
        assertEquals(true, detector.observe(rmsDb = 5f, elapsedMillis = 190L, speakerphone = true))
    }

    @Test
    fun `playback observations do not raise the learned ambient baseline`() {
        val detector = PlaybackBargeInDetector()
        detector.learnAmbient(rmsDb = 0f, speakerphone = false)

        repeat(30) { index ->
            assertEquals(
                false,
                detector.observe(rmsDb = 2.4f, elapsedMillis = index * 20L, speakerphone = false),
            )
        }
        assertEquals(false, detector.observe(rmsDb = 3f, elapsedMillis = 600L, speakerphone = false))
        assertEquals(false, detector.observe(rmsDb = 3f, elapsedMillis = 730L, speakerphone = false))
        assertEquals(true, detector.observe(rmsDb = 3f, elapsedMillis = 770L, speakerphone = false))
    }

    @Test
    fun `switching playback route requires a new conservative baseline`() {
        val detector = PlaybackBargeInDetector()
        detector.learnAmbient(rmsDb = 0f, speakerphone = false)

        assertEquals(false, detector.observe(rmsDb = 6f, elapsedMillis = 0L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 6f, elapsedMillis = 200L, speakerphone = true))
    }

    @Test
    fun `clear speech rise above loud startup noise still interrupts promptly`() {
        val detector = PlaybackBargeInDetector()

        assertEquals(false, detector.observe(rmsDb = 5f, elapsedMillis = 0L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 8f, elapsedMillis = 20L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 8f, elapsedMillis = 130L, speakerphone = true))
        assertEquals(true, detector.observe(rmsDb = 8f, elapsedMillis = 190L, speakerphone = true))
    }

    @Test
    fun `short playback noise does not trigger barge in`() {
        val detector = PlaybackBargeInDetector()

        assertEquals(false, detector.observe(rmsDb = 1f, elapsedMillis = 0L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 6f, elapsedMillis = 40L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 1f, elapsedMillis = 120L, speakerphone = true))
        assertEquals(false, detector.observe(rmsDb = 6f, elapsedMillis = 220L, speakerphone = true))
    }

    @Test
    fun `voice call distinguishes playback echo from user interruption`() {
        assertEquals(true, isLikelyPlaybackEcho("今天北京天气", "今天北京天气晴朗，最高温度二十六度。"))
        assertEquals(true, isLikelyPlaybackEcho("今天北京天气晴朗", "今天北京天气晴朗。"))
        assertEquals(false, isLikelyPlaybackEcho("帮我打开设置", "今天北京天气晴朗。"))
        assertEquals(false, isLikelyPlaybackEcho("好的", "好的，我来处理。"))
        assertEquals(true, isLikelyPlaybackEcho("今天北京天气晴郎最高温度二十六度", "今天北京天气晴朗，最高温度二十六度。"))
    }

    @Test
    fun `short playback echo is suppressed until independent acoustic speech is confirmed`() {
        assertEquals(
            true,
            shouldSuppressPlaybackEcho("好的", "好的，我来处理。", acousticUserSpeechConfirmed = false),
        )
        assertEquals(
            false,
            shouldSuppressPlaybackEcho("好的", "好的，我来处理。", acousticUserSpeechConfirmed = true),
        )
    }

    @Test
    fun `short phrase is accepted after the playback echo tail closes`() {
        assertEquals(
            false,
            shouldSuppressPlaybackEcho("好的", spoken = "", acousticUserSpeechConfirmed = false),
        )
    }

    @Test
    fun `recognition partial cannot pause playback without acoustic confirmation`() {
        assertEquals(false, shouldPauseAssistantForPartial(assistantPlaying = true, acousticUserSpeechConfirmed = false))
        assertEquals(true, shouldPauseAssistantForPartial(assistantPlaying = true, acousticUserSpeechConfirmed = true))
        assertEquals(false, shouldPauseAssistantForPartial(assistantPlaying = false, acousticUserSpeechConfirmed = true))
    }

    @Test
    fun `sustained route noise alone cannot confirm playback interruption`() {
        assertEquals(
            false,
            shouldConfirmPlaybackInterruption(
                acousticCandidate = true,
                recognized = "",
                spoken = "正在朗读的回答",
            ),
        )
        assertEquals(
            false,
            shouldConfirmPlaybackInterruption(
                acousticCandidate = true,
                recognized = "啊",
                spoken = "正在朗读的回答",
            ),
        )
    }

    @Test
    fun `recognized playback echo cannot confirm playback interruption`() {
        assertEquals(
            false,
            shouldConfirmPlaybackInterruption(
                acousticCandidate = true,
                recognized = "正在朗读",
                spoken = "正在朗读的回答",
            ),
        )
    }

    @Test
    fun `acoustic candidate with independent recognized speech confirms interruption`() {
        assertEquals(
            true,
            shouldConfirmPlaybackInterruption(
                acousticCandidate = true,
                recognized = "不对，请停一下",
                spoken = "正在朗读的回答",
            ),
        )
        assertEquals(
            false,
            shouldConfirmPlaybackInterruption(
                acousticCandidate = false,
                recognized = "不对，请停一下",
                spoken = "正在朗读的回答",
            ),
        )
    }

    @Test
    fun `unsupported new speech voice falls back for legacy gateway`() {
        val unsupported = HermesHttpException(400, "不支持该音色", "语音合成", "unsupported_voice")
        val unrelated = HermesHttpException(503, "语音服务不可用", "语音合成", "speech_failed")

        assertEquals(true, shouldFallbackToLegacySpeechVoice(unsupported, "xiaoyi"))
        assertEquals(false, shouldFallbackToLegacySpeechVoice(unsupported, "xiaoxiao"))
        assertEquals(false, shouldFallbackToLegacySpeechVoice(unrelated, "xiaoyi"))
    }

    @Test
    fun `speech synthesis retries transient gateway failures`() = runTest {
        var attempts = 0

        val audio = synthesizeSpeechWithRetry {
            attempts += 1
            if (attempts < 3) {
                throw HermesHttpException(503, "语音合成暂不可用", "语音合成", "speech_failed")
            }
            byteArrayOf(1, 2, 3)
        }

        assertEquals(3, attempts)
        assertArrayEquals(byteArrayOf(1, 2, 3), audio)
    }

    @Test
    fun `speech synthesis does not retry permanent request errors`() = runTest {
        var attempts = 0
        var caught: HermesHttpException? = null

        try {
            synthesizeSpeechWithRetry {
                attempts += 1
                throw HermesHttpException(400, "不支持该音色", "语音合成", "unsupported_voice")
            }
        } catch (error: HermesHttpException) {
            caught = error
        }

        assertEquals(1, attempts)
        assertEquals("unsupported_voice", caught?.serverCode)
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
        val secondFile = file.copy(id = "local-2", name = "notes.txt", uploadedId = "server-file-2")
        val message = optimisticUserMessage("说明文字", emptyList(), listOf(file, secondFile))

        assertEquals("说明文字", message.content)
        assertEquals(listOf(file, secondFile), message.files)
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
            .save("one", ComposerDraft("第一条", emptyList(), listOf("file-one")))
            .save("two", ComposerDraft("第二条", emptyList(), listOf("file-two", "file-three")))

        assertEquals("第一条", drafts.load("one").text)
        assertEquals("第二条", drafts.load("two").text)
        assertEquals(listOf("file-one"), drafts.load("one").fileIds)
        assertEquals(listOf("file-two", "file-three"), drafts.load("two").fileIds)
        assertEquals(setOf("one", "two"), drafts.keys())
        assertEquals("", drafts.load("missing").text)
    }

    @Test
    fun `lazy list latest index targets the final bottom spacer`() {
        assertEquals(11, latestLazyListIndex(10))
        assertEquals(11, prependAnchorLazyListIndex(10))
    }

    @Test
    fun `history paging prefetches before the exact top only after user scroll`() {
        assertEquals(false, shouldLoadOlderMessages(3, "session", 10, true, false, false, false))
        assertEquals(true, shouldLoadOlderMessages(3, "session", 10, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(4, "session", 10, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, "session", 10, false, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, "session", 10, true, true, false, true))
    }

    @Test
    fun `new local conversation never requests older history`() {
        assertEquals(false, shouldLoadOlderMessages(0, null, 0, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, null, 12, true, false, false, true))
        assertEquals(false, shouldLoadOlderMessages(0, "session", 0, true, false, false, true))
    }

    @Test
    fun `empty older page stops further history refreshes`() {
        assertEquals(false, nextHasMoreMessages(pageHasMore = true, receivedMessageCount = 0))
        assertEquals(false, nextHasMoreMessages(pageHasMore = false, receivedMessageCount = 20))
        assertEquals(true, nextHasMoreMessages(pageHasMore = true, receivedMessageCount = 20))
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
    fun `detached stream keeps the submitted optimistic message pair`() {
        val messages = listOf(
            app.nexus.mobile.network.ChatMessage("user", app.nexus.mobile.network.ChatRole.USER, "submitted before lock"),
            app.nexus.mobile.network.ChatMessage("assistant", app.nexus.mobile.network.ChatRole.ASSISTANT, "working")
        )

        val detached = messagesAfterSendTermination(
            messages,
            optimisticUserId = "user",
            optimisticAssistantId = "assistant",
            termination = SendTermination.DETACHED
        )

        assertEquals(messages, detached)
    }

    @Test
    fun `clearing submitted draft removes persisted text and attachments immediately`() {
        val drafts = ConversationDrafts()
            .save("session-1", ComposerDraft("submitted", listOf("image-1"), listOf("file-1")))
            .clear("session-1")

        assertEquals(ComposerDraft(), drafts.load("session-1"))
    }

    @Test
    fun `draft bundle persists multiple uploaded files`() {
        val files = listOf(
            PersistedDraftFile("file-1", "plan.md", "text/markdown", 123, "", "file-1", "/api/files/file-1"),
            PersistedDraftFile("file-2", "report.pdf", "application/pdf", 456, "", "file-2", "/api/files/file-2")
        )
        val bundle = PersistedDraftBundle(
            localDraftKey = "local-1",
            localDraftKeys = mapOf(
                "default" to "local-1",
                "work" to "local-work"
            ),
            drafts = mapOf(
                "session-1" to PersistedComposerDraft(
                    text = "pending",
                    images = listOf(PersistedDraftImage("image-1", "https://server/api/files/image-1", "image-1")),
                    files = files
                )
            )
        )

        val restored = decodeDraftBundle(encodeDraftBundle(bundle))
        val restoredDraft = restored.drafts.getValue("session-1")

        assertEquals(bundle, restored)
        assertEquals("local-work", restored.localDraftKeys["work"])
        assertEquals(true, restoredDraft.images.single().toImage().uploadState.readyToSend)
        assertEquals(listOf("file-1", "file-2"), restoredDraft.resolvedFiles().map { it.id })
        assertTrue(restoredDraft.resolvedFiles().all { it.toFile().uploadState.readyToSend })
    }

    @Test
    fun `legacy single file draft json migrates to the file list`() {
        val legacyJson = """{
            "localDraftKey":"local-legacy",
            "drafts":{
                "session-1":{
                    "text":"pending",
                    "images":[],
                    "file":{
                        "id":"legacy-file",
                        "name":"legacy.txt",
                        "mimeType":"text/plain",
                        "size":12,
                        "uri":"content://legacy",
                        "uploadedId":"uploaded-legacy",
                        "downloadUrl":"/api/files/uploaded-legacy"
                    }
                }
            }
        }"""

        val restored = decodeDraftBundle(legacyJson).drafts.getValue("session-1")

        assertEquals(listOf("legacy-file"), restored.resolvedFiles().map { it.id })
        assertEquals(true, restored.resolvedFiles().single().toFile().uploadState.readyToSend)
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
    fun `explicit Hermes model directory keeps available models`() {
        val models = listOf(
            HermesModel("primary-model", providerId = "local"),
            HermesModel("gpt-5.6-sol", providerId = "openai")
        )

        assertEquals(listOf("primary-model", "gpt-5.6-sol"), inferenceModels(models).map { it.id })
    }

    @Test
    fun `explicit Hermes model directory filters unavailable and duplicate provider models`() {
        val models = listOf(
            HermesModel("gpt-5.6-sol", providerId = "openai"),
            HermesModel("gpt-5.6-sol", providerId = "openai"),
            HermesModel("gpt-5.6-sol", providerId = "custom"),
            HermesModel("claude-sonnet", providerId = "anthropic", available = false)
        )

        assertEquals(
            listOf("openai:gpt-5.6-sol", "custom:gpt-5.6-sol"),
            inferenceModels(models).map { "${it.providerId}:${it.id}" },
        )
    }

    @Test
    fun `profile selection keeps configured profile and falls back to default`() {
        val profiles = listOf(
            HermesProfile("default", "Hermes 默认", isDefault = true),
            HermesProfile("profile-a", "工作")
        )

        assertEquals("profile-a", resolveSelectedProfileId(profiles, "profile-a"))
        assertEquals("default", resolveSelectedProfileId(profiles, "missing"))
        assertEquals("default", resolveSelectedProfileId(profiles, null))
        assertEquals("default", resolveSelectedProfileId(emptyList(), "missing"))
    }

    @Test
    fun `non-default profile scopes local conversation storage`() {
        assertEquals("session-1", profileScopedStorageKey("default", "session-1"))
        assertEquals("profile:work:session-1", profileScopedStorageKey("work", "session-1"))
    }

    @Test
    fun `inference selection falls back to the first actual model when missing`() {
        val fast = HermesModel("model-fast", providerId = "provider-a")
        val current = HermesModel("model-current", providerId = "provider-b", isCurrent = true)
        val inference = listOf(fast, current)

        assertEquals(fast, resolveSelectedInferenceModel(inference, "model-fast", "provider-a"))
        assertEquals(fast, resolveSelectedInferenceModel(inference, "model-fast", null))
        assertEquals(current, resolveSelectedInferenceModel(inference, "missing", "provider-a"))
        assertEquals(current, resolveSelectedInferenceModel(inference, null, null))
        assertEquals(null, resolveSelectedInferenceModel(emptyList(), null, null))
    }

    @Test
    fun `reasoning effort restores stored names and wire values`() {
        assertEquals(ReasoningEffort.DEFAULT, ReasoningEffort.fromStored(null))
        assertEquals(ReasoningEffort.HIGH, ReasoningEffort.fromStored("HIGH"))
        assertEquals(ReasoningEffort.XHIGH, ReasoningEffort.fromStored("xhigh"))
        assertEquals(null, ReasoningEffort.DEFAULT.wireValue)
        assertEquals(null, ConversationRuntimeConfig().reasoningEffort.wireValue)
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

    @Test
    fun `chat attachment payload includes every uploaded image and file`() {
        val images = listOf(
            app.nexus.mobile.network.ChatImage("image-local-1", "content://image-1", uploadedId = "image-1"),
            app.nexus.mobile.network.ChatImage("image-local-2", "content://image-2", uploadedId = "image-2")
        )
        val files = listOf(
            app.nexus.mobile.network.ChatFile("file-local-1", "one.txt", "text/plain", 1, "content://file-1", uploadedId = "file-1"),
            app.nexus.mobile.network.ChatFile("file-local-2", "two.pdf", "application/pdf", 2, "content://file-2", uploadedId = "file-2")
        )

        val payload = chatAttachmentPayload(images, files)

        assertEquals(listOf("image-1", "image-2", "file-1", "file-2"), payload.ids)
        assertEquals(mapOf("file-1" to "file", "file-2" to "file"), payload.kinds)
    }

    @Test
    fun `removing one pending file keeps every other file`() {
        val files = listOf(
            app.nexus.mobile.network.ChatFile("one", "one.txt", "text/plain", 1, "content://one"),
            app.nexus.mobile.network.ChatFile("two", "two.txt", "text/plain", 2, "content://two"),
            app.nexus.mobile.network.ChatFile("three", "three.txt", "text/plain", 3, "content://three")
        )

        assertEquals(listOf("one", "three"), removeFileAttachment(files, "two").map { it.id })
        assertEquals(files, removeFileAttachment(files, "missing"))
    }

    @Test
    fun `conversation runtime configs round trip independently`() {
        val bundle = PersistedRuntimeConfigBundle(
            schemaVersion = RUNTIME_CONFIG_SCHEMA_VERSION,
            localDraftKey = "draft-1",
            configs = mapOf(
                "session-a" to ConversationRuntimeConfig(
                    inferenceModelId = "gpt-fast",
                    reasoningEffort = ReasoningEffort.LOW,
                    inferenceProviderId = "openai-codex"
                ).toPersistedRuntimeConfig(),
                "session-b" to ConversationRuntimeConfig("gpt-deep", ReasoningEffort.XHIGH).toPersistedRuntimeConfig()
            )
        )

        val restored = decodeRuntimeConfigBundle(encodeRuntimeConfigBundle(bundle))

        assertEquals(bundle, restored)
        assertEquals("gpt-fast", restored.configs.getValue("session-a").toRuntimeConfig().inferenceModelId)
        assertEquals("openai-codex", restored.configs.getValue("session-a").toRuntimeConfig().inferenceProviderId)
        assertEquals(ReasoningEffort.XHIGH, restored.configs.getValue("session-b").toRuntimeConfig().reasoningEffort)
    }

    @Test
    fun `legacy runtime selection migrates exactly once`() {
        val first = migrateRuntimeConfigBundle(
            bundle = PersistedRuntimeConfigBundle(),
            activeSessionId = "session-a",
            localDraftKey = "draft-1",
            legacyInferenceModelId = "gpt-legacy",
            legacyReasoningEffort = ReasoningEffort.HIGH
        )
        val second = migrateRuntimeConfigBundle(
            bundle = first,
            activeSessionId = "session-b",
            localDraftKey = "draft-1",
            legacyInferenceModelId = "should-not-migrate-again",
            legacyReasoningEffort = ReasoningEffort.XHIGH
        )

        assertEquals(RUNTIME_CONFIG_SCHEMA_VERSION, second.schemaVersion)
        assertEquals(setOf("session-a"), second.configs.keys)
        assertEquals("gpt-legacy", second.configs.getValue("session-a").inferenceModelId)
        assertEquals("HIGH", second.configs.getValue("session-a").reasoningEffort)
    }

    @Test
    fun `live assistant id never creates a recovery snapshot bubble`() {
        val messages = listOf(
            app.nexus.mobile.network.ChatMessage("user", app.nexus.mobile.network.ChatRole.USER, "prompt")
        )

        val reconciled = reconcileRunSnapshot(messages, "assistant-live", "run-1", "snapshot")

        assertEquals(listOf("user"), reconciled.map { it.id })
    }

    @Test
    fun `removing a conversation draft does not affect other drafts`() {
        val drafts = ConversationDrafts()
            .save("one", ComposerDraft(text = "one", fileIds = listOf("file-one")))
            .save("two", ComposerDraft(text = "two", fileIds = listOf("file-two")))
            .remove("one")

        assertEquals(ComposerDraft(), drafts.load("one"))
        assertEquals("two", drafts.load("two").text)
        assertEquals(listOf("file-two"), drafts.load("two").fileIds)
    }

    @Test
    fun `active session preference is isolated per Hermes profile`() {
        assertEquals("active_session_id", activeSessionPreferenceKey("default"))
        assertEquals("profile:work:active_session_id", activeSessionPreferenceKey("work"))
        assertTrue(activeSessionPreferenceKey("work") != activeSessionPreferenceKey("personal"))
    }

}
