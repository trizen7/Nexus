package app.nexus.mobile

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.content.ContextCompat
import app.nexus.mobile.network.HermesApiClient
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.coroutines.coroutineContext

class RunMonitorService : Service() {
    private data class Monitor(val job: Job, val title: String)

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val monitorJobs = SessionMonitorRegistry<Monitor>()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL).orEmpty()
        val token = intent?.getStringExtra(EXTRA_TOKEN).orEmpty()
        val sessionId = intent?.getStringExtra(EXTRA_SESSION_ID).orEmpty()
        val title = intent?.getStringExtra(EXTRA_TITLE).orEmpty().ifBlank { "Nexus" }
        if (serverUrl.isBlank() || token.isBlank() || sessionId.isBlank()) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        NotificationHelper.ensureChannels(this)
        NotificationHelper.showRun(this@RunMonitorService, sessionId, title, RunNotificationKind.RUNNING)
        startForeground(NotificationHelper.answerForegroundNotificationId(), NotificationHelper.runNotification(this@RunMonitorService, sessionId, title, RunNotificationKind.RUNNING))
        val job = scope.launch(start = CoroutineStart.LAZY) {
            val client = HermesApiClient(serverUrl, token)
            while (isActive) {
                val status = runCatching { client.getSessionRunStatus(sessionId) }.getOrNull()
                if (status != null) {
                    val kind = runNotificationKind(status.status, status.active)
                    if (kind != RunNotificationKind.RUNNING && kind != RunNotificationKind.NONE) {
                        NotificationHelper.showRun(this@RunMonitorService, sessionId, title, kind)
                        val removal = monitorJobs.remove(sessionId, Monitor(coroutineContext[Job]!!, title))
                        if (removal.nextOwner == null) {
                            stopForeground(STOP_FOREGROUND_REMOVE)
                            stopSelf()
                        } else if (removal.wasOwner) {
                            val next = removal.nextOwner
                            startForeground(
                                NotificationHelper.answerForegroundNotificationId(),
                                NotificationHelper.runNotification(this@RunMonitorService, next.sessionId, next.value.title, RunNotificationKind.RUNNING)
                            )
                        }
                        break
                    }
                }
                delay(4_000)
            }
        }
        monitorJobs.put(sessionId, Monitor(job, title))?.job?.cancel()
        job.start()
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        monitorJobs.values().forEach { it.job.cancel() }
        monitorJobs.clear()
        scope.cancel()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    companion object {
        private const val EXTRA_SERVER_URL = "server_url"
        private const val EXTRA_TOKEN = "token"
        private const val EXTRA_SESSION_ID = "session_id"
        private const val EXTRA_TITLE = "title"

        fun start(context: Context, serverUrl: String, token: String, sessionId: String, title: String) {
            val intent = Intent(context, RunMonitorService::class.java).apply {
                putExtra(EXTRA_SERVER_URL, serverUrl)
                putExtra(EXTRA_TOKEN, token)
                putExtra(EXTRA_SESSION_ID, sessionId)
                putExtra(EXTRA_TITLE, title)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun cancelAll(context: Context) {
            context.stopService(Intent(context, RunMonitorService::class.java))
        }
    }
}
