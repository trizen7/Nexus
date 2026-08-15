package app.nexus.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.media.audiofx.AutomaticGainControl
import android.media.audiofx.NoiseSuppressor
import android.os.Handler
import android.os.Looper
import android.os.Process
import com.k2fsa.sherpa.onnx.EndpointConfig
import com.k2fsa.sherpa.onnx.EndpointRule
import com.k2fsa.sherpa.onnx.FeatureConfig
import com.k2fsa.sherpa.onnx.OnlineModelConfig
import com.k2fsa.sherpa.onnx.OnlineTransducerModelConfig
import com.k2fsa.sherpa.onnx.OnlineRecognizer
import com.k2fsa.sherpa.onnx.OnlineRecognizerConfig
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicReference

internal class LocalStreamingSpeechController(
    context: Context,
    private val onPartial: (Long, String) -> Unit,
    private val onResult: (Long, String, RecordedVoice?) -> Unit,
    private val onError: (Int, String, RecordedVoice?) -> Unit,
    private val onReady: () -> Unit = {},
    private val onSpeechStart: (Long) -> Unit = {},
    private val onSpeechEnd: (Long) -> Unit = {},
    private val onSpeechCanceled: (Long) -> Unit = {},
    private val onAudioProcessingConfigured: (LocalAudioProcessingState) -> Unit = {},
    private val continuous: Boolean = true,
) : SpeechInputSession {
    private val applicationContext = context.applicationContext
    private val mainHandler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "nexus-local-asr").apply { isDaemon = true }
    }
    private val listening = AtomicBoolean(false)
    private val generation = AtomicInteger(0)
    private val readySession = AtomicInteger(0)
    private val destroyed = AtomicBoolean(false)
    private val stopRequested = AtomicBoolean(false)

    @Volatile
    private var activeRecorder: AudioRecord? = null
    @Volatile
    private var activeCaptureThread: Thread? = null
    private var recognizer: OnlineRecognizer? = null

    init {
        executor.execute {
            runCatching { createRecognizer() }.onSuccess { recognizer = it }
        }
    }

    override fun start() {
        check(!destroyed.get()) { "本地语音识别器已关闭" }
        check(activeCaptureThread?.isAlive != true) { "上一段麦克风采集仍在结束" }
        if (!listening.compareAndSet(false, true)) return
        stopRequested.set(false)
        val session = generation.incrementAndGet()
        mainHandler.postDelayed({
            if (isCurrent(session) && readySession.get() != session) {
                listening.set(false)
                val failedGeneration = generation.incrementAndGet()
                if (!destroyed.get() && generation.get() == failedGeneration) {
                    onError(
                        LOCAL_SPEECH_ERROR_INITIALIZATION,
                        "X-ASR 初始化超时，请重新进入通话；若仍失败，请重新下载模型",
                        null,
                    )
                }
            }
        }, INITIALIZATION_TIMEOUT_MILLIS)
        executor.execute { runSession(session) }
    }

    override fun stop() {
        if (continuous) {
            cancel()
            return
        }
        if (!listening.get()) return
        stopRequested.set(true)
        runCatching { activeRecorder?.stop() }
    }

    override fun cancel() {
        listening.set(false)
        generation.incrementAndGet()
        runCatching { activeRecorder?.stop() }
    }

    override fun destroy() {
        if (!destroyed.compareAndSet(false, true)) return
        cancel()
        executor.execute {
            recognizer?.release()
            recognizer = null
        }
        executor.shutdown()
    }

    @SuppressLint("MissingPermission")
    private fun runSession(session: Int) {
        var recorder: AudioRecord? = null
        var echoCanceler: AcousticEchoCanceler? = null
        var noiseSuppressor: NoiseSuppressor? = null
        var gainControl: AutomaticGainControl? = null
        var stream: com.k2fsa.sherpa.onnx.OnlineStream? = null
        var captureThread: Thread? = null
        val captureFailure = AtomicReference<Exception?>(null)
        val captureFinished = AtomicBoolean(false)
        val frames = PcmFrameBuffer(PCM_BUFFER_FRAMES, FRAME_SAMPLES)
        try {
            val activeRecognizer = recognizer ?: createRecognizer().also { recognizer = it }
            if (!isCurrent(session)) return
            val sessionRecorder = createAudioRecord()
            recorder = sessionRecorder
            activeRecorder = sessionRecorder
            echoCanceler = enableEchoCanceler(sessionRecorder.audioSessionId)
            noiseSuppressor = enableNoiseSuppressor(sessionRecorder.audioSessionId)
            gainControl = enableGainControl(sessionRecorder.audioSessionId)
            postCurrent(session) {
                onAudioProcessingConfigured(
                    LocalAudioProcessingState(
                        echoCancellationAvailable = echoCanceler != null,
                        noiseSuppression = noiseSuppressor != null,
                        automaticGainControl = gainControl != null,
                    )
                )
            }
            stream = activeRecognizer.createStream()
            sessionRecorder.startRecording()
            captureThread = Thread({
                runCatching { Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO) }
                try {
                    while (isCurrent(session) && !stopRequested.get()) {
                        val pcm = frames.acquire()
                        val count = sessionRecorder.read(pcm, 0, pcm.size, AudioRecord.READ_BLOCKING)
                        when {
                            count > 0 -> frames.offer(pcm, count)
                            count == 0 -> frames.recycle(pcm)
                            isCurrent(session) && !stopRequested.get() -> {
                                frames.recycle(pcm)
                                error("麦克风读取失败：$count")
                            }
                            else -> frames.recycle(pcm)
                        }
                    }
                } catch (error: Exception) {
                    if (isCurrent(session) && !stopRequested.get()) captureFailure.compareAndSet(null, error)
                } finally {
                    captureFinished.set(true)
                }
            }, "nexus-local-asr-capture").apply {
                isDaemon = true
                activeCaptureThread = this
                start()
            }
            readySession.set(session)
            postCurrent(session, onReady)

            val samples = FloatArray(FRAME_SAMPLES)
            var latestText = ""
            var speechStarted = false
            var processedSamples = 0L
            var utteranceId = 1L
            var acousticCandidateInProgress: Boolean
            var lastAcousticSpeechAtMillis: Long? = null
            val bargeInDetector = PcmBargeInDetector()
            while (isCurrent(session)) {
                val frame = frames.poll(PCM_POLL_MILLIS)
                if (frame == null) {
                    captureFailure.get()?.let { throw it }
                    if (captureFinished.get() && stopRequested.get()) {
                        stream.inputFinished()
                        while (activeRecognizer.isReady(stream)) activeRecognizer.decode(stream)
                        val completedText = activeRecognizer.getResult(stream).text.trim()
                        listening.set(false)
                        if (completedText.isNotBlank()) {
                            postCompleted(session) { onSpeechEnd(utteranceId) }
                            postCompleted(session) { onResult(utteranceId, completedText, null) }
                        } else {
                            postCompleted(session) { onSpeechCanceled(utteranceId) }
                        }
                        break
                    }
                    if (captureFinished.get()) error("麦克风采集意外停止")
                    continue
                }
                if (frames.droppedFrameCount > 0) {
                    frames.recycle(frame)
                    error("设备处理语音的速度不足，已丢弃音频帧")
                }
                try {
                    val frameElapsedMillis = processedSamples * 1_000L / SAMPLE_RATE
                    val acoustic = bargeInDetector.observe(
                        samples = frame.samples,
                        count = frame.count,
                        elapsedMillis = frameElapsedMillis,
                    )
                    processedSamples += frame.count
                    acousticCandidateInProgress = acoustic.speechCandidate
                    if (acoustic.speechCandidate) lastAcousticSpeechAtMillis = frameElapsedMillis
                    if (!speechStarted && acoustic.triggered) {
                        speechStarted = true
                        val activeUtteranceId = utteranceId
                        postCurrent(session) { onSpeechStart(activeUtteranceId) }
                    }
                    for (index in 0 until frame.count) {
                        samples[index] = frame.samples[index] / 32768.0f
                    }
                    stream.acceptWaveform(
                        if (frame.count == samples.size) samples else samples.copyOf(frame.count),
                        SAMPLE_RATE,
                    )
                    while (activeRecognizer.isReady(stream)) activeRecognizer.decode(stream)
                } finally {
                    frames.recycle(frame)
                }

                val text = activeRecognizer.getResult(stream).text.trim()
                if (text.isNotEmpty() && text != latestText) {
                    latestText = text
                    val activeUtteranceId = utteranceId
                    postCurrent(session) { onPartial(activeUtteranceId, text) }
                }

                val endpointDetected = continuous && activeRecognizer.isEndpoint(stream)
                val silenceFallback = continuous && shouldForceLocalSpeechEndpoint(
                    speechStarted = speechStarted,
                    completedText = latestText,
                    lastAcousticSpeechAtMillis = lastAcousticSpeechAtMillis,
                    elapsedMillis = processedSamples * 1_000L / SAMPLE_RATE,
                )
                if (endpointDetected || silenceFallback) {
                    val completedText = latestText
                    val wasSpeechStarted = speechStarted
                    if (endpointDetected && shouldReplayPreRollAtEndpoint(
                            completedText = completedText,
                            speechStarted = wasSpeechStarted,
                            acousticCandidateInProgress = acousticCandidateInProgress,
                        )
                    ) {
                        activeRecognizer.reset(stream)
                        replayPreRoll(activeRecognizer, stream, bargeInDetector.snapshotPreRoll())
                        latestText = activeRecognizer.getResult(stream).text.trim()
                        continue
                    }
                    activeRecognizer.reset(stream)
                    latestText = ""
                    speechStarted = false
                    lastAcousticSpeechAtMillis = null
                    bargeInDetector.resetSpeechWindow()
                    if (completedText.isNotBlank()) {
                        val completedId = utteranceId++
                        postCompleted(session) {
                            onSpeechEnd(completedId)
                        }
                        postCompleted(session) { onResult(completedId, completedText, null) }
                    } else if (wasSpeechStarted) {
                        val canceledId = utteranceId
                        postCompleted(session) { onSpeechCanceled(canceledId) }
                    }
                }
            }
        } catch (error: Exception) {
            if (isCurrent(session)) {
                listening.set(false)
                val failedGeneration = generation.incrementAndGet()
                mainHandler.post {
                    if (!destroyed.get() && generation.get() == failedGeneration) {
                        onError(
                            LOCAL_SPEECH_ERROR_INITIALIZATION,
                            "本地语音识别失败：${error.message ?: "未知错误"}",
                            null,
                        )
                    }
                }
            }
        } finally {
            activeRecorder = null
            runCatching { recorder?.stop() }
            runCatching { recorder?.release() }
            captureThread?.let { thread ->
                runCatching { thread.join(CAPTURE_JOIN_MILLIS) }
                if (thread.isAlive) {
                    thread.interrupt()
                    runCatching { thread.join(CAPTURE_INTERRUPT_JOIN_MILLIS) }
                }
                if (!thread.isAlive && activeCaptureThread === thread) activeCaptureThread = null
            }
            frames.clear()
            runCatching { stream?.release() }
            runCatching { echoCanceler?.release() }
            runCatching { noiseSuppressor?.release() }
            runCatching { gainControl?.release() }
        }
    }

    private fun createRecognizer(): OnlineRecognizer {
        val files = LocalAsrModelManager.selectedModelFiles(applicationContext)
            ?: error("请先在设置中下载本地语音模型")
        val modelConfig = OnlineModelConfig(
            transducer = OnlineTransducerModelConfig(
                encoder = files.modelFile("encoder.int8.onnx").absolutePath,
                decoder = files.modelFile("decoder.onnx").absolutePath,
                joiner = files.modelFile("joiner.int8.onnx").absolutePath,
            ),
            tokens = files.tokens.absolutePath,
            numThreads = Runtime.getRuntime().availableProcessors().coerceIn(1, 4),
            modelType = "zipformer2",
        )
        val config = OnlineRecognizerConfig(
            featConfig = FeatureConfig(sampleRate = SAMPLE_RATE, featureDim = 80),
            modelConfig = modelConfig,
            endpointConfig = EndpointConfig(
                rule1 = EndpointRule(false, 2.4f, 0.0f),
                rule2 = EndpointRule(true, 1.2f, 0.0f),
                rule3 = EndpointRule(false, 0.0f, 20.0f),
            ),
            enableEndpoint = continuous,
            decodingMethod = "modified_beam_search",
            maxActivePaths = 4,
        )
        return OnlineRecognizer(assetManager = null, config = config)
    }

    private fun replayPreRoll(
        recognizer: OnlineRecognizer,
        stream: com.k2fsa.sherpa.onnx.OnlineStream,
        frames: List<ShortArray>,
    ) {
        frames.forEach { pcm ->
            val samples = FloatArray(pcm.size) { index -> pcm[index] / 32768.0f }
            stream.acceptWaveform(samples, SAMPLE_RATE)
            while (recognizer.isReady(stream)) recognizer.decode(stream)
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
        val recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
            .setAudioFormat(
                AudioFormat.Builder()
                    .setSampleRate(SAMPLE_RATE)
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                    .build()
            )
            .setBufferSizeInBytes(maxOf(minimum * 2, FRAME_SAMPLES * 8))
            .build()
        check(recorder.state == AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            "无法初始化麦克风"
        }
        return recorder
    }

    private fun enableEchoCanceler(sessionId: Int): AcousticEchoCanceler? =
        if (AcousticEchoCanceler.isAvailable()) enableEffect(AcousticEchoCanceler.create(sessionId)) else null

    private fun enableNoiseSuppressor(sessionId: Int): NoiseSuppressor? =
        if (NoiseSuppressor.isAvailable()) enableEffect(NoiseSuppressor.create(sessionId)) else null

    private fun enableGainControl(sessionId: Int): AutomaticGainControl? =
        if (AutomaticGainControl.isAvailable()) enableEffect(AutomaticGainControl.create(sessionId)) else null

    private fun <T : android.media.audiofx.AudioEffect> enableEffect(effect: T?): T? {
        if (effect == null) return null
        val enabled = runCatching {
            effect.enabled = true
            effect.enabled
        }.getOrDefault(false)
        if (!enabled) {
            effect.release()
            return null
        }
        return effect
    }

    private fun isCurrent(session: Int): Boolean =
        listening.get() && !destroyed.get() && generation.get() == session

    private fun postCurrent(session: Int, callback: () -> Unit) {
        mainHandler.post { if (isCurrent(session)) callback() }
    }

    private fun postCompleted(session: Int, callback: () -> Unit) {
        mainHandler.post {
            if (!destroyed.get() && generation.get() == session) callback()
        }
    }

    private companion object {
        const val SAMPLE_RATE = 16_000
        const val FRAME_MILLIS = 20
        const val FRAME_SAMPLES = SAMPLE_RATE * FRAME_MILLIS / 1_000
        const val PCM_BUFFER_FRAMES = 25
        const val PCM_POLL_MILLIS = 100L
        const val CAPTURE_JOIN_MILLIS = 1_000L
        const val CAPTURE_INTERRUPT_JOIN_MILLIS = 500L
        const val INITIALIZATION_TIMEOUT_MILLIS = 45_000L
    }
}

internal const val LOCAL_SPEECH_ENDPOINT_FALLBACK_MILLIS = 1_800L

internal fun shouldForceLocalSpeechEndpoint(
    speechStarted: Boolean,
    completedText: String,
    lastAcousticSpeechAtMillis: Long?,
    elapsedMillis: Long,
): Boolean = speechStarted &&
    completedText.isNotBlank() &&
    lastAcousticSpeechAtMillis != null &&
    elapsedMillis - lastAcousticSpeechAtMillis >= LOCAL_SPEECH_ENDPOINT_FALLBACK_MILLIS

internal fun shouldReplayPreRollAtEndpoint(
    completedText: String,
    speechStarted: Boolean,
    acousticCandidateInProgress: Boolean,
): Boolean = completedText.isBlank() && !speechStarted && acousticCandidateInProgress

internal data class LocalAudioProcessingState(
    // Availability only: Android does not expose an objective AEC quality/ERLE measurement.
    val echoCancellationAvailable: Boolean,
    val noiseSuppression: Boolean,
    val automaticGainControl: Boolean,
)
