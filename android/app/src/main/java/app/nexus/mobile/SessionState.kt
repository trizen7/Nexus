package app.nexus.mobile

import app.nexus.mobile.network.ChatMessage
import app.nexus.mobile.network.ChatRole
import app.nexus.mobile.network.HermesCronJob
import app.nexus.mobile.network.HermesModel
import app.nexus.mobile.network.HermesSession
import app.nexus.mobile.network.SessionChannel

data class SessionGroup(val channel: SessionChannel, val sessions: List<HermesSession>)

data class VoiceTranscriptState(val text: String, val uploading: Boolean)

fun voiceTranscriptState(transcript: String): VoiceTranscriptState =
    VoiceTranscriptState(text = transcript.trim(), uploading = false)

fun visibleSessions(sessions: List<HermesSession>): List<HermesSession> =
    sessions.filterNot { it.channel == SessionChannel.CRON }

fun resolveVisibleActiveSessionId(
    sessions: List<HermesSession>,
    preferredSessionId: String?,
    chooseFirstWhenMissing: Boolean = true
): String? {
    val visible = visibleSessions(sessions)
    return preferredSessionId?.takeIf { id -> visible.any { it.id == id } }
        ?: visible.firstOrNull()?.id?.takeIf { chooseFirstWhenMissing }
}

fun personaModels(models: List<HermesModel>): List<HermesModel> =
    models.filter(HermesModel::isPersona)

fun inferenceModels(models: List<HermesModel>): List<HermesModel> =
    models.filter(HermesModel::isInferenceModel)

fun resolveSelectedPersonaModelId(models: List<HermesModel>, preferredModelId: String?): String? =
    preferredModelId?.takeIf { id -> models.any { it.id == id } } ?: models.firstOrNull()?.id

fun resolveSelectedInferenceModelId(models: List<HermesModel>, preferredModelId: String?): String? =
    preferredModelId?.takeIf { id -> models.any { it.id == id } }

fun isValidRepeatInput(value: String): Boolean =
    value.isBlank() || (value.toIntOrNull()?.let { it > 0 } == true)

fun repeatCountOrNull(value: String): Int? = value.trim().takeIf { it.isNotEmpty() }?.toIntOrNull()

fun groupSessionsByChannel(sessions: List<HermesSession>): List<SessionGroup> =
    visibleSessions(sessions)
        .groupBy { it.channel }
        .map { (channel, items) ->
            SessionGroup(channel, items.sortedByDescending { it.lastActive })
        }
        .sortedBy { it.channel.order }

fun initialExpandedChannels(groups: List<SessionGroup>, activeSessionId: String?): Set<SessionChannel> {
    val activeChannel = groups.firstOrNull { group ->
        group.sessions.any { it.id == activeSessionId }
    }?.channel
    return activeChannel?.let(::setOf) ?: groups.firstOrNull()?.channel?.let(::setOf).orEmpty()
}

fun toggleChannel(expanded: Set<SessionChannel>, channel: SessionChannel): Set<SessionChannel> =
    if (channel in expanded) expanded - channel else expanded + channel

fun defaultServerUrl(): String = ""

data class SavedConnection(
    val serverUrl: String,
    val username: String,
    val token: String,
    val activeSessionId: String?,
    val autoRefresh: Boolean = true,
    val themeMode: ThemeMode = ThemeMode.SYSTEM,
    val selectedPersonaModelId: String? = null,
    val selectedInferenceModelId: String? = null
) {
    val isUsable: Boolean
        get() = serverUrl.isNotBlank() && username.isNotBlank() && token.isNotBlank()
}

data class CronJobEditorState(
    val jobId: String? = null,
    val name: String = "",
    val schedule: String = "",
    val prompt: String = "",
    val repeatText: String = "",
    val enabled: Boolean = true,
    val completedRuns: Int = 0
) {
    val key: String
        get() = jobId ?: "new"

    companion object {
        fun create(): CronJobEditorState = CronJobEditorState()

        fun edit(job: HermesCronJob): CronJobEditorState = CronJobEditorState(
            jobId = job.id,
            name = job.name,
            schedule = job.schedule.editableValue,
            prompt = job.prompt,
            repeatText = job.repeatTimes?.toString().orEmpty(),
            enabled = !job.isPaused,
            completedRuns = job.completedRuns
        )
    }
}
data class ConversationDraft(
    val persistedSessionId: String?
) {
    val isDraft: Boolean
        get() = persistedSessionId == null

    fun persistedAs(sessionId: String): ConversationDraft = copy(persistedSessionId = sessionId)

    companion object {
        fun newDraft(): ConversationDraft = ConversationDraft(persistedSessionId = null)
    }
}

