package app.nexus.mobile.network

import com.google.gson.Gson
import com.google.gson.JsonObject
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString.Companion.toByteString
import java.io.Closeable
import java.io.IOException
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

internal const val CLOUD_ASR_PROTOCOL = "nexus.asr.v1"

internal data class CloudAsrCapabilities(
    val available: Boolean,
    val protocol: String,
    val sampleRate: Int,
    val channels: Int,
    val encoding: String,
    val frameMillis: Int,
    val supportsPartial: Boolean,
    val supportsPunctuation: Boolean,
)

internal sealed interface CloudAsrEvent {
    data class Ready(val sessionId: String) : CloudAsrEvent
    data class SpeechStart(val utteranceId: Long) : CloudAsrEvent
    data class Partial(val utteranceId: Long, val text: String, val revision: Long) : CloudAsrEvent
    data class Final(val utteranceId: Long, val text: String) : CloudAsrEvent
    data class SpeechEnd(val utteranceId: Long) : CloudAsrEvent
    data class Error(val code: String, val message: String, val retryable: Boolean) : CloudAsrEvent
    data object Closed : CloudAsrEvent
}

internal fun parseCloudAsrCapabilities(json: String): CloudAsrCapabilities {
    val root = Gson().fromJson(json, JsonObject::class.java) ?: JsonObject()
    return CloudAsrCapabilities(
        available = root.boolean("available"),
        protocol = root.text("protocol"),
        sampleRate = root.integer("sample_rate"),
        channels = root.integer("channels"),
        encoding = root.text("encoding"),
        frameMillis = root.integer("frame_millis"),
        supportsPartial = root.boolean("supports_partial"),
        supportsPunctuation = root.boolean("supports_punctuation"),
    )
}

internal fun parseCloudAsrEvent(json: String): CloudAsrEvent? {
    return runCatching {
        val root = Gson().fromJson(json, JsonObject::class.java) ?: return@runCatching null
        when (root.text("type")) {
            "ready" -> root.text("session_id").takeIf(String::isNotBlank)?.let(CloudAsrEvent::Ready)
            "speech_start" -> root.positiveLong("utterance_id")?.let(CloudAsrEvent::SpeechStart)
            "partial" -> {
                val id = root.positiveLong("utterance_id") ?: return@runCatching null
                val revision = root.nonNegativeLong("revision") ?: return@runCatching null
                CloudAsrEvent.Partial(id, root.text("text").trim(), revision)
            }
            "final" -> {
                val id = root.positiveLong("utterance_id") ?: return@runCatching null
                CloudAsrEvent.Final(id, root.text("text").trim())
            }
            "speech_end" -> root.positiveLong("utterance_id")?.let(CloudAsrEvent::SpeechEnd)
            "error" -> CloudAsrEvent.Error(
                code = root.text("code").ifBlank { "provider_unavailable" },
                message = root.text("message").ifBlank { "云端语音识别暂不可用" },
                retryable = root.boolean("retryable"),
            )
            "closed" -> CloudAsrEvent.Closed
            else -> null
        }
    }.getOrNull()
}

internal class CloudAsrEventGate {
    private val revisions = mutableMapOf<Long, Long>()
    private val finalized = mutableSetOf<Long>()
    private var highestUtteranceId = 0L

    @Synchronized
    fun accept(event: CloudAsrEvent): Boolean {
        return when (event) {
            is CloudAsrEvent.Partial -> {
                if (event.utteranceId < highestUtteranceId || event.utteranceId in finalized) return false
                val previous = revisions[event.utteranceId] ?: -1L
                if (event.revision <= previous) return false
                revisions[event.utteranceId] = event.revision
                highestUtteranceId = maxOf(highestUtteranceId, event.utteranceId)
                true
            }
            is CloudAsrEvent.Final -> {
                if (!finalized.add(event.utteranceId)) return false
                highestUtteranceId = maxOf(highestUtteranceId, event.utteranceId)
                true
            }
            is CloudAsrEvent.SpeechStart -> event.utteranceId >= highestUtteranceId
            is CloudAsrEvent.SpeechEnd -> event.utteranceId >= highestUtteranceId
            else -> true
        }
    }
}

