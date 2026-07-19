package app.nexus.mobile

import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import app.nexus.mobile.network.ChatFile
import app.nexus.mobile.network.ChatImage
import app.nexus.mobile.network.ChatMessage
import app.nexus.mobile.network.ChatRole

private data class CachedImage(val id: String, val uri: String) {
    fun toImage() = ChatImage(id, uri, uri)
}

private data class CachedFile(
    val id: String,
    val name: String,
    val mimeType: String?,
    val size: Long,
    val downloadUrl: String?
) {
    fun toFile() = ChatFile(id, name, mimeType, size, "", uploadedId = id, downloadUrl = downloadUrl)
}

private data class CachedMessage(
    val id: String,
    val role: String,
    val content: String,
    val images: List<CachedImage>,
    val files: List<CachedFile>
) {
    fun toMessage() = ChatMessage(
        id,
        runCatching { ChatRole.valueOf(role) }.getOrDefault(ChatRole.OTHER),
        content,
        images.map(CachedImage::toImage),
        files.map(CachedFile::toFile)
    )

    companion object {
        fun from(message: ChatMessage) = CachedMessage(
            message.id,
            message.role.name,
            message.content,
            message.images.map { CachedImage(it.id, it.previewUri) },
            message.files.map { CachedFile(it.id, it.name, it.mimeType, it.size, it.downloadUrl) }
        )
    }
}

fun encodeMessageCache(messages: List<ChatMessage>): String = Gson().toJson(messages.map(CachedMessage::from))

fun decodeMessageCache(json: String): List<ChatMessage> = runCatching {
    val type = object : TypeToken<List<CachedMessage>>() {}.type
    Gson().fromJson<List<CachedMessage>>(json, type).orEmpty().map(CachedMessage::toMessage)
}.getOrDefault(emptyList())
