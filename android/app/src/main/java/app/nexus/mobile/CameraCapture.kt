package app.nexus.mobile

import android.content.Context
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File

fun cameraImageFileName(timestamp: Long): String = "nexus-camera-$timestamp.jpg"

object CameraCapture {
    fun createUri(context: Context): Uri {
        val directory = File(context.cacheDir, "camera").apply { mkdirs() }
        val file = File(directory, cameraImageFileName(System.currentTimeMillis()))
        return FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
    }

    fun cleanup(context: Context, keepUri: Uri? = null) {
        val keepName = keepUri?.lastPathSegment?.substringAfterLast('/')
        File(context.cacheDir, "camera").listFiles()?.forEach { file ->
            if (file.name != keepName && (System.currentTimeMillis() - file.lastModified() > 24 * 60 * 60 * 1000L)) {
                file.delete()
            }
        }
    }
}
