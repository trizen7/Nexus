package app.nexus.mobile

import android.content.Context
import android.net.Uri
import android.provider.OpenableColumns
import app.nexus.mobile.network.ChatFile
import app.nexus.mobile.network.UploadSource
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.UUID

object FileProcessor {
    private const val MAX_FILE_BYTES = 50L * 1024 * 1024

    suspend fun prepare(context: Context, uri: Uri): ChatFile = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        var name = "附件"
        var size = 0L
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use { cursor ->
            if (cursor.moveToFirst()) {
                val nameIndex = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                val sizeIndex = cursor.getColumnIndex(OpenableColumns.SIZE)
                if (nameIndex >= 0) name = cursor.getString(nameIndex) ?: name
                if (sizeIndex >= 0 && !cursor.isNull(sizeIndex)) size = cursor.getLong(sizeIndex)
            }
        }
        require(size <= MAX_FILE_BYTES) { "文件不能超过 50MB" }
        val mime = resolver.getType(uri)
        ChatFile(UUID.randomUUID().toString(), name, mime, size, uri.toString())
    }

    fun uploadSource(context: Context, file: ChatFile): UploadSource = UploadSource(
        name = file.name,
        mimeType = file.mimeType,
        size = file.size,
        openStream = {
            context.contentResolver.openInputStream(Uri.parse(file.uri)) ?: error("无法读取文件")
        }
    )

    fun formatSize(bytes: Long): String = when {
        bytes >= 1024 * 1024 -> "%.1f MB".format(bytes / 1024.0 / 1024.0)
        bytes >= 1024 -> "%.1f KB".format(bytes / 1024.0)
        else -> "$bytes B"
    }
}
