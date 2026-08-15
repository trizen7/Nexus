package app.nexus.mobile

import android.content.Context
import android.content.ComponentName
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

interface SpeechInputSession {
    fun start()
    fun stop() = cancel()
    fun cancel()
    fun destroy()
}

internal const val SPEECH_RECOGNIZER_CALLBACK_TIMEOUT = 1_002
private const val SPEECH_RECOGNIZER_READY_TIMEOUT_MILLIS = 15_000L
private const val SPEECH_RECOGNIZER_SPEAKING_TIMEOUT_MILLIS = 30_000L
private const val SPEECH_RECOGNIZER_RESULT_TIMEOUT_MILLIS = 4_000L

private val NEXUS_SPEECH_BIASING_STRINGS = arrayListOf(
    "Nexus",
    "Hermes",
    "Android",
    "Gateway",
    "API",
    "模型",
    "会话",
    "定时任务",
)

internal fun speechRecognitionIntent(partialResults: Boolean): Intent =
    Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
        putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
        putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, "zh-CN")
        putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, partialResults)
        putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 5)
        putExtra(RecognizerIntent.EXTRA_PREFER_OFFLINE, false)
        putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_MINIMUM_LENGTH_MILLIS, 400L)
        putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 1_200L)
        putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 1_800L)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            putStringArrayListExtra(RecognizerIntent.EXTRA_BIASING_STRINGS, NEXUS_SPEECH_BIASING_STRINGS)
            putExtra(RecognizerIntent.EXTRA_ENABLE_BIASING_DEVICE_CONTEXT, true)
            putExtra(RecognizerIntent.EXTRA_ENABLE_FORMATTING, RecognizerIntent.FORMATTING_OPTIMIZE_QUALITY)
        }
    }

internal fun bestSpeechResult(candidates: List<String>?, confidenceScores: FloatArray?): String? {
    val usable = candidates.orEmpty().mapIndexedNotNull { index, text ->
        text.trim().takeIf(String::isNotEmpty)?.let { index to it }
    }
    if (usable.isEmpty()) return null
    val scored = usable.filter { (index, _) ->
        confidenceScores != null && index < confidenceScores.size && confidenceScores[index] >= 0f
    }
    return scored.maxByOrNull { (index, _) -> confidenceScores!![index] }?.second ?: usable.first().second
}

internal fun partialSpeechFallback(errorCode: Int, latestPartialResult: String): String? =
    latestPartialResult.trim().takeIf {
        it.isNotEmpty() && errorCode in setOf(SpeechRecognizer.ERROR_SPEECH_TIMEOUT, SpeechRecognizer.ERROR_NO_MATCH)
    }

internal fun completeSpeechResult(finalResult: String?, latestPartialResult: String): String? {
    val finalText = finalResult?.trim().takeUnless { it.isNullOrEmpty() }
    val partialText = latestPartialResult.trim().takeIf(String::isNotEmpty)
    if (finalText == null) return partialText
    if (
        partialText != null &&
        partialText.length in (finalText.length + 1)..(finalText.length + 4) &&
        partialText.endsWith(finalText)
    ) {
        return partialText
    }
    return finalText
}