fun requiresPersistedSession(activeSessionId: String?, text: String): Boolean =
    activeSessionId == null && text.isNotBlank()

fun canSendMessage(text: String, imageIds: List<String>): Boolean =
    text.isNotBlank() || imageIds.isNotEmpty()

fun hasValidImageBounds(width: Int, height: Int): Boolean = width > 0 && height > 0

fun mergeAuthoritativeMessages(existing: List<ChatMessage>, incoming: List<ChatMessage>): List<ChatMessage> =
    if (incoming.size >= existing.size) incoming else existing

fun latestMessagePage(messages: List<ChatMessage>, pageSize: Int): List<ChatMessage> =
    messages.takeLast(pageSize.coerceAtLeast(1))

fun olderMessagePage(messages: List<ChatMessage>, alreadyLoaded: Int, pageSize: Int): List<ChatMessage> {
    val end = (messages.size - alreadyLoaded).coerceAtLeast(0)
    val start = (end - pageSize.coerceAtLeast(1)).coerceAtLeast(0)
    return messages.subList(start, end)
}

fun prependMessagePage(current: List<ChatMessage>, older: List<ChatMessage>): List<ChatMessage> =
    (older + current).distinctBy(ChatMessage::id)

fun latestLazyListIndex(messageCount: Int): Int = messageCount.coerceAtLeast(0) + 1

fun prependAnchorLazyListIndex(prependedMessageCount: Int): Int =
    prependAnchorLazyListIndex(previousFirstVisibleIndex = 1, prependedMessageCount = prependedMessageCount)

fun prependAnchorLazyListIndex(previousFirstVisibleIndex: Int, prependedMessageCount: Int): Int =
    previousFirstVisibleIndex.coerceAtLeast(0) + prependedMessageCount.coerceAtLeast(0)

fun shouldLoadOlderMessages(
    firstVisibleItemIndex: Int,
    hasMoreMessages: Boolean,
    loadingOlder: Boolean,
    loading: Boolean,
    userHasScrolled: Boolean
): Boolean = userHasScrolled && firstVisibleItemIndex <= 3 && hasMoreMessages && !loadingOlder && !loading

enum class ThemeMode(val label: String) {
    SYSTEM("跟随系统"),
    LIGHT("浅色"),
    DARK("深色");

    companion object {
        fun fromStored(value: String?): ThemeMode = entries.firstOrNull { it.name == value } ?: SYSTEM
    }
}

fun shouldUseDarkTheme(mode: ThemeMode, systemDark: Boolean): Boolean = when (mode) {
    ThemeMode.SYSTEM -> systemDark
    ThemeMode.LIGHT -> false
    ThemeMode.DARK -> true
}

fun shouldRenderMessageBubble(message: ChatMessage): Boolean =
    message.content.isNotBlank() || message.images.isNotEmpty() || message.files.isNotEmpty()

fun nextLoadedMessageCount(previousLoaded: Int, returnedCount: Int): Int =
    previousLoaded.coerceAtLeast(0) + returnedCount.coerceAtLeast(0)

fun draftTextAfterRunFailure(current: String, original: String, restore: Boolean): String =
    if (restore) restoreDraftText(current, original) else current

fun removeOptimisticMessages(
    messages: List<ChatMessage>,
    userMessageId: String?,
    assistantMessageId: String?
): List<ChatMessage> = messages.filterNot { it.id == userMessageId || it.id == assistantMessageId }

enum class SendTermination { COMPLETED, STOPPED_BY_USER, FAILED }

fun messagesAfterSendTermination(
    messages: List<ChatMessage>,
    optimisticUserId: String?,
    optimisticAssistantId: String?,
    termination: SendTermination
): List<ChatMessage> = when (termination) {
    SendTermination.COMPLETED -> messages
    SendTermination.FAILED -> removeOptimisticMessages(messages, optimisticUserId, optimisticAssistantId)
    SendTermination.STOPPED_BY_USER -> messages.filterNot { message ->
        message.id == optimisticAssistantId && message.content.isBlank() && message.images.isEmpty() && message.files.isEmpty()
    }
}

