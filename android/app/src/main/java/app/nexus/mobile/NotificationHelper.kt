package app.nexus.mobile

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.net.Uri
import java.nio.charset.StandardCharsets
import java.util.Base64

import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat

object NotificationHelper {
    private const val TRANSFER_CHANNEL = "nexus_transfers"
    private const val ANSWER_CHANNEL = "nexus_answers_v2"
    private const val HASH_MASK = 0x1fffffff
    private const val ANSWER_NOTIFICATION_TYPE = 0x20000000
    private const val TRANSFER_NOTIFICATION_TYPE = 0x40000000
    private const val FOREGROUND_NOTIFICATION_ID = 0x60000000

    fun ensureChannels(context: Context) {
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(TRANSFER_CHANNEL, "文件传输", NotificationManager.IMPORTANCE_LOW).apply {
                description = "显示Nexus文件上传和下载进度"
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
            }
        )
        manager.createNotificationChannel(
            NotificationChannel(ANSWER_CHANNEL, "回答提醒", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "Nexus完成回答或执行失败时提醒"
                lockscreenVisibility = android.app.Notification.VISIBILITY_PRIVATE
            }
        )
    }

    fun showTransfer(
        context: Context,
        key: String,
        title: String,
        progress: Int,
        uploading: Boolean,
        sessionId: String? = null
    ) {
        if (!canNotify(context)) return
        ensureChannels(context)
        val text = if (uploading) "正在上传 $progress%" else "正在下载 $progress%"
        notify(
            context,
            transferId(sessionId, key),
            NotificationCompat.Builder(context, TRANSFER_CHANNEL)
                .setSmallIcon(if (uploading) android.R.drawable.stat_sys_upload else android.R.drawable.stat_sys_download)
                .setContentTitle(compactNotificationFileName(title))
                .setContentText(text)
                .setOnlyAlertOnce(true)
                .setOngoing(progress < 100)
                .setProgress(100, progress.coerceIn(0, 100), false)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setContentIntent(openAppIntent(context, type = PendingIntentType.TRANSFER, sessionId = sessionId, fileKey = key))
                .build()
        )
    }

    fun finishTransfer(
        context: Context,
        key: String,
        title: String,
        uploading: Boolean,
        success: Boolean,
        sessionId: String? = null
    ) {
        if (!canNotify(context)) return
        ensureChannels(context)
        val action = if (uploading) "上传" else "下载"
        val text = if (success) "${action}完成" else "${action}失败"
        notify(
            context,
            transferId(sessionId, key),
            NotificationCompat.Builder(context, TRANSFER_CHANNEL)
                .setSmallIcon(if (success) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
                .setContentTitle(compactNotificationFileName(title))
                .setContentText(text)
                .setProgress(0, 0, false)
                .setOngoing(false)
                .setOnlyAlertOnce(false)
                .setAutoCancel(true)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setContentIntent(openAppIntent(context, type = PendingIntentType.TRANSFER, sessionId = sessionId, fileKey = key))
                .build()
        )
    }

    fun showPausedTransfer(
        context: Context,
        key: String,
        title: String,
        progress: Int,
        sessionId: String? = null
    ) {
        if (!canNotify(context)) return
        ensureChannels(context)
        notify(
            context,
            transferId(sessionId, key),
            NotificationCompat.Builder(context, TRANSFER_CHANNEL)
                .setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle(compactNotificationFileName(title))
                .setContentText("下载已暂停 · $progress%")
                .setProgress(100, progress.coerceIn(0, 100), false)
                .setOngoing(false)
                .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
                .setContentIntent(openAppIntent(context, type = PendingIntentType.TRANSFER, sessionId = sessionId, fileKey = key))
                .build()
        )
    }

    fun cancelTransfer(context: Context, key: String, sessionId: String? = null) {
        NotificationManagerCompat.from(context).cancel(transferId(sessionId, key))
    }

    fun showRun(context: Context, sessionId: String, title: String, kind: RunNotificationKind) {
        if (!canNotify(context) || kind == RunNotificationKind.NONE) return
        ensureChannels(context)
        notify(
            context,
            answerNotificationId(sessionId),
            runNotification(context, sessionId, title, kind)
        )
    }

    fun runNotification(context: Context, sessionId: String, title: String, kind: RunNotificationKind): android.app.Notification {
        val (text, ongoing) = when (kind) {
            RunNotificationKind.RUNNING -> "Nexus正在处理…" to true
            RunNotificationKind.COMPLETED -> "回答已完成" to false
            RunNotificationKind.FAILED -> "回答失败，点击查看" to false
            RunNotificationKind.STOPPED -> "回答已停止" to false
            RunNotificationKind.NONE -> "" to false
        }
        return NotificationCompat.Builder(context, ANSWER_CHANNEL)
            .setSmallIcon(if (kind == RunNotificationKind.FAILED) android.R.drawable.stat_notify_error else android.R.drawable.stat_notify_chat)
            .setContentTitle(title)
            .setContentText(text)
            .setOngoing(ongoing)
            .setOnlyAlertOnce(ongoing)
            .setAutoCancel(!ongoing)
            .setVisibility(NotificationCompat.VISIBILITY_PRIVATE)
            .setContentIntent(openAppIntent(context, type = PendingIntentType.ANSWER, sessionId = sessionId))
            .build()
    }

    fun answerNotificationId(sessionId: String): Int = ANSWER_NOTIFICATION_TYPE or typedHash(sessionId)

    fun answerForegroundNotificationId(): Int = FOREGROUND_NOTIFICATION_ID

    fun transferNotificationId(sessionId: String?, key: String): Int =
        TRANSFER_NOTIFICATION_TYPE or typedHash("${sessionId.orEmpty()}|$key")

    private fun transferId(sessionId: String?, key: String): Int = transferNotificationId(sessionId, key)

    enum class PendingIntentType(val prefix: Int) {
        ANSWER(1),
        TRANSFER(2);

        companion object {
            fun fromPrefix(prefix: Int): PendingIntentType = entries.firstOrNull { it.prefix == prefix } ?: ANSWER
        }
    }

    fun pendingIntentRequestCode(type: PendingIntentType, sessionId: String?, fileKey: String?): Int {
        val seed = type.prefix.toString() + "|" + (sessionId ?: "") + "|" + (fileKey ?: "")
        return fnv32(seed)
    }

    fun pendingIntentData(type: PendingIntentType, sessionId: String?, fileKey: String?): String {
        fun encode(value: String?): String = Base64.getUrlEncoder().withoutPadding()
            .encodeToString(value.orEmpty().toByteArray(StandardCharsets.UTF_8))
        return "nexus://notification/${type.name.lowercase()}/${encode(sessionId)}/${encode(fileKey)}"
    }

    fun cancelAll(context: Context) {
        NotificationManagerCompat.from(context).cancelAll()
    }

    private fun canNotify(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 || context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    @SuppressLint("MissingPermission")
    private fun notify(context: Context, id: Int, notification: android.app.Notification) {
        try {
            NotificationManagerCompat.from(context).notify(id, notification)
        } catch (_: SecurityException) {
            // Notification permission may be revoked while a transfer is running.
        }
    }

    private fun openAppIntent(
        context: Context,
        type: PendingIntentType,
        sessionId: String? = null,
        fileKey: String? = null
    ): PendingIntent {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            data = Uri.parse(pendingIntentData(type, sessionId, fileKey))
            sessionId?.let { putExtra(EXTRA_SESSION_ID, it) }
            fileKey?.let { putExtra(EXTRA_FILE_KEY, it) }
        }
        return PendingIntent.getActivity(
            context,
            pendingIntentRequestCode(type, sessionId, fileKey),
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    private fun fnv32(value: String): Int {
        var hash = 0x811c9dc5L
        for (byte in value.toByteArray(Charsets.UTF_8)) {
            hash = (hash xor (byte.toInt() and 0xff).toLong()) * 0x01000193L
            hash = hash and 0xffffffffL
        }
        return (hash and 0x7fffffffL).toInt()
    }

    private fun typedHash(value: String): Int = fnv32(value) and HASH_MASK

    const val EXTRA_SESSION_ID = "nexus_notification_session_id"
    const val EXTRA_FILE_KEY = "nexus_notification_file_key"
}