class SpeechInputController(
    context: Context,
    private val onPartial: (String) -> Unit,
    private val onResult: (String, RecordedVoice?) -> Unit,
    private val onError: (Int, String, RecordedVoice?) -> Unit,
    private val onReady: () -> Unit = {},
    private val onSpeechStart: () -> Unit = {},
    private val onSpeechEnd: () -> Unit = {},
    private val onAudioLevel: (Float) -> Unit = {},
    private val captureEchoCanceledAudio: Boolean = true,
    private val recognitionService: ComponentName? = null,
) : RecognitionListener, SpeechInputSession {
    private val applicationContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private var recognizer = createRecognizer()
    private var listening = false
    private var recreateBeforeNextStart = false
    private var latestPartialResult = ""
    private var echoCanceledAudioSource: EchoCanceledSpeechAudioSource? = null
    private var echoCanceledAudioUnsupported = false
    private val callbackTimeout = Runnable {
        if (!listening) return@Runnable
        listening = false
        val recording = takeEchoCanceledRecording()
        recreateBeforeNextStart = true
        recognizer.cancel()
        onError(
            SPEECH_RECOGNIZER_CALLBACK_TIMEOUT,
            "系统语音识别没有响应",
            recording,
        )
    }

    init { recognizer.setRecognitionListener(this) }

    override fun start() {
        if (listening) return
        if (recreateBeforeNextStart) recreateRecognizer()
        latestPartialResult = ""
        listening = true
        try {
            val intent = speechRecognitionIntent(partialResults = true)
            val audioSource = if (
                captureEchoCanceledAudio &&
                Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                !echoCanceledAudioUnsupported
            ) {
                EchoCanceledSpeechAudioSource.create(applicationContext.cacheDir)?.also { source ->
                    source.attachTo(intent)
                    source.start()
                }
            } else {
                null
            }
            echoCanceledAudioSource = audioSource
            recognizer.startListening(intent)
            scheduleCallbackTimeout(SPEECH_RECOGNIZER_READY_TIMEOUT_MILLIS)
        } catch (error: Exception) {
            cancelCallbackTimeout()
            if (echoCanceledAudioSource != null) echoCanceledAudioUnsupported = true
            closeEchoCanceledAudioSource()
            listening = false
            recreateBeforeNextStart = true
            throw error
        }
    }

    override fun stop() {
        if (listening) {
            echoCanceledAudioSource?.finishAudio()
            recognizer.stopListening()
        }
    }

    override fun cancel() {
        if (!listening) return
        listening = false
        cancelCallbackTimeout()
        closeEchoCanceledAudioSource()
        recognizer.cancel()
    }

    override fun destroy() {
        listening = false
        cancelCallbackTimeout()
        closeEchoCanceledAudioSource()
        recognizer.cancel()
        recognizer.destroy()
    }

    override fun onResults(results: Bundle?) {
        if (!listening) return
        listening = false
        cancelCallbackTimeout()
        val recording = takeEchoCanceledRecording()
        val text = completeSpeechResult(
            finalResult = bestSpeechResult(
                results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION),
                results?.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES),
            ),
            latestPartialResult = latestPartialResult,
        )
        if (text == null) {
            onError(
                SpeechRecognizer.ERROR_NO_MATCH,
                friendlySpeechError(SpeechRecognizer.ERROR_NO_MATCH),
                recording,
            )
        } else {
            onResult(text, recording)
        }
    }

    override fun onPartialResults(results: Bundle?) {
        if (!listening) return
        scheduleCallbackTimeout(SPEECH_RECOGNIZER_READY_TIMEOUT_MILLIS)
        bestSpeechResult(
            results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION),
            results?.getFloatArray(SpeechRecognizer.CONFIDENCE_SCORES),
        )?.let { text ->
            latestPartialResult = text
            onPartial(text)
        }
    }

    override fun onError(error: Int) {
        if (!listening) return
        listening = false
        cancelCallbackTimeout()
        val usedEchoCanceledAudio = echoCanceledAudioSource != null
        val recording = takeEchoCanceledRecording()
        if (usedEchoCanceledAudio && error in setOf(SpeechRecognizer.ERROR_AUDIO, SpeechRecognizer.ERROR_CLIENT)) {
            echoCanceledAudioUnsupported = true
        }
        val partialFallback = partialSpeechFallback(error, latestPartialResult)
        if (partialFallback != null) {
            onResult(partialFallback, recording)
            return
        }
        recreateBeforeNextStart = shouldRecreateSpeechRecognizer(error)
        onError(error, friendlySpeechError(error), recording)
    }

    override fun onReadyForSpeech(params: Bundle?) {
        if (listening) {
            scheduleCallbackTimeout(SPEECH_RECOGNIZER_READY_TIMEOUT_MILLIS)
            onReady()
        }
    }

    override fun onBeginningOfSpeech() {
        if (listening) {
            scheduleCallbackTimeout(SPEECH_RECOGNIZER_SPEAKING_TIMEOUT_MILLIS)
            onSpeechStart()
        }
    }

    override fun onRmsChanged(rmsdB: Float) {
        if (listening) onAudioLevel(rmsdB)
    }
    override fun onBufferReceived(buffer: ByteArray?) = Unit

    override fun onEndOfSpeech() {
        if (listening) {
            echoCanceledAudioSource?.finishAudio()
            scheduleCallbackTimeout(SPEECH_RECOGNIZER_RESULT_TIMEOUT_MILLIS)
            onSpeechEnd()
        }
    }

    override fun onEvent(eventType: Int, params: Bundle?) = Unit

    private fun recreateRecognizer() {
        listening = false
        cancelCallbackTimeout()
        closeEchoCanceledAudioSource()
        recognizer.cancel()
        recognizer.destroy()
        recognizer = createRecognizer().also {
            it.setRecognitionListener(this)
        }
        recreateBeforeNextStart = false
    }

    private fun scheduleCallbackTimeout(delayMillis: Long) {
        mainHandler.removeCallbacks(callbackTimeout)
        mainHandler.postDelayed(callbackTimeout, delayMillis)
    }

    private fun cancelCallbackTimeout() {
        mainHandler.removeCallbacks(callbackTimeout)
    }

    private fun closeEchoCanceledAudioSource() {
        echoCanceledAudioSource?.close()
        echoCanceledAudioSource = null
    }

    private fun takeEchoCanceledRecording(): RecordedVoice? {
        val source = echoCanceledAudioSource.also { echoCanceledAudioSource = null } ?: return null
        return source.takeRecording().also { source.close() }
    }

    private fun createRecognizer(): SpeechRecognizer = if (recognitionService == null) {
        SpeechRecognizer.createSpeechRecognizer(applicationContext)
    } else {
        SpeechRecognizer.createSpeechRecognizer(applicationContext, recognitionService)
    }
}

internal fun shouldRecreateSpeechRecognizer(errorCode: Int): Boolean =
    errorCode in setOf(3, 4, 5, 8, 11)

fun isSpeechRecognitionAvailable(context: Context): Boolean = SpeechRecognizer.isRecognitionAvailable(context)