fun reconcileRunSnapshot(
    messages: List<ChatMessage>,
    liveAssistantId: String?,
    runId: String?,
    snapshot: String
): List<ChatMessage> {
    val withoutRecoverySnapshots = messages.filterNot { it.id.startsWith("run-snapshot-") }
    if (snapshot.isBlank()) return withoutRecoverySnapshots
    if (liveAssistantId != null && withoutRecoverySnapshots.any { it.id == liveAssistantId }) {
        return withoutRecoverySnapshots.map { message ->
            if (message.id == liveAssistantId) message.copy(content = snapshot) else message
        }
    }
    val snapshotId = "run-snapshot-${runId ?: "active"}"
    return withoutRecoverySnapshots + ChatMessage(snapshotId, ChatRole.ASSISTANT, snapshot)
}

fun compactNotificationFileName(name: String, maxLength: Int = 28): String {
    if (name.length <= maxLength.coerceAtLeast(5)) return name
    val extension = name.substringAfterLast('.', "").takeIf(String::isNotBlank)?.let { ".$it" }.orEmpty()
    val stem = name.removeSuffix(extension)
    val available = (maxLength - extension.length - 1).coerceAtLeast(4)
    val left = (available + 1) / 2
    val right = available / 2
    return stem.take(left) + "…" + stem.takeLast(right) + extension
}

enum class RunNotificationKind { NONE, RUNNING, COMPLETED, FAILED, STOPPED }

fun runNotificationKind(status: String, active: Boolean): RunNotificationKind = when {
    active || status in setOf("queued", "running", "stopping") -> RunNotificationKind.RUNNING
    status == "completed" -> RunNotificationKind.COMPLETED
    status in setOf("failed", "interrupted") -> RunNotificationKind.FAILED
    status == "stopped" -> RunNotificationKind.STOPPED
    else -> RunNotificationKind.NONE
}

fun applySessionLoadResult(
    requestedSessionId: String,
    activeSessionId: String?,
    current: List<ChatMessage>,
    incoming: List<ChatMessage>
): List<ChatMessage> = if (requestedSessionId == activeSessionId) incoming else current

fun acceptsSessionLoad(
    requestedSessionId: String,
    activeSessionId: String?,
    requestedGeneration: Long,
    currentGeneration: Long
): Boolean = requestedSessionId == activeSessionId && requestedGeneration == currentGeneration

fun cleanScreenshotMarker(text: String, hasImages: Boolean): String =
    if (!hasImages) text else text
        .replace("[screenshot]", "")
        .replace(Regex("\\n{3,}"), "\n\n")
        .trim()

private val downloadableExtensions = setOf(
    "apk", "pdf", "zip", "rar", "7z", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "md"
)

fun extractDownloadableLinks(text: String): List<String> =
    Regex("https?://[^\\s<>]+", RegexOption.IGNORE_CASE)
        .findAll(text)
        .map { it.value.trimEnd('.', ',', '，', '。', ')', '）') }
        .filter { url ->
            val extensionSource = url.substringAfterLast('#', url.substringBefore('?'))
            extensionSource.substringBefore('?').substringAfterLast('.', "").lowercase() in downloadableExtensions
        }
        .distinct()
        .toList()

fun linkedDownloadFileName(url: String): String {
    val routeName = url.substringBefore('#').substringBefore('?').substringAfterLast('/').ifBlank { "Nexus文件" }
    val fragment = url.substringAfterLast('#', "")
    val extension = fragment.substringBefore('?').takeIf { it.matches(Regex("\\.[A-Za-z0-9]{1,8}")) }.orEmpty()
    return if (extension.isNotBlank() && !routeName.endsWith(extension, ignoreCase = true)) routeName + extension else routeName
}

fun gatewayFileId(url: String): String? = Regex("/api/files/([^/?#]+)")
    .find(url)?.groupValues?.getOrNull(1)?.takeIf(String::isNotBlank)

fun resolveDownloadUrl(serverUrl: String, downloadUrl: String): String {
    val fragment = downloadUrl.substringAfter('#', "").takeIf(String::isNotBlank)?.let { "#$it" }.orEmpty()
    val requestPath = downloadUrl.substringBefore('#')
    val resolved = if (requestPath.startsWith("http://", ignoreCase = true) || requestPath.startsWith("https://", ignoreCase = true)) {
        requestPath
    } else {
        serverUrl.trimEnd('/') + "/" + requestPath.trimStart('/')
    }
    return resolved + fragment
}

fun isTextAttachment(name: String, mimeType: String?): Boolean {
    val extension = name.substringAfterLast('.', "").lowercase()
    return mimeType.orEmpty().startsWith("text/") || extension in setOf("txt", "md", "json", "csv", "xml", "yaml", "yml")
}

