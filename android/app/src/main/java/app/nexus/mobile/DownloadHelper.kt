package app.nexus.mobile

import android.content.Context
import android.content.Intent
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.RandomAccessFile
import java.util.concurrent.TimeUnit
import kotlin.coroutines.coroutineContext

object DownloadHelper {
    private val client = OkHttpClient.Builder()
        .connectTimeout(30, TimeUnit.SECONDS)
        .readTimeout(5, TimeUnit.MINUTES)
        .build()
    private val activeCalls = java.util.concurrent.ConcurrentHashMap<String, Call>()

    suspend fun download(
        context: Context,
        key: String,
        url: String,
        token: String?,
        fileName: String,
        onProgress: (Int) -> Unit
    ): File = withContext(Dispatchers.IO) {
        val directory = downloadDirectory(context, key).apply { mkdirs() }
        val target = File(directory, safeFileName(fileName))
        val partial = File(target.absolutePath + ".part")
        val existing = partial.takeIf(File::exists)?.length() ?: 0L
        val requestUrl = url.substringBefore('#')
        val request = Request.Builder().url(requestUrl).apply {
            token?.takeIf(String::isNotBlank)?.let { header("Authorization", "Bearer $it") }
            if (existing > 0) header("Range", "bytes=$existing-")
        }.build()
        val call = client.newCall(request)
        activeCalls[key] = call
        try {
            call.execute().use { response ->
                if (!response.isSuccessful) error("下载失败：HTTP ${response.code}")
                val body = response.body ?: error("下载内容为空")
                val append = existing > 0 && response.code == 206
                val start = if (append) existing else 0L
                val total = body.contentLength().takeIf { it >= 0 }?.plus(start) ?: -1L
                RandomAccessFile(partial, "rw").use { output ->
                    if (append) output.seek(start) else output.setLength(0)
                    var downloaded = start
                    body.byteStream().use { input ->
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        while (true) {
                            coroutineContext.ensureActive()
                            val count = input.read(buffer)
                            if (count < 0) break
                            output.write(buffer, 0, count)
                            downloaded += count
                            if (total > 0) onProgress(((downloaded * 100) / total).toInt().coerceIn(0, 100))
                        }
                    }
                }
            }
        } finally {
            activeCalls.remove(key, call)
        }
        if (target.exists()) target.delete()
        check(partial.renameTo(target)) { "无法保存下载文件" }
        onProgress(100)
        target
    }

    fun openWithChooser(context: Context, file: File, mimeType: String?) {
        val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, mimeType ?: "application/octet-stream")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(Intent.createChooser(intent, "选择打开方式").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }

    fun deleteLocal(path: String?): Boolean {
        val file = path?.let(::File) ?: return false
        val deleted = file.delete()
        file.parentFile?.takeIf { it.name.startsWith("item-") }?.delete()
        return deleted
    }

    fun cancel(context: Context, key: String) {
        activeCalls.remove(key)?.cancel()
        downloadDirectory(context, key).deleteRecursively()
    }

    fun pause(key: String) {
        activeCalls.remove(key)?.cancel()
    }

    private fun safeFileName(name: String): String =
        name.replace(Regex("[\\\\/:*?\"<>|]"), "_").ifBlank { "Nexus文件" }

    private fun downloadDirectory(context: Context, key: String): File =
        File(File(context.filesDir, "downloads"), "item-${key.hashCode().toUInt().toString(16)}")
}
