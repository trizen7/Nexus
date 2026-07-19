package app.nexus.mobile

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

class SpeechInputController(
    context: Context,
    private val onPartial: (String) -> Unit,
    private val onResult: (String) -> Unit,
    private val onError: (Int, String) -> Unit
) : RecognitionListener {
    private val recognizer = SpeechRecognizer.createSpeechRecognizer(context)
    private var listening = false

    init { recognizer.setRecognitionListener(this) }

    fun start() {
        if (listening) return
        listening = true
        recognizer.startListening(Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-CN")
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
        })
    }

    fun stop() {
        if (listening) recognizer.stopListening()
    }

    fun cancel() {
        listening = false
        recognizer.cancel()
    }

    fun destroy() {
        recognizer.destroy()
    }

    override fun onResults(results: Bundle?) {
        listening = false
        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let(onResult)
    }

    override fun onPartialResults(results: Bundle?) {
        results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)?.firstOrNull()?.let(onPartial)
    }

    override fun onError(error: Int) {
        listening = false
        onError(error, friendlySpeechError(error))
    }

    override fun onReadyForSpeech(params: Bundle?) = Unit
    override fun onBeginningOfSpeech() = Unit
    override fun onRmsChanged(rmsdB: Float) = Unit
    override fun onBufferReceived(buffer: ByteArray?) = Unit
    override fun onEndOfSpeech() = Unit
    override fun onEvent(eventType: Int, params: Bundle?) = Unit
}

fun isSpeechRecognitionAvailable(context: Context): Boolean = SpeechRecognizer.isRecognitionAvailable(context)