fun canSendComposition(text: String, imageIds: List<String>, hasFile: Boolean): Boolean =
    text.isNotBlank() || imageIds.isNotEmpty() || hasFile

fun friendlySpeechError(code: Int): String = when (code) {
    4, 5, 8, 10, 11, 12, 13 -> "语音识别服务暂不可用"
    6, 7 -> "没有听清，请重试"
    9 -> "没有麦克风权限"
    else -> "语音识别失败，请重试"
}

fun shouldFallbackToSystemSpeech(code: Int): Boolean = code in setOf(4, 5, 8, 10, 11, 12, 13)

fun restoreDraftText(current: String, original: String): String = current.ifBlank { original }

fun restoreDraftForSession(
    originSessionId: String?,
    currentSessionId: String?,
    currentText: String,
    originalText: String
): String = if (originSessionId == currentSessionId) restoreDraftText(currentText, originalText) else currentText

fun optimisticUserMessage(
    text: String,
    images: List<app.nexus.mobile.network.ChatImage>,
    file: app.nexus.mobile.network.ChatFile?
): ChatMessage = ChatMessage(
    id = java.util.UUID.randomUUID().toString(),
    role = app.nexus.mobile.network.ChatRole.USER,
    content = text,
    images = images,
    files = file?.let(::listOf).orEmpty()
)

sealed interface AttachmentUploadState {
    val readyToSend: Boolean
        get() = this is Ready

    data object Local : AttachmentUploadState
    data class Uploading(val progress: Int) : AttachmentUploadState
    data class Ready(val fileId: String) : AttachmentUploadState
    data class Paused(val progress: Int) : AttachmentUploadState
    data class Failed(val message: String) : AttachmentUploadState
}

enum class DownloadStatus { NOT_DOWNLOADED, DOWNLOADING, PAUSED, COMPLETED, FAILED }

enum class AnswerStatus(val label: String?) {
    IDLE(null),
    THINKING("思考中…"),
    TOOL("正在使用工具…"),
    GENERATING("正在生成回答…"),
    COMPLETED("回答完成"),
    STOPPED("已停止"),
    FAILED("回答失败")
}

data class FileDownloadState(
    val key: String,
    val status: DownloadStatus = DownloadStatus.NOT_DOWNLOADED,
    val progress: Int = 0,
    val localPath: String? = null,
    val error: String? = null,
    val sessionId: String? = null
)

data class TransferNotificationTarget(val key: String, val sessionId: String?)

fun downloadTransferNotificationTarget(state: FileDownloadState): TransferNotificationTarget =
    TransferNotificationTarget(state.key, state.sessionId)

fun downloadTransferNotificationId(state: FileDownloadState): Int {
    val target = downloadTransferNotificationTarget(state)
    return NotificationHelper.transferNotificationId(target.sessionId, target.key)
}

data class ComposerDraft(
    val text: String = "",
    val imageIds: List<String> = emptyList(),
    val fileId: String? = null
)

data class ConversationDrafts(private val values: Map<String, ComposerDraft> = emptyMap()) {
    fun save(sessionKey: String, draft: ComposerDraft): ConversationDrafts =
        copy(values = values + (sessionKey to draft))

    fun load(sessionKey: String): ComposerDraft = values[sessionKey] ?: ComposerDraft()
}

fun deriveSessionTitle(text: String, fileName: String? = null, hasImage: Boolean = false): String {
    val firstLine = text.lineSequence().map(String::trim).firstOrNull(String::isNotEmpty).orEmpty()
    return when {
        firstLine.isNotBlank() -> firstLine.replace(Regex("[#*_`]+"), "").take(24)
        !fileName.isNullOrBlank() -> "查看${fileName.take(20)}"
        hasImage -> "图片分析"
        else -> "新对话"
    }
}

private val hiddenRuntimePrefixes = listOf(
    "[CONTEXT COMPACTION", "[IMPORTANT: You are running as a scheduled cron job", "[System:"
)

fun isVisibleUserMessage(text: String): Boolean {
    val normalized = text.trimStart()
    return normalized.isNotBlank() && hiddenRuntimePrefixes.none { normalized.startsWith(it, ignoreCase = true) }
}

fun genericConnectionInterruptedMessage(): String = "网络连接中断，请稍后重试"

data class SessionState(
    val sessions: List<HermesSession> = emptyList(),
    val activeSessionId: String? = null
) {
    val activeSession: HermesSession?
        get() = sessions.firstOrNull { it.id == activeSessionId }

    fun select(sessionId: String): SessionState = copy(activeSessionId = sessionId)
}
