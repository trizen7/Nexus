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

class RunMonitorService : Service() {
    private data class Monitor(
        val job: Job,
        val title: String,
        val sessionId: String,
        val profileId: String
    )

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val monitorJobs = SessionMonitorRegistry<Monitor>()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val serverUrl = intent?.getStringExtra(EXTRA_SERVER_URL).orEmpty()
        val token = intent?.getStringExtra(EXTRA_TOKEN).orEmpty()
        val sessionId = intent?.getStringExtra(EXTRA_SESSION_ID).orEmpty()
        val profileId = intent?.getStringExtra(EXTRA_PROFILE_ID).orEmpty().ifBlank { "default" }
        val monitorId = profileScopedStorageKey(profileId, sessionId)
        val title = intent?.getStringExtra(EXTRA_TITLE).orEmpty().ifBlank { "Nexus" }
        if (serverUrl.isBlank() || token.isBlank() || sessionId.isBlank()) {
            stopSelf(startId)
            return START_NOT_STICKY
        }
        NotificationHelper.ensureChannels(this)
        NotificationHelper.showRun(
            this@RunMonitorService,
            monitorId,
            title,
            RunNotificationKind.RUNNING,
            sessionId,
            profileId
        )
        startForeground(
            NotificationHelper.answerForegroundNotificationId(),
            NotificationHelper.runNotification(
                this@RunMonitorService,
                monitorId,
                title,
                RunNotificationKind.RUNNING,
                sessionId,
                profileId
            )
        )
        lateinit var monitor: Monitor
        val job = scope.launch(start = CoroutineStart.LAZY) {
            val client = HermesApiClient(serverUrl, token)
            client.selectProfile(profileId)
            while (isActive) {
                val status = runCatching { client.getSessionRunStatus(sessionId) }.getOrNull()
                if (status != null) {
                    val kind = runNotificationKind(status.status, status.active)
                    if (kind != RunNotificationKind.RUNNING && kind != RunNotificationKind.NONE) {
                        NotificationHelper.showRun(
                            this@RunMonitorService,
                            monitorId,
                            title,
                            kind,
                            sessionId,
                            profileId
                        )
                        val removal = monitorJobs.remove(monitorId, monitor)
                        if (removal.nextOwner == null) {
                            stopForeground(STOP_FOREGROUND_REMOVE)
                            stopSelf()
                        } else if (removal.wasOwner) {
                            val next = removal.nextOwner
                            startForeground(
                                NotificationHelper.answerForegroundNotificationId(),
                                NotificationHelper.runNotification(
                                    this@RunMonitorService,
                                    next.sessionId,
                                    next.value.title,
                                    RunNotificationKind.RUNNING,
                                    next.value.sessionId,
                                    next.value.profileId
                                )
                            )
                        }
                        break
                    }
                }
                delay(4_000)
            }
        }
        monitor = Monitor(job, title, sessionId, profileId)
        monitorJobs.put(monitorId, monitor)?.job?.cancel()
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
        private const val EXTRA_PROFILE_ID = "profile_id"
        private const val EXTRA_TITLE = "title"

        fun start(context: Context, serverUrl: String, token: String, profileId: String, sessionId: String, title: String) {
            val intent = Intent(context, RunMonitorService::class.java).apply {
                putExtra(EXTRA_SERVER_URL, serverUrl)
                putExtra(EXTRA_TOKEN, token)
                putExtra(EXTRA_SESSION_ID, sessionId)
                putExtra(EXTRA_PROFILE_ID, profileId)
                putExtra(EXTRA_TITLE, title)
            }
            ContextCompat.startForegroundService(context, intent)
        }

        fun cancelAll(context: Context) {
            context.stopService(Intent(context, RunMonitorService::class.java))
        }
    }
}
