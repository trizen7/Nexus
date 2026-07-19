package app.nexus.mobile.network

enum class SessionChannel(val label: String, val order: Int) {
    API("API / 手机", 0),
    PC("电脑端", 1),
    CRON("定时任务", 2),
    WEIXIN("微信", 3),
    QQ("QQ", 4),
    TELEGRAM("Telegram", 5),
    DISCORD("Discord", 6),
    OTHER("其他渠道", 99)
}

data class HermesSession(
    val id: String,
    val title: String?,
    val source: String,
    val messageCount: Int,
    val lastActive: Double
) {
    val displayTitle: String
        get() = title?.takeIf { it.isNotBlank() } ?: "新对话"

    val channel: SessionChannel
        get() = when (source.lowercase()) {
            "api_server" -> SessionChannel.API
            "desktop", "cli", "tui" -> SessionChannel.PC
            "cron" -> SessionChannel.CRON
            "weixin", "wechat" -> SessionChannel.WEIXIN
            "qqbot", "qq" -> SessionChannel.QQ
            "telegram" -> SessionChannel.TELEGRAM
            "discord" -> SessionChannel.DISCORD
            else -> SessionChannel.OTHER
        }
}

enum class ChatRole { USER, ASSISTANT, TOOL, OTHER }

data class ChatImage(
    val id: String,
    val previewUri: String,
    val dataUrl: String = "",
    val uploadBytes: ByteArray? = null,
    val uploadedId: String? = null,
    val uploadState: app.nexus.mobile.AttachmentUploadState = app.nexus.mobile.AttachmentUploadState.Local
)

data class ChatFile(
    val id: String,
    val name: String,
    val mimeType: String?,
    val size: Long,
    val uri: String,
    val embeddedText: String? = null,
    val uploadedId: String? = null,
    val downloadUrl: String? = null,
    val uploadState: app.nexus.mobile.AttachmentUploadState = app.nexus.mobile.AttachmentUploadState.Local
)

data class UploadSource(
    val name: String,
    val mimeType: String?,
    val size: Long,
    val openStream: () -> java.io.InputStream
)

data class UploadedFile(
    val id: String,
    val name: String,
    val mimeType: String?,
    val size: Long,
    val downloadUrl: String
)

data class TranscribedAudio(
    val transcript: String,
    val file: UploadedFile
)

data class ChatMessage(
    val id: String,
    val role: ChatRole,
    val content: String,
    val images: List<ChatImage> = emptyList(),
    val files: List<ChatFile> = emptyList()
)

data class MessagePage(
    val messages: List<ChatMessage>,
    val total: Int,
    val offset: Int,
    val limit: Int,
    val hasMore: Boolean
)

data class SessionRunStatus(
    val sessionId: String,
    val runId: String?,
    val status: String,
    val active: Boolean,
    val phase: String = "idle",
    val snapshot: String = "",
    val toolName: String? = null,
    val message: String? = null
)