internal class CloudAsrClient(
    baseUrl: String,
    private val token: String,
    private val httpClient: OkHttpClient = defaultCloudAsrHttpClient(),
) {
    private val baseUrl = baseUrl.trimEnd('/') + "/"

    fun capabilities(): CloudAsrCapabilities {
        val request = authorizedRequest("api/asr/capabilities").get().build()
        httpClient.newCall(request).execute().use { response ->
            val body = response.body?.string().orEmpty()
            if (!response.isSuccessful) {
                if (response.code in setOf(404, 501)) {
                    return CloudAsrCapabilities(false, CLOUD_ASR_PROTOCOL, 0, 0, "", 0, false, false)
                }
                throw IOException("云端语音识别能力查询失败：HTTP ${response.code}")
            }
            return parseCloudAsrCapabilities(body)
        }
    }

    fun connect(
        onEvent: (CloudAsrEvent) -> Unit,
        onFailure: (Throwable) -> Unit,
    ): CloudAsrSocket {
        val terminal = AtomicBoolean(false)
        val gate = CloudAsrEventGate()
        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                val start = """{"type":"start","protocol":"$CLOUD_ASR_PROTOCOL","language":"zh-CN","sample_rate":16000,"channels":1,"encoding":"pcm_s16le","frame_millis":20,"punctuation":true,"interim_results":true}"""
                if (!webSocket.send(start) && terminal.compareAndSet(false, true)) {
                    onFailure(IOException("无法启动云端语音识别"))
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                val event = parseCloudAsrEvent(text) ?: return
                if (!terminal.get() && gate.accept(event)) onEvent(event)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                if (terminal.compareAndSet(false, true)) onEvent(CloudAsrEvent.Closed)
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                if (terminal.compareAndSet(false, true)) onFailure(t)
            }
        }
        val webSocket = httpClient.newWebSocket(
            authorizedRequest("api/asr/stream").build(),
            listener,
        )
        return CloudAsrSocket(webSocket, terminal)
    }

    private fun authorizedRequest(path: String): Request.Builder = Request.Builder()
        .url(baseUrl + path)
        .header("Authorization", "Bearer $token")
        .header("Accept", "application/json")
}

internal class CloudAsrSocket(
    private val webSocket: WebSocket,
    private val terminal: AtomicBoolean,
) : Closeable {
    fun sendAudio(pcm: ByteArray, count: Int): Boolean {
        if (terminal.get() || count <= 0 || count > pcm.size) return false
        if (webSocket.queueSize() + count > MAX_WEBSOCKET_QUEUE_BYTES) return false
        return webSocket.send(pcm.toByteString(0, count))
    }

    fun commit(utteranceId: Long): Boolean = !terminal.get() && webSocket.send(
        """{"type":"commit","utterance_id":$utteranceId}"""
    )

    override fun close() {
        if (terminal.compareAndSet(false, true)) {
            if (!webSocket.close(1000, "cancel")) webSocket.cancel()
        } else {
            webSocket.cancel()
        }
    }

    private companion object {
        const val MAX_WEBSOCKET_QUEUE_BYTES = 256L * 1024L
    }
}

private fun JsonObject.text(key: String): String =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asString }.getOrNull() }.orEmpty()

private fun JsonObject.integer(key: String): Int =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asInt }.getOrNull() } ?: 0

private fun JsonObject.boolean(key: String): Boolean =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asBoolean }.getOrNull() } ?: false

private fun JsonObject.positiveLong(key: String): Long? = nonNegativeLong(key)?.takeIf { it > 0L }

private fun JsonObject.nonNegativeLong(key: String): Long? =
    get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asLong }.getOrNull() }?.takeIf { it >= 0L }

private fun defaultCloudAsrHttpClient(): OkHttpClient = OkHttpClient.Builder()
    .connectTimeout(3, TimeUnit.SECONDS)
    .readTimeout(0, TimeUnit.MILLISECONDS)
    .writeTimeout(10, TimeUnit.SECONDS)
    .retryOnConnectionFailure(false)
    .pingInterval(20, TimeUnit.SECONDS)
    .build()
