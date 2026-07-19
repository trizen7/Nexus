package app.nexus.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.net.Uri
import app.nexus.mobile.network.ChatImage
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.util.UUID
import kotlin.math.max

object ImageProcessor {
    suspend fun prepare(context: Context, uri: Uri): ChatImage = withContext(Dispatchers.IO) {
        val resolver = context.contentResolver
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        val boundsStream = resolver.openInputStream(uri) ?: error("无法打开图片")
        boundsStream.use { BitmapFactory.decodeStream(it, null, bounds) }
        if (!hasValidImageBounds(bounds.outWidth, bounds.outHeight)) error("无法识别图片格式")

        val sample = calculateSample(bounds.outWidth, bounds.outHeight, 1280)
        val options = BitmapFactory.Options().apply { inSampleSize = sample }
        val bitmap = resolver.openInputStream(uri)?.use { BitmapFactory.decodeStream(it, null, options) }
            ?: error("无法解析图片")
        try {
            val bytes = ByteArrayOutputStream().use { output ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 78, output)
                output.toByteArray()
            }
            ChatImage(
                id = UUID.randomUUID().toString(),
                previewUri = uri.toString(),
                uploadBytes = bytes
            )
        } finally {
            bitmap.recycle()
        }
    }

    suspend fun prepare(bitmap: Bitmap): ChatImage = withContext(Dispatchers.Default) {
        val bytes = ByteArrayOutputStream().use { output ->
            bitmap.compress(Bitmap.CompressFormat.JPEG, 85, output)
            output.toByteArray()
        }
        ChatImage(
            id = UUID.randomUUID().toString(),
            previewUri = "data:image/jpeg;base64," + android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP),
            uploadBytes = bytes
        )
    }

    internal fun calculateSample(width: Int, height: Int, maxSide: Int): Int {
        var sample = 1
        var current = max(width, height)
        while (current / 2 >= maxSide) {
            sample *= 2
            current /= 2
        }
        return sample
    }
}
