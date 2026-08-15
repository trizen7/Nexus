package app.nexus.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AudioEffect
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.os.Process
import android.speech.SpeechRecognizer
import app.nexus.mobile.network.CloudAsrClient
import app.nexus.mobile.network.CloudAsrEvent
import app.nexus.mobile.network.CloudAsrSocket
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger

internal class CloudStreamingSpeechController(
    @Suppress("UNUSED_PARAMETER") context: Context,
    serverUrl: String,
    token: String,
    private val onPartial: (Long, String) -> Unit,
    private val onResult: (Long, String, RecordedVoice?) -> Unit,
    private val onError: (Int, String, RecordedVoice?) -> Unit,
    private val onReady: () -> Unit = {},
    private val onSpeechStart: (Long) -> Unit = {},
    private val onSpeechEnd: (Long) -> Unit = {},
    private val onAudioProcessingConfigured: (LocalAudioProcessingState) -> Unit = {},
) : SpeechInputSession {
    private val client = CloudAsrClient(serverUrl, token)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "nexus-cloud-asr-capture").apply { isDaemon = true }
    }
    private val listening = AtomicBoolean(false)
    private val destroyed = AtomicBoolean(false)
    private val generation = AtomicInteger(0)

    @Volatile private var activeRecorder: AudioRecord? = null
    @Volatile private var activeSocket: CloudAsrSocket? = null
    private var speechActive = false

    override fun start() {
        check(!destroyed.get()) { "云端语音识别器已关闭" }
        if (!listening.compareAndSet(false, true)) return
        val session = generation.incrementAndGet()
        activeSocket = client.connect(
            onEvent = { event -> handleEvent(session, event) },
            onFailure = { fail(session, "云端语音识别连接已中断") },
        )
    }

    override fun cancel() {
        listening.set(false)
        generation.incrementAndGet()
        runCatching { activeRecorder?.stop() }
        activeSocket?.close()
        activeSocket = null
    }

    override fun destroy() {
        if (!destroyed.compareAndSet(false, true)) return
        cancel()
        executor.shutdown()
    }

    private fun handleEvent(session: Int, event: CloudAsrEvent) {
        if (!isCurrent(session)) return
        when (event) {
            is CloudAsrEvent.Ready -> executor.execute { capture(session) }
            is CloudAsrEvent.SpeechStart -> {
                speechActive = true
                postCurrent(session) { onSpeechStart(event.utteranceId) }
            }
            is CloudAsrEvent.Partial -> if (event.text.isNotBlank()) {
                postCurrent(session) { onPartial(event.utteranceId, event.text) }
            }
            is CloudAsrEvent.Final -> if (event.text.isNotBlank()) {
                speechActive = false
                postCurrent(session) {
                    onSpeechEnd(event.utteranceId)
                    onResult(event.utteranceId, event.text, null)
                }
            }
            is CloudAsrEvent.SpeechEnd -> {
                if (speechActive) postCurrent(session) { onSpeechEnd(event.utteranceId) }
                speechActive = false
            }
            is CloudAsrEvent.Error -> fail(session, event.message)
            CloudAsrEvent.Closed -> fail(session, "云端语音识别连接已关闭")
        }
    }

    @SuppressLint("MissingPermission")
    private fun capture(session: Int) {
        var recorder: AudioRecord? = null
        var echoCanceler: AcousticEchoCanceler? = null
        var noiseSuppressor: NoiseSuppressor? = null
        var gainControl: AutomaticGainControl? = null
        try {
            if (!isCurrent(session)) return
            recorder = createAudioRecord()
            activeRecorder = recorder
            echoCanceler = if (AcousticEchoCanceler.isAvailable()) {
                runCatching { enableEffect(AcousticEchoCanceler.create(recorder.audioSessionId)) }.getOrNull()
            } else null
            noiseSuppressor = if (NoiseSuppressor.isAvailable()) {
                runCatching { enableEffect(NoiseSuppressor.create(recorder.audioSessionId)) }.getOrNull()
            } else null
            gainControl = if (AutomaticGainControl.isAvailable()) {
                runCatching { enableEffect(AutomaticGainControl.create(recorder.audioSessionId)) }.getOrNull()
            } else null
            val processing = LocalAudioProcessingState(
                echoCancellationAvailable = echoCanceler != null,
                noiseSuppression = noiseSuppressor != null,
                automaticGainControl = gainControl != null,
            )
            recorder.startRecording()
            postCurrent(session) {
                onAudioProcessingConfigured(processing)
                onReady()
            }
            runCatching { Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO) }
            val frame = ByteArray(FRAME_BYTES)
            while (isCurrent(session)) {
                val count = recorder.read(frame, 0, frame.size, AudioRecord.READ_BLOCKING)
                if (count <= 0) {
                    if (isCurrent(session)) error("麦克风读取失败")
                    break
                }
                if (count % 2 != 0 || activeSocket?.sendAudio(frame, count) != true) {
                    error("云端语音发送队列拥塞")
                }
            }
        } catch (_: Exception) {
            fail(session, "云端实时语音识别暂不可用")
        } finally {
            activeRecorder = null
            runCatching { recorder?.stop() }
            runCatching { recorder?.release() }
            runCatching { echoCanceler?.release() }
            runCatching { noiseSuppressor?.release() }
            runCatching { gainControl?.release() }
        }
    }

    @SuppressLint("MissingPermission")
    private fun createAudioRecord(): AudioRecord {
        val minimum = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        check(minimum > 0) { "当前设备不支持 16 kHz 单声道录音" }
        return AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(SAMPLE_RATE)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minimum * 2, FRAME_BYTES * 8))
            .build()
            .also { recorder ->
                if (recorder.state != AudioRecord.STATE_INITIALIZED) {
                    recorder.release()
                    error("无法初始化麦克风")
                }
            }
    }

    private fun <T : AudioEffect> enableEffect(effect: T?): T? {
        if (effect == null) return null
        val enabled = runCatching {
            effect.enabled = true
            effect.enabled
        }.getOrDefault(false)
        return if (enabled) effect else null.also { effect.release() }
    }

    private fun fail(session: Int, message: String) {
        if (!isCurrent(session)) return
        listening.set(false)
        val failedGeneration = generation.incrementAndGet()
        runCatching { activeRecorder?.stop() }
        activeSocket?.close()
        activeSocket = null
        mainHandler.post {
            if (!destroyed.get() && generation.get() == failedGeneration) {
                onError(SpeechRecognizer.ERROR_NETWORK, message, null)
            }
        }
    }

    private fun isCurrent(session: Int): Boolean =
        listening.get() && !destroyed.get() && generation.get() == session

    private fun postCurrent(session: Int, callback: () -> Unit) {
        mainHandler.post { if (isCurrent(session)) callback() }
    }

    private companion object {
        const val SAMPLE_RATE = 16_000
        const val FRAME_MILLIS = 20
        const val FRAME_BYTES = SAMPLE_RATE * FRAME_MILLIS / 1_000 * 2
    }
}
