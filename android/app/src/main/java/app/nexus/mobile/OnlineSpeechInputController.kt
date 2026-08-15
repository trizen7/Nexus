package app.nexus.mobile

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.speech.SpeechRecognizer
import java.util.concurrent.atomic.AtomicBoolean

internal class OnlineSpeechInputController(
    context: Context,
    private val onResult: (RecordedVoice) -> Unit,
    private val onError: (Int, String, RecordedVoice?) -> Unit,
    private val onReady: () -> Unit = {},
    private val onSpeechStart: () -> Unit = {},
    private val onSpeechEnd: () -> Unit = {},
    private val autoEndpoint: Boolean = false,
    voiceCommunication: Boolean = false,
    private val onAudioProcessingConfigured: (LocalAudioProcessingState) -> Unit = {},
) : SpeechInputSession {
    private val recorder = VoiceRecorder(context.applicationContext, voiceCommunication)
    private val mainHandler = Handler(Looper.getMainLooper())
    private val listening = AtomicBoolean(false)
    private val destroyed = AtomicBoolean(false)

    private var startedAtMillis = 0L
    private var lastSpeechAtMillis = 0L
    private var speechObserved = false
    private val endpointCheck = object : Runnable {
        override fun run() {
            if (!listening.get()) return
            val now = SystemClock.elapsedRealtime()
            if (recorder.maxAmplitude() >= SPEECH_AMPLITUDE) {
                lastSpeechAtMillis = now
                if (!speechObserved) {
                    speechObserved = true
                    onSpeechStart()
                }
            }
            when {
                now - startedAtMillis >= MAXIMUM_RECORDING_MILLIS -> finish()
                autoEndpoint && speechObserved && now - lastSpeechAtMillis >= ENDPOINT_SILENCE_MILLIS -> finish()
                autoEndpoint && !speechObserved && now - startedAtMillis >= NO_SPEECH_TIMEOUT_MILLIS -> failNoSpeech()
                else -> mainHandler.postDelayed(this, AMPLITUDE_POLL_MILLIS)
            }
        }
    }

    override fun start() {
        check(!destroyed.get()) { "在线语音识别器已关闭" }
        if (!listening.compareAndSet(false, true)) return
        try {
            recorder.start()
            startedAtMillis = SystemClock.elapsedRealtime()
            lastSpeechAtMillis = startedAtMillis
            speechObserved = false
            onAudioProcessingConfigured(
                LocalAudioProcessingState(
                    echoCancellationAvailable = recorder.echoCancellationAvailable(),
                    noiseSuppression = false,
                    automaticGainControl = false,
                )
            )
            onReady()
            mainHandler.postDelayed(endpointCheck, AMPLITUDE_POLL_MILLIS)
        } catch (error: Exception) {
            listening.set(false)
            recorder.cancel()
            throw error
        }
    }

    override fun stop() = finish()

    override fun cancel() {
        if (!listening.compareAndSet(true, false)) return
        mainHandler.removeCallbacks(endpointCheck)
        recorder.cancel()
    }

    override fun destroy() {
        destroyed.set(true)
        cancel()
    }

    private fun finish() {
        if (!listening.compareAndSet(true, false)) return
        mainHandler.removeCallbacks(endpointCheck)
        if (speechObserved) onSpeechEnd()
        val recording = recorder.stop()
        if (!speechObserved || recording == null || recording.durationMillis < MINIMUM_RECORDING_MILLIS) {
            recording?.file?.delete()
            onError(SpeechRecognizer.ERROR_NO_MATCH, "录音太短，请重试", null)
        } else {
            onResult(recording)
        }
    }

    private fun failNoSpeech() {
        if (!listening.compareAndSet(true, false)) return
        mainHandler.removeCallbacks(endpointCheck)
        recorder.cancel()
        onError(SpeechRecognizer.ERROR_SPEECH_TIMEOUT, "没有检测到语音", null)
    }

    private companion object {
        const val MINIMUM_RECORDING_MILLIS = 300L
        const val MAXIMUM_RECORDING_MILLIS = 30_000L
        const val NO_SPEECH_TIMEOUT_MILLIS = 10_000L
        const val ENDPOINT_SILENCE_MILLIS = 1_200L
        const val AMPLITUDE_POLL_MILLIS = 100L
        const val SPEECH_AMPLITUDE = 600
    }
}
