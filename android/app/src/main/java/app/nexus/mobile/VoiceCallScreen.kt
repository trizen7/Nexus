package app.nexus.mobile

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.media.MediaPlayer
import android.os.Build
import android.os.SystemClock
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MicOff
import androidx.compose.material.icons.filled.PhoneInTalk
import androidx.compose.material.icons.filled.SpeakerPhone
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.ContextCompat
import app.nexus.mobile.network.ChatRole
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Deferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.resume

private data class QueuedSpeech(val generation: Long, val text: String, val voice: String)
private data class PreparedSpeech(val request: QueuedSpeech, val audio: ByteArray)
private data class RecentPlayback(val text: String, val startedAtMillis: Long)
private data class PhoneUtteranceContext(
    val echoSource: String,
    val bargeInActive: Boolean,
    val acousticSpeechConfirmed: Boolean,
)
private const val MIN_CLOUD_TRANSCRIPTION_MILLIS = 300L

internal fun visibleVoicePreview(
    preview: String,
    listening: Boolean,
    transcribing: Boolean,
    latestUserMessage: String?,
): String? {
    val normalized = preview.trim()
    if (!listening && !transcribing) return null
    if (normalized.isEmpty() || normalized in VOICE_PREVIEW_STATUS_TEXTS) return null
    if (normalized == latestUserMessage?.trim()) return null
    return normalized
}

private val VOICE_PREVIEW_STATUS_TEXTS = setOf(
    "正在聆听…",
    "正在识别…",
    "请说话…",
    "正在记录你的讲话…",
)

private data class VoiceOption(val id: String, val label: String)

private val VOICE_OPTIONS = listOf(
    VoiceOption("xiaoxiao", "晓晓女声"),
    VoiceOption("xiaoyi", "晓伊女声"),
    VoiceOption("yunxi", "云希男声"),
    VoiceOption("yunjian", "云健男声"),
)

private class VoiceCallAudioRouter(context: Context) {
    private val audioManager = context.getSystemService(AudioManager::class.java)
    private var active = false
    private var previousMode = AudioManager.MODE_NORMAL
    private var previousSpeakerphone = false
    private var previousCommunicationDevice: AudioDeviceInfo? = null

    fun start() {
        if (active) return
        previousMode = audioManager.mode
        @Suppress("DEPRECATION")
        previousSpeakerphone = audioManager.isSpeakerphoneOn
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            previousCommunicationDevice = audioManager.communicationDevice
        }
        audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
        active = true
    }

    fun setSpeakerEnabled(enabled: Boolean) {
        if (!active) start()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val targetType = if (enabled) {
                AudioDeviceInfo.TYPE_BUILTIN_SPEAKER
            } else {
                AudioDeviceInfo.TYPE_BUILTIN_EARPIECE
            }
            val target = audioManager.availableCommunicationDevices.firstOrNull { it.type == targetType }
            if (target != null) {
                audioManager.setCommunicationDevice(target)
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            setLegacySpeakerphone(enabled)
        }
    }

    fun restore() {
        if (!active) return
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val previous = previousCommunicationDevice
            if (previous != null && audioManager.availableCommunicationDevices.any { it.id == previous.id }) {
                audioManager.setCommunicationDevice(previous)
            } else {
                audioManager.clearCommunicationDevice()
            }
        } else {
            setLegacySpeakerphone(previousSpeakerphone)
        }
        audioManager.mode = previousMode
        active = false
    }

    @Suppress("DEPRECATION")
    private fun setLegacySpeakerphone(enabled: Boolean) {
        audioManager.isSpeakerphoneOn = enabled
    }
}

private class VoiceCallAudioQueue(
    private val context: Context,
    private val synthesize: suspend (String, String) -> ByteArray,
    private val onFailure: (String) -> Unit,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val queue = Channel<QueuedSpeech>(capacity = 8)
    private val preparedQueue = Channel<PreparedSpeech>(capacity = 2)
    private val generation = AtomicLong(0)
    private val pendingCount = AtomicInteger(0)
    private var activeSynthesis: Deferred<ByteArray>? = null
    private var player: MediaPlayer? = null
    private var finishPlayback: (() -> Unit)? = null
    private val recentPlayback = ArrayDeque<RecentPlayback>()
    private var lastPlaybackEndedAtMillis = Long.MIN_VALUE
    var speaking by mutableStateOf(false)
        private set
    var playing by mutableStateOf(false)
        private set
    var pausedForSpeech by mutableStateOf(false)
        private set
    var currentText by mutableStateOf("")
        private set

    init {
        scope.launch {
            for (request in queue) {
                if (request.generation != generation.get()) continue
                var synthesis: Deferred<ByteArray>? = null
                try {
                    synthesis = scope.async { synthesize(request.text, request.voice) }
                    activeSynthesis = synthesis
                    val audio = synthesis.await()
                    if (request.generation != generation.get()) continue
                    preparedQueue.send(PreparedSpeech(request, audio))
                } catch (error: CancellationException) {
                    if (scope.coroutineContext[kotlinx.coroutines.Job]?.isActive != true) throw error
                } catch (error: Exception) {
                    if (request.generation == generation.get()) {
                        onFailure(error.message ?: "语音合成失败")
                        speaking = pendingCount.decrementAndGet() > 0
                    }
                } finally {
                    if (activeSynthesis === synthesis) activeSynthesis = null
                }
            }
        }
        scope.launch {
            for (prepared in preparedQueue) {
                val request = prepared.request
                if (request.generation != generation.get()) continue
                try {
                    rememberPlaybackText(request.text)
                    currentText = request.text
                    playing = true
                    play(prepared.audio, request.generation)
                } catch (error: CancellationException) {
                    throw error
                } catch (error: Exception) {
                    if (request.generation == generation.get()) {
                        onFailure(error.message ?: "语音播放失败")
                    }
                } finally {
                    if (request.generation == generation.get()) {
                        playing = false
                        pausedForSpeech = false
                        currentText = ""
                        lastPlaybackEndedAtMillis = SystemClock.elapsedRealtime()
                        speaking = pendingCount.decrementAndGet() > 0
                    }
                }
            }
        }
    }

    suspend fun enqueue(text: String, voice: String) {
        val spoken = text
            .replace(Regex("https?://\\S+"), "链接")
            .replace(Regex("[*#>_~]"), "")
            .replace(Regex("\\s+"), " ")
            .trim()
        if (spoken.isNotEmpty()) {
            val request = QueuedSpeech(generation.get(), spoken, voice)
            pendingCount.incrementAndGet()
            speaking = true
            try {
                queue.send(request)
            } catch (error: CancellationException) {
                if (request.generation == generation.get()) {
                    speaking = pendingCount.decrementAndGet() > 0
                }
                throw error
            } catch (_: Exception) {
                if (request.generation == generation.get()) {
                    speaking = pendingCount.decrementAndGet() > 0
                }
                onFailure("语音朗读队列已关闭")
            }
        }
    }

    fun stop() {
        generation.incrementAndGet()
        activeSynthesis?.cancel()
        activeSynthesis = null
        pendingCount.set(0)
        speaking = false
        playing = false
        pausedForSpeech = false
        lastPlaybackEndedAtMillis = SystemClock.elapsedRealtime()
        finishPlayback?.invoke()
    }

    fun echoReferenceText(): String {
        val now = SystemClock.elapsedRealtime()
        while (recentPlayback.firstOrNull()?.let { now - it.startedAtMillis > ECHO_HISTORY_MILLIS } == true) {
            recentPlayback.removeFirst()
        }
        val echoWindowOpen = speaking || playing || pausedForSpeech || (
            lastPlaybackEndedAtMillis != Long.MIN_VALUE &&
                now - lastPlaybackEndedAtMillis <= ECHO_TAIL_MILLIS
            )
        return if (echoWindowOpen) recentPlayback.joinToString("。") { it.text } else ""
    }

    fun pauseForSpeech(): Boolean {
        val active = player ?: return false
        if (!playing) return false
        return runCatching {
            if (!active.isPlaying) return@runCatching false
            active.pause()
            playing = false
            pausedForSpeech = true
            true
        }.getOrDefault(false)
    }

    fun resumeAfterEcho(): Boolean {
        val active = player ?: return false
        if (!pausedForSpeech) return false
        return runCatching {
            active.start()
            playing = true
            pausedForSpeech = false
            true
        }.getOrDefault(false)
    }

    fun close() {
        stop()
        queue.close()
        preparedQueue.close()
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancel()
    }

    private fun rememberPlaybackText(text: String) {
        recentPlayback.addLast(RecentPlayback(text, SystemClock.elapsedRealtime()))
        while (recentPlayback.sumOf { it.text.length } > MAX_ECHO_HISTORY_CHARS) {
            recentPlayback.removeFirstOrNull() ?: break
        }
    }

    private suspend fun play(audio: ByteArray, expectedGeneration: Long) {
        val file = withContext(NonCancellable + Dispatchers.IO) {
            File.createTempFile("nexus-call-", ".mp3", context.cacheDir)
        }
        try {
            withContext(Dispatchers.IO) { file.writeBytes(audio) }
            suspendCancellableCoroutine { continuation ->
                var completed = false
                lateinit var active: MediaPlayer
                var prepareWatchdog: kotlinx.coroutines.Job? = null
                var playbackWatchdog: kotlinx.coroutines.Job? = null
                fun complete(error: Throwable? = null) {
                    if (completed) return
                    completed = true
                    prepareWatchdog?.cancel()
                    playbackWatchdog?.cancel()
                    runCatching { active.stop() }
                    active.release()
                    if (player === active) player = null
                    finishPlayback = null
                    if (continuation.isActive) {
                        if (error == null) {
                            continuation.resumeWith(Result.success(Unit))
                        } else {
                            continuation.resumeWith(Result.failure(error))
                        }
                    }
                }
                active = MediaPlayer()
                val configured = runCatching {
                    active.setAudioAttributes(
                        AudioAttributes.Builder()
                            .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                            .setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                            .build()
                    )
                    active.setDataSource(file.absolutePath)
                    active.setOnCompletionListener { complete() }
                    active.setOnPreparedListener {
                        prepareWatchdog?.cancel()
                        if (expectedGeneration != generation.get()) {
                            complete()
                        } else {
                            runCatching {
                                active.playbackParams = active.playbackParams.setSpeed(1.05f)
                                active.start()
                                playbackWatchdog = scope.launch {
                                    delay(PLAYBACK_TIMEOUT_MILLIS)
                                    complete(IllegalStateException("语音播放超时"))
                                }
                            }.onFailure(::complete)
                        }
                    }
                    active.setOnErrorListener { _, what, extra ->
                        complete(IllegalStateException("语音播放失败（$what/$extra）"))
                        true
                    }
                }
                if (configured.isFailure) {
                    complete(configured.exceptionOrNull())
                    return@suspendCancellableCoroutine
                }
                if (expectedGeneration != generation.get()) {
                    active.release()
                    continuation.resume(Unit)
                    return@suspendCancellableCoroutine
                }
                player = active
                finishPlayback = { complete() }
                prepareWatchdog = scope.launch {
                    delay(PREPARE_TIMEOUT_MILLIS)
                    complete(IllegalStateException("语音准备超时"))
                }
                runCatching { active.prepareAsync() }.onFailure(::complete)
                continuation.invokeOnCancellation { complete() }
            }
        } finally {
            withContext(NonCancellable + Dispatchers.IO) { file.delete() }
        }
    }

    private companion object {
        const val ECHO_HISTORY_MILLIS = 12_000L
        const val ECHO_TAIL_MILLIS = 2_500L
        const val MAX_ECHO_HISTORY_CHARS = 400
        const val PREPARE_TIMEOUT_MILLIS = 10_000L
        const val PLAYBACK_TIMEOUT_MILLIS = 120_000L
    }
}

internal data class SpeechChunk(val text: String, val consumedChars: Int)

internal data class AssistantSpeechCursor(
    val messageId: String?,
    val assistantCount: Int,
    val content: String,
    val consumedChars: Int,
)

internal fun reconcileAssistantSpeechCursor(
    current: AssistantSpeechCursor,
    messageId: String,
    assistantCount: Int,
    content: String,
): AssistantSpeechCursor {
    val sameLogicalResponse = current.messageId == messageId || current.assistantCount == assistantCount
    if (!sameLogicalResponse) return AssistantSpeechCursor(messageId, assistantCount, content, 0)
    val commonPrefix = current.content.zip(content).indexOfFirst { (old, new) -> old != new }
        .let { mismatch -> if (mismatch < 0) minOf(current.content.length, content.length) else mismatch }
    return AssistantSpeechCursor(
        messageId = messageId,
        assistantCount = assistantCount,
        content = content,
        consumedChars = minOf(current.consumedChars, commonPrefix),
    )
}

internal fun nextSpeechChunk(pending: String, flush: Boolean): SpeechChunk? {
    if (pending.isBlank()) return null
    val boundaries = pending.mapIndexedNotNull { index, character ->
        (index + 1).takeIf { character in "。！？!?；;\n" }
    }
    val usefulBoundary = boundaries.firstOrNull { it >= 8 }
    val cut = when {
        usefulBoundary != null && usefulBoundary <= 40 -> usefulBoundary
        flush && boundaries.isNotEmpty() -> boundaries.first()
        pending.length >= 36 -> {
            val sentence = boundaries.lastOrNull { it in 8..36 }
            val soft = pending.take(36).indexOfLast { it in "，,、：: " }
            sentence ?: if (soft >= 16) soft + 1 else 28
        }
        flush -> pending.length
        else -> return null
    }
    return SpeechChunk(pending.take(cut), cut)
}

internal fun isLikelyPlaybackEcho(recognized: String, spoken: String): Boolean {
    val candidate = normalizeSpeechForEcho(recognized)
    val source = normalizeSpeechForEcho(spoken)
    if (candidate.length < 4 || source.length < 4) return false
    if (source.contains(candidate) || candidate.contains(source)) return true
    val candidatePairs = candidate.windowed(2).toSet()
    val sourcePairs = source.windowed(2).toSet()
    if (candidatePairs.isEmpty() || sourcePairs.isEmpty()) return false
    val candidateOverlap = candidatePairs.count(sourcePairs::contains).toDouble() / candidatePairs.size
    return candidateOverlap >= 0.72
}

internal fun shouldSuppressPlaybackEcho(
    recognized: String,
    spoken: String,
    acousticUserSpeechConfirmed: Boolean,
): Boolean {
    val candidate = normalizeSpeechForEcho(recognized)
    val source = normalizeSpeechForEcho(spoken)
    if (candidate.isEmpty() || source.isEmpty()) return false
    if (isLikelyPlaybackEcho(candidate, source)) return true
    return !acousticUserSpeechConfirmed &&
        candidate.length in 2..3 &&
        (source.startsWith(candidate) || source.contains(candidate))
}

private fun normalizeSpeechForEcho(value: String): String = value
    .lowercase()
    .replace(Regex("[\\p{P}\\p{S}\\s]+"), "")

internal fun shouldPauseAssistantForPartial(
    assistantPlaying: Boolean,
    acousticUserSpeechConfirmed: Boolean,
): Boolean = assistantPlaying && acousticUserSpeechConfirmed

internal fun shouldConfirmPlaybackInterruption(
    acousticCandidate: Boolean,
    recognized: String,
    spoken: String,
): Boolean = acousticCandidate &&
    normalizeSpeechForEcho(recognized).length >= 2 &&
    !shouldSuppressPlaybackEcho(
        recognized = recognized,
        spoken = spoken,
        acousticUserSpeechConfirmed = false,
    )

internal class PlaybackBargeInDetector(
    private val earpieceThresholdRms: Float = 2.5f,
    private val speakerThresholdRms: Float = 4.5f,
    private val requiredRiseRms: Float = 1.5f,
    private val sustainedMillis: Long = 160L,
) {
    private var baselineRms: Float? = null
    private var baselineSpeakerphone: Boolean? = null
    private var loudSinceMillis: Long? = null

    fun learnAmbient(rmsDb: Float, speakerphone: Boolean) {
        if (!rmsDb.isFinite()) return
        val level = rmsDb.coerceIn(-20f, 20f)
        if (baselineSpeakerphone != speakerphone) {
            baselineSpeakerphone = speakerphone
            baselineRms = level
            loudSinceMillis = null
            return
        }
        val baseline = baselineRms
        baselineRms = if (baseline == null) {
            level
        } else {
            baseline + (level - baseline) * AMBIENT_ADAPTATION
        }
        loudSinceMillis = null
    }

    fun observe(rmsDb: Float, elapsedMillis: Long, speakerphone: Boolean): Boolean {
        if (!rmsDb.isFinite()) return false
        val level = rmsDb.coerceIn(-20f, 20f)
        val routeThreshold = if (speakerphone) speakerThresholdRms else earpieceThresholdRms
        if (baselineSpeakerphone != speakerphone) {
            baselineSpeakerphone = speakerphone
            baselineRms = null
            loudSinceMillis = null
        }
        val baseline = baselineRms ?: run {
            // There is no independent evidence that the first callback is speech. Treating
            // an initially loud route as quiet would make steady background noise interrupt
            // playback after the sustain window. Recognition still receives every sample;
            // interruption becomes eligible as soon as a later callback rises above this level.
            baselineRms = level
            loudSinceMillis = null
            return false
        }
        val threshold = maxOf(routeThreshold, baseline + requiredRiseRms)
        if (level >= threshold) {
            val loudSince = loudSinceMillis ?: elapsedMillis.also { loudSinceMillis = it }
            return elapsedMillis - loudSince >= sustainedMillis
        }
        loudSinceMillis = null
        return false
    }

    fun resetPlaybackWindow() {
        loudSinceMillis = null
    }

    fun reset() {
        baselineRms = null
        baselineSpeakerphone = null
        loudSinceMillis = null
    }

    private companion object {
        const val AMBIENT_ADAPTATION = 0.12f
    }
}

@Composable
fun VoiceCallScreen(state: MainUiState, viewModel: MainViewModel) {
    val context = LocalContext.current
    val transcriptionScope = rememberCoroutineScope()
    val localSpeechModelInstalled = remember(context) {
        LocalAsrModelManager.selectedModelFiles(context) != null
    }
    val selectedPhoneSpeechEngine = remember { SpeechEnginePreferences.phone(context) }
    val systemSpeechRecognitionAvailable = remember(context) { isSpeechRecognitionAvailable(context) }
    val configuredSystemSpeechService = remember(context) {
        SystemSpeechRecognitionManager.selectedComponent(context)
    }
    val systemSpeechRecognitionActive =
        selectedPhoneSpeechEngine == SpeechRecognitionEngine.SYSTEM && systemSpeechRecognitionAvailable
    val localSpeechRecognitionAvailable =
        selectedPhoneSpeechEngine == SpeechRecognitionEngine.X_ASR && localSpeechModelInstalled
    val onlineSpeechRecognitionActive = selectedPhoneSpeechEngine == SpeechRecognitionEngine.ONLINE
    val continuousStreamingSpeech = localSpeechRecognitionAvailable
    val speechRecognitionAvailable =
        systemSpeechRecognitionActive || localSpeechRecognitionAvailable || onlineSpeechRecognitionActive
    var microphoneEnabled by remember { mutableStateOf(true) }
    var speakerphoneEnabled by remember { mutableStateOf(false) }
    var selectedVoice by remember { mutableStateOf("xiaoxiao") }
    var voiceMenuExpanded by remember { mutableStateOf(false) }
    var localError by remember {
        mutableStateOf(
            when {
                speechRecognitionAvailable -> null
                selectedPhoneSpeechEngine == SpeechRecognitionEngine.X_ASR -> "X-ASR 未安装，请先在设置中下载模型"
                else -> "当前设备没有可用的系统语音输入"
            }
        )
    }
    var ttsNotice by remember { mutableStateOf<String?>(null) }
    var listenRequest by remember { mutableIntStateOf(0) }
    var restartDelayMillis by remember { mutableLongStateOf(280L) }
    var suppressedAssistantCount by remember { mutableStateOf<Int?>(null) }
    var ignoredPlaybackText by remember { mutableStateOf("") }
    var bargeInActive by remember { mutableStateOf(false) }
    var speechDetectedInSession by remember { mutableStateOf(false) }
    var consecutiveRecognizerTimeouts by remember { mutableIntStateOf(0) }
    var acousticSpeechConfirmedInSession by remember { mutableStateOf(false) }
    var acousticBargeInCandidate by remember { mutableStateOf(false) }
    var latestLocalPartialId by remember { mutableLongStateOf(0L) }
    var latestLocalPartialText by remember { mutableStateOf("") }
    var activeLocalSpeechId by remember { mutableStateOf<Long?>(null) }
    val localUtteranceContexts = remember { mutableMapOf<Long, PhoneUtteranceContext>() }
    val pendingLocalRefinements = remember { mutableSetOf<Long>() }
    var localEchoCancellationAvailable by remember { mutableStateOf<Boolean?>(null) }
    val initialAssistant = remember { state.messages.lastOrNull { it.role == ChatRole.ASSISTANT } }
    var speechCursor by remember {
        mutableStateOf(
            AssistantSpeechCursor(
                messageId = initialAssistant?.id,
                assistantCount = state.messages.count { it.role == ChatRole.ASSISTANT },
                content = initialAssistant?.content.orEmpty(),
                consumedChars = initialAssistant?.content?.length ?: 0,
            )
        )
    }
    var hasPermission by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
    }
    val audioQueue = remember(viewModel) {
        VoiceCallAudioQueue(context, viewModel::synthesizeSpeech) { ttsNotice = it }
    }
    val playbackBargeInDetector = remember { PlaybackBargeInDetector() }
    val audioRouter = remember(context) { VoiceCallAudioRouter(context) }

    fun pauseAssistantForSpeech() {
        if (bargeInActive || audioQueue.pausedForSpeech) return
        val playbackText = audioQueue.currentText
        if (audioQueue.pauseForSpeech()) {
            ignoredPlaybackText = playbackText
            playbackBargeInDetector.resetPlaybackWindow()
            viewModel.updateVoicePreview("正在记录你的讲话…")
        }
    }

    fun confirmPlaybackInterruption(text: String): Boolean {
        if (!audioQueue.playing && !audioQueue.pausedForSpeech) return false
        val echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() }
        if (!shouldConfirmPlaybackInterruption(acousticBargeInCandidate, text, echoSource)) return false
        speechDetectedInSession = true
        acousticSpeechConfirmedInSession = true
        if (audioQueue.playing) pauseAssistantForSpeech()
        return true
    }

    fun commitAssistantInterruption() {
        if (bargeInActive) return
        suppressedAssistantCount = speechCursor.assistantCount
        audioQueue.stop()
        playbackBargeInDetector.resetPlaybackWindow()
        bargeInActive = true
    }

    fun resumePlaybackAfterEcho() {
        audioQueue.resumeAfterEcho()
        playbackBargeInDetector.resetPlaybackWindow()
        ignoredPlaybackText = ""
        bargeInActive = false
        acousticBargeInCandidate = false
    }

    fun restartListening(delayMillis: Long = 120L) {
        restartDelayMillis = delayMillis
        listenRequest += 1
    }

    fun resetLocalUtteranceState() {
        localUtteranceContexts.clear()
        latestLocalPartialId = 0L
        latestLocalPartialText = ""
        activeLocalSpeechId = null
        pendingLocalRefinements.clear()
    }

    fun submitUserTranscript(text: String, continuousLocalListening: Boolean = false, preservePreview: Boolean = false) {
        ignoredPlaybackText = ""
        commitAssistantInterruption()
        bargeInActive = false
        localError = null
        viewModel.setVoiceListening(continuousLocalListening)
        if (!preservePreview) viewModel.updateVoicePreview(text)
        viewModel.sendVoiceMessage(text)
        if (continuousLocalListening) {
            if (preservePreview) viewModel.updateVoicePreview(latestLocalPartialText)
        } else {
            restartListening()
        }
        acousticBargeInCandidate = false
    }

    fun finishRecognizedTranscript(
        text: String,
        echoSource: String,
        utteranceBargeInActive: Boolean = bargeInActive,
        utteranceAcousticConfirmed: Boolean = acousticSpeechConfirmedInSession,
        continuousLocalListening: Boolean = false,
        preservePreview: Boolean = false,
    ) {
        val suppressAsEcho = !utteranceBargeInActive && shouldSuppressPlaybackEcho(
            recognized = text,
            spoken = echoSource,
            acousticUserSpeechConfirmed = utteranceAcousticConfirmed,
        )
        if (suppressAsEcho) {
            resumePlaybackAfterEcho()
            viewModel.setVoiceListening(continuousLocalListening)
            if (preservePreview) viewModel.updateVoicePreview(latestLocalPartialText) else viewModel.updateVoicePreview("")
            if (!continuousLocalListening) restartListening()
        } else {
            submitUserTranscript(text, continuousLocalListening, preservePreview)
        }
    }

    fun handleRecognitionFailure(code: Int, message: String) {
        val permissionGranted = ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.RECORD_AUDIO,
        ) == PackageManager.PERMISSION_GRANTED
        hasPermission = permissionGranted
        if (audioQueue.pausedForSpeech) resumePlaybackAfterEcho()
        ignoredPlaybackText = ""
        bargeInActive = false
        acousticBargeInCandidate = false
        playbackBargeInDetector.resetPlaybackWindow()
        if (code == SPEECH_RECOGNIZER_CALLBACK_TIMEOUT) {
            consecutiveRecognizerTimeouts += 1
            viewModel.setVoiceListening(false)
            if (shouldStopSpeechWatchdogRetries(consecutiveRecognizerTimeouts)) {
                viewModel.updateVoicePreview("")
                localError = "系统语音识别连续没有响应，请检查语音引擎后重试"
                return
            }
        } else {
            consecutiveRecognizerTimeouts = 0
        }
        val silentRetry = code in setOf(6, 7) && !speechDetectedInSession
        if (!silentRetry) viewModel.setVoiceListening(false)
        if (shouldAutoRetryPhoneSpeech(code, permissionGranted)) {
            localError = null
            val retryPrompt = speechRetryPrompt(code, speechDetectedInSession)
            viewModel.updateVoicePreview(retryPrompt.orEmpty())
            restartListening(if (retryPrompt != null) 650L else voiceListenRestartDelayMillis(code))
        } else {
            localError = if (code == 9) "需要麦克风权限才能开始通话" else message
        }
    }

    fun transcribeAndFinish(
        recording: RecordedVoice?,
        fallbackText: String?,
        echoSource: String,
        onFailure: () -> Unit,
    ) {
        val usableRecording = recording?.takeIf { it.durationMillis >= MIN_CLOUD_TRANSCRIPTION_MILLIS }
        if (recording != null && usableRecording == null) recording.file.delete()
        if (usableRecording == null) {
            val text = fallbackText?.trim().takeUnless { it.isNullOrEmpty() }
            if (text != null) finishRecognizedTranscript(text, echoSource) else onFailure()
            return
        }
        viewModel.setVoiceTranscribing()
        transcriptionScope.launch {
            val cloudText = try {
                viewModel.transcribeVoice(
                    usableRecording,
                    deleteRemoteAfterTranscription = true,
                )
            } catch (error: CancellationException) {
                throw error
            } catch (_: Exception) {
                null
            }?.trim().takeUnless { it.isNullOrEmpty() }
            val text = cloudText ?: fallbackText?.trim().takeUnless { it.isNullOrEmpty() }
            if (text != null) {
                finishRecognizedTranscript(text, echoSource)
            } else {
                onFailure()
            }
        }
    }

    val speechController: SpeechInputSession? = remember(
        context,
        viewModel,
        localSpeechRecognitionAvailable,
        systemSpeechRecognitionActive,
        onlineSpeechRecognitionActive,
        configuredSystemSpeechService,
    ) {
        if (!speechRecognitionAvailable) {
            null
        } else {
            val onPartialResult: (String) -> Unit = { text ->
                consecutiveRecognizerTimeouts = 0
                speechDetectedInSession = true
                confirmPlaybackInterruption(text)
                val echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() }
                if (shouldPauseAssistantForPartial(audioQueue.playing, acousticSpeechConfirmedInSession)) {
                    pauseAssistantForSpeech()
                }
                if (
                    bargeInActive ||
                    !shouldSuppressPlaybackEcho(text, echoSource, acousticSpeechConfirmedInSession)
                ) {
                    viewModel.updateVoicePreview(text)
                }
            }
            val onFinalResult: (String, RecordedVoice?) -> Unit = { text, recording ->
                consecutiveRecognizerTimeouts = 0
                speechDetectedInSession = true
                confirmPlaybackInterruption(text)
                val echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() }
                if (
                    !bargeInActive &&
                    shouldSuppressPlaybackEcho(text, echoSource, acousticSpeechConfirmedInSession)
                ) {
                    recording?.file?.delete()
                    resumePlaybackAfterEcho()
                    viewModel.updateVoicePreview("")
                    restartListening()
                } else {
                    if (audioQueue.playing) pauseAssistantForSpeech()
                    transcribeAndFinish(
                        recording = recording,
                        fallbackText = text,
                        echoSource = echoSource,
                        onFailure = { handleRecognitionFailure(7, friendlySpeechError(7)) },
                    )
                }
            }
            val onRecognitionError: (Int, String, RecordedVoice?) -> Unit = { code, message, recording ->
                if (recording != null && speechDetectedInSession) {
                    val echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() }
                    transcribeAndFinish(
                        recording = recording,
                        fallbackText = null,
                        echoSource = echoSource,
                        onFailure = { handleRecognitionFailure(code, message) },
                    )
                } else {
                    recording?.file?.delete()
                    handleRecognitionFailure(code, message)
                }
            }
            val onAudioLevelChanged: (Float) -> Unit = { rmsDb ->
                if (!localSpeechRecognitionAvailable) {
                    if (audioQueue.playing) {
                        val shouldPause = playbackBargeInDetector.observe(
                            rmsDb = rmsDb,
                            elapsedMillis = SystemClock.elapsedRealtime(),
                            speakerphone = speakerphoneEnabled,
                        )
                        if (shouldPause) {
                            acousticBargeInCandidate = true
                        }
                    } else if (!audioQueue.pausedForSpeech && !speechDetectedInSession) {
                        playbackBargeInDetector.learnAmbient(
                            rmsDb = rmsDb,
                            speakerphone = speakerphoneEnabled,
                        )
                    } else {
                        playbackBargeInDetector.resetPlaybackWindow()
                    }
                }
            }
            val commonReady = {
                localError = null
                viewModel.setVoiceListening(true)
                viewModel.updateVoicePreview("请说话…")
            }
            val commonSpeechEnd = { viewModel.setVoiceTranscribing() }

            if (localSpeechRecognitionAvailable) {
                LocalStreamingSpeechController(
                    context = context,
                    onPartial = { utteranceId, text ->
                        activeLocalSpeechId = utteranceId
                        latestLocalPartialId = utteranceId
                        latestLocalPartialText = text
                        localUtteranceContexts.putIfAbsent(
                            utteranceId,
                            PhoneUtteranceContext(
                                echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() },
                                bargeInActive = bargeInActive,
                                acousticSpeechConfirmed = acousticSpeechConfirmedInSession,
                            ),
                        )
                        viewModel.setVoiceListening(true)
                        onPartialResult(text)
                        localUtteranceContexts[utteranceId] = localUtteranceContexts.getValue(utteranceId).copy(
                            bargeInActive = bargeInActive,
                            acousticSpeechConfirmed = acousticSpeechConfirmedInSession,
                        )
                    },
                    onResult = { utteranceId, text, recording ->
                        pendingLocalRefinements.remove(utteranceId)
                        recording?.file?.delete()
                        val utterance = localUtteranceContexts.remove(utteranceId) ?: PhoneUtteranceContext(
                            echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() },
                            bargeInActive = bargeInActive,
                            acousticSpeechConfirmed = acousticSpeechConfirmedInSession,
                        )
                        val newerSpeechActive = activeLocalSpeechId?.let { it > utteranceId } == true
                        val preservePreview = newerSpeechActive || latestLocalPartialId > utteranceId
                        val suppressAsEcho = !utterance.bargeInActive && shouldSuppressPlaybackEcho(
                            recognized = text,
                            spoken = utterance.echoSource,
                            acousticUserSpeechConfirmed = utterance.acousticSpeechConfirmed,
                        )
                        if (newerSpeechActive) {
                            if (!suppressAsEcho) {
                                suppressedAssistantCount = speechCursor.assistantCount
                                audioQueue.stop()
                                localError = null
                                viewModel.setVoiceListening(true)
                                viewModel.sendVoiceMessage(text)
                                if (preservePreview) viewModel.updateVoicePreview(latestLocalPartialText)
                            }
                        } else {
                            finishRecognizedTranscript(
                                text = text,
                                echoSource = utterance.echoSource,
                                utteranceBargeInActive = utterance.bargeInActive,
                                utteranceAcousticConfirmed = utterance.acousticSpeechConfirmed,
                                continuousLocalListening = true,
                                preservePreview = preservePreview,
                            )
                        }
                        if (activeLocalSpeechId == null && pendingLocalRefinements.isNotEmpty()) {
                            viewModel.setVoiceTranscribing()
                        }
                    },
                    onError = { code, message, recording ->
                        resetLocalUtteranceState()
                        if (audioQueue.pausedForSpeech) resumePlaybackAfterEcho()
                        onRecognitionError(code, message, recording)
                    },
                    onReady = commonReady,
                    onSpeechStart = { utteranceId ->
                        activeLocalSpeechId = utteranceId
                        latestLocalPartialId = maxOf(latestLocalPartialId, utteranceId)
                        latestLocalPartialText = ""
                        localUtteranceContexts.putIfAbsent(
                            utteranceId,
                            PhoneUtteranceContext(
                                echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() },
                                bargeInActive = bargeInActive,
                                acousticSpeechConfirmed = false,
                            ),
                        )
                        viewModel.setVoiceListening(true)
                        speechDetectedInSession = true
                        acousticBargeInCandidate = true
                    },
                    onSpeechEnd = { utteranceId ->
                        pendingLocalRefinements += utteranceId
                        localUtteranceContexts.putIfAbsent(utteranceId, PhoneUtteranceContext(
                            echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() },
                            bargeInActive = bargeInActive,
                            acousticSpeechConfirmed = acousticSpeechConfirmedInSession,
                        ))
                        if (activeLocalSpeechId == utteranceId) activeLocalSpeechId = null
                        speechDetectedInSession = false
                        acousticSpeechConfirmedInSession = false
                        acousticBargeInCandidate = false
                        commonSpeechEnd()
                    },
                    onSpeechCanceled = { utteranceId ->
                        localUtteranceContexts.remove(utteranceId)
                        if (activeLocalSpeechId == utteranceId) activeLocalSpeechId = null
                        speechDetectedInSession = false
                        acousticSpeechConfirmedInSession = false
                        acousticBargeInCandidate = false
                        if (audioQueue.pausedForSpeech) resumePlaybackAfterEcho()
                        viewModel.setVoiceListening(true)
                        viewModel.updateVoicePreview("")
                    },
                    onAudioProcessingConfigured = { processing ->
                        localEchoCancellationAvailable = processing.echoCancellationAvailable
                        if (!processing.echoCancellationAvailable && speakerphoneEnabled) {
                            speakerphoneEnabled = false
                            ttsNotice = "当前设备无法启用回声消除，已切换到听筒"
                        }
                    },
                )
            } else if (onlineSpeechRecognitionActive) {
                OnlineSpeechInputController(
                    context = context,
                    onResult = { recording ->
                        speechDetectedInSession = true
                        if (audioQueue.playing) pauseAssistantForSpeech()
                        transcribeAndFinish(
                            recording = recording,
                            fallbackText = null,
                            echoSource = ignoredPlaybackText.ifBlank { audioQueue.echoReferenceText() },
                            onFailure = {
                                handleRecognitionFailure(
                                    4,
                                    "在线语音识别失败，请检查 Gateway 的 ASR 配置",
                                )
                            },
                        )
                    },
                    onError = onRecognitionError,
                    onReady = commonReady,
                    onSpeechStart = {
                        speechDetectedInSession = true
                        acousticSpeechConfirmedInSession = true
                        if (audioQueue.playing) pauseAssistantForSpeech()
                    },
                    onSpeechEnd = commonSpeechEnd,
                    autoEndpoint = true,
                    voiceCommunication = true,
                    onAudioProcessingConfigured = { processing ->
                        localEchoCancellationAvailable = processing.echoCancellationAvailable
                        if (!processing.echoCancellationAvailable && speakerphoneEnabled) {
                            speakerphoneEnabled = false
                            ttsNotice = "当前设备无法启用回声消除，已切换到听筒"
                        }
                    },
                )
            } else {
                SpeechInputController(
                    context = context,
                    onPartial = onPartialResult,
                    onResult = onFinalResult,
                    onError = { code, message, recording ->
                        onRecognitionError(code, message, recording)
                    },
                    onReady = commonReady,
                    onSpeechStart = {
                        consecutiveRecognizerTimeouts = 0
                        speechDetectedInSession = true
                    },
                    onSpeechEnd = commonSpeechEnd,
                    onAudioLevel = onAudioLevelChanged,
                    captureEchoCanceledAudio = false,
                    recognitionService = configuredSystemSpeechService,
                )
            }
        }
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        hasPermission = granted
        if (granted) {
            localError = if (speechRecognitionAvailable) null else "当前设备没有可用的系统语音输入"
            listenRequest += 1
        } else {
            localError = "需要麦克风权限才能开始通话"
        }
    }

    LaunchedEffect(hasPermission) {
        if (!hasPermission) permissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
    }

    LaunchedEffect(ttsNotice) {
        if (ttsNotice != null) {
            delay(4_000L)
            ttsNotice = null
        }
    }

    LaunchedEffect(hasPermission) {
        if (hasPermission) VoiceCallService.start(context) else VoiceCallService.stop(context)
    }

    DisposableEffect(context) {
        onDispose { VoiceCallService.stop(context) }
    }

    DisposableEffect(audioRouter) {
        audioRouter.start()
        audioRouter.setSpeakerEnabled(false)
        onDispose { audioRouter.restore() }
    }

    LaunchedEffect(audioRouter, speakerphoneEnabled) {
        audioRouter.setSpeakerEnabled(speakerphoneEnabled)
    }

    val latestAssistant = state.messages.lastOrNull { it.role == ChatRole.ASSISTANT }
    LaunchedEffect(latestAssistant?.id, latestAssistant?.content, state.streaming, selectedVoice) {
        val message = latestAssistant ?: return@LaunchedEffect
        val assistantCount = state.messages.count { it.role == ChatRole.ASSISTANT }
        val suppressedCount = suppressedAssistantCount
        if (suppressedCount != null && assistantCount <= suppressedCount) {
            speechCursor = AssistantSpeechCursor(
                messageId = message.id,
                assistantCount = assistantCount,
                content = message.content,
                consumedChars = message.content.length,
            )
            return@LaunchedEffect
        }
        if (suppressedCount != null && assistantCount > suppressedCount) {
            suppressedAssistantCount = null
        }
        speechCursor = reconcileAssistantSpeechCursor(
            current = speechCursor,
            messageId = message.id,
            assistantCount = assistantCount,
            content = message.content,
        )
        var pending = message.content.drop(speechCursor.consumedChars.coerceAtMost(message.content.length))
        while (true) {
            val chunk = nextSpeechChunk(pending, flush = !state.streaming) ?: break
            audioQueue.enqueue(chunk.text, selectedVoice)
            speechCursor = speechCursor.copy(consumedChars = speechCursor.consumedChars + chunk.consumedChars)
            pending = message.content.drop(speechCursor.consumedChars.coerceAtMost(message.content.length))
        }
    }

    LaunchedEffect(
        microphoneEnabled,
        hasPermission,
        localError,
        listenRequest,
        continuousStreamingSpeech,
    ) {
        if (
            !microphoneEnabled ||
            !hasPermission ||
            localError != null ||
            speechController == null
        ) {
            resetLocalUtteranceState()
            speechController?.cancel()
            viewModel.setVoiceListening(false)
            return@LaunchedEffect
        }

        speechDetectedInSession = false
        acousticSpeechConfirmedInSession = false
        acousticBargeInCandidate = false
        if (continuousStreamingSpeech) {
            try {
                speechController.start()
            } catch (_: Exception) {
                restartDelayMillis = 500L
                listenRequest += 1
            }
            return@LaunchedEffect
        }

        speechController.cancel()
        // Give the recognizer a short reset window between sessions.
        delay(restartDelayMillis)
        restartDelayMillis = 280L
        viewModel.updateVoicePreview(
            "请说话…"
        )
        try {
            speechController.start()
        } catch (_: Exception) {
            localError = when (selectedPhoneSpeechEngine) {
                SpeechRecognitionEngine.X_ASR -> "X-ASR 启动失败，请检查本地模型"
                SpeechRecognitionEngine.ONLINE -> "在线语音识别启动失败"
                SpeechRecognitionEngine.SYSTEM -> "系统语音识别启动失败"
            }
            viewModel.setVoiceListening(false)
        }
    }

    DisposableEffect(speechController) {
        onDispose {
            resetLocalUtteranceState()
            speechController?.destroy()
            viewModel.setVoiceListening(false)
        }
    }

    DisposableEffect(audioQueue) {
        onDispose { audioQueue.close() }
    }

    val status = when {
        audioQueue.pausedForSpeech -> "正在听你说"
        audioQueue.speaking -> "Nexus 正在说话"
        state.voiceTranscribing -> "正在识别完整语句"
        state.streaming -> "正在思考并准备回答"
        localError != null -> "通话已暂停"
        continuousStreamingSpeech && !state.voiceListening ->
            "正在准备 X-ASR"
        !microphoneEnabled -> "麦克风已静音"
        !hasPermission || speechController == null -> "麦克风不可用"
        ttsNotice != null -> "朗读暂时中断，仍在监听"
        else -> "正在听你说"
    }
    val conversationMessages = remember(state.messages) {
        state.messages.filter { message ->
            message.content.isNotBlank() && message.role in setOf(ChatRole.USER, ChatRole.ASSISTANT)
        }
    }
    val voicePreview = visibleVoicePreview(
        preview = state.voicePreview,
        listening = state.voiceListening,
        transcribing = state.voiceTranscribing,
        latestUserMessage = conversationMessages.lastOrNull { it.role == ChatRole.USER }?.content,
    )
    val conversationListState = rememberLazyListState()
    LaunchedEffect(conversationMessages.lastOrNull()?.content, voicePreview, localError, ttsNotice) {
        val itemCount = conversationMessages.size +
            (if (voicePreview != null) 1 else 0) +
            (if (localError != null) 1 else 0) +
            (if (ttsNotice != null) 1 else 0) +
            (if (conversationMessages.isEmpty() && voicePreview == null) 1 else 0)
        if (itemCount > 0) conversationListState.scrollToItem(itemCount - 1)
    }

    fun toggleMicrophone() {
        microphoneEnabled = !microphoneEnabled
        if (!microphoneEnabled) {
            resetLocalUtteranceState()
            speechController?.cancel()
            viewModel.setVoiceListening(false)
        } else {
            localError = null
            listenRequest += 1
        }
    }

    fun endCall() {
        resetLocalUtteranceState()
        speechController?.cancel()
        audioQueue.stop()
        viewModel.stopStreaming()
        VoiceCallService.stop(context)
        viewModel.closeVoiceCall()
    }

    BackHandler(onBack = ::endCall)

    Column(
        Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .statusBarsPadding()
    ) {
        Surface(color = MaterialTheme.colorScheme.background) {
            Row(
                modifier = Modifier.fillMaxWidth().height(58.dp).padding(start = 16.dp, end = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Column(Modifier.weight(1f)) {
                    Text(
                        "Nexus 语音通话",
                        color = MaterialTheme.colorScheme.onSurface,
                        fontSize = 17.sp,
                        fontWeight = FontWeight.SemiBold,
                    )
                    Text(status, color = MaterialTheme.colorScheme.primary, fontSize = 12.sp)
                }
            }
        }
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        LazyColumn(
            state = conversationListState,
            modifier = Modifier.weight(1f).fillMaxWidth().padding(horizontal = 16.dp),
            contentPadding = PaddingValues(vertical = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            if (conversationMessages.isEmpty() && voicePreview == null) {
                item("empty") {
                    Text(
                        "开始和 Nexus 语音通话",
                        modifier = Modifier.fillMaxWidth().padding(vertical = 48.dp),
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 15.sp,
                        textAlign = TextAlign.Center,
                    )
                }
            }
            items(conversationMessages, key = { it.id }) { message ->
                VoiceCallMessageBubble(
                    text = message.content,
                    user = message.role == ChatRole.USER,
                )
            }
            voicePreview?.let { preview ->
                item("voice-preview") {
                    VoiceCallMessageBubble(text = preview, user = true, pending = true)
                }
            }
            localError?.let { error ->
                item("voice-error") {
                    Text(
                        error,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                        color = MaterialTheme.colorScheme.error,
                        fontSize = 13.sp,
                        textAlign = TextAlign.Center,
                    )
                }
            }
            ttsNotice?.let { notice ->
                item("tts-notice") {
                    Text(
                        notice,
                        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp),
                        color = MaterialTheme.colorScheme.tertiary,
                        fontSize = 13.sp,
                        textAlign = TextAlign.Center,
                    )
                }
            }
        }
        Row(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "音色",
                modifier = Modifier.weight(1f),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                fontSize = 13.sp,
            )
            Box {
                val selectedOption = VOICE_OPTIONS.first { it.id == selectedVoice }
                Surface(
                    onClick = { voiceMenuExpanded = true },
                    shape = RoundedCornerShape(8.dp),
                    color = MaterialTheme.colorScheme.surface,
                    border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
                ) {
                    Row(
                        modifier = Modifier.padding(start = 11.dp, top = 7.dp, end = 7.dp, bottom = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Icon(
                            Icons.AutoMirrored.Filled.VolumeUp,
                            contentDescription = null,
                            modifier = Modifier.size(18.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                        Text(
                            selectedOption.label,
                            modifier = Modifier.padding(start = 7.dp),
                            color = MaterialTheme.colorScheme.onSurface,
                            fontSize = 13.sp,
                        )
                        Icon(Icons.Filled.ArrowDropDown, contentDescription = null, tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                DropdownMenu(
                    expanded = voiceMenuExpanded,
                    onDismissRequest = { voiceMenuExpanded = false },
                ) {
                    VOICE_OPTIONS.forEach { option ->
                        DropdownMenuItem(
                            text = { Text(option.label) },
                            onClick = {
                                selectedVoice = option.id
                                voiceMenuExpanded = false
                            },
                            leadingIcon = {
                                if (selectedVoice == option.id) {
                                    Icon(Icons.Filled.Check, contentDescription = null, tint = MaterialTheme.colorScheme.primary)
                                }
                            },
                        )
                    }
                }
            }
        }
        Surface(color = MaterialTheme.colorScheme.surface, tonalElevation = 2.dp) {
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .navigationBarsPadding()
                    .height(102.dp)
                    .padding(horizontal = 32.dp),
                horizontalArrangement = Arrangement.SpaceEvenly,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                CallControl(
                    icon = if (microphoneEnabled) Icons.Filled.Mic else Icons.Filled.MicOff,
                    label = if (microphoneEnabled) "静音" else "取消静音",
                    active = !microphoneEnabled,
                    onClick = ::toggleMicrophone,
                )
                EndCallControl(onClick = ::endCall)
                CallControl(
                    icon = if (speakerphoneEnabled) Icons.Filled.SpeakerPhone else Icons.Filled.PhoneInTalk,
                    label = if (speakerphoneEnabled) "听筒" else "免提",
                    active = speakerphoneEnabled,
                ) {
                    if (!speakerphoneEnabled && selectedPhoneSpeechEngine == SpeechRecognitionEngine.SYSTEM) {
                        ttsNotice = "\u7cfb\u7edf\u8bed\u97f3\u8bc6\u522b\u65e0\u6cd5\u9a8c\u8bc1\u56de\u58f0\u6d88\u9664\u8d28\u91cf\uff0c\u514d\u63d0\u6682\u4e0d\u53ef\u7528"
                    } else if (!speakerphoneEnabled && !localSpeechRecognitionAvailable && !onlineSpeechRecognitionActive) {
                        ttsNotice = "当前没有可用的语音识别引擎，免提暂不可用"
                    } else if (!speakerphoneEnabled && localEchoCancellationAvailable != true) {
                        ttsNotice = if (localEchoCancellationAvailable == null) {
                            "正在检测回声消除能力，请稍后再试"
                        } else {
                            "当前设备无法启用回声消除，免提暂不可用"
                        }
                    } else {
                        speakerphoneEnabled = !speakerphoneEnabled
                    }
                }
            }
        }
    }
}

@Composable
private fun VoiceCallMessageBubble(text: String, user: Boolean, pending: Boolean = false) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = if (user) Arrangement.End else Arrangement.Start,
    ) {
        Surface(
            modifier = Modifier.fillMaxWidth(0.84f),
            shape = RoundedCornerShape(8.dp),
            color = if (user) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
            border = if (user) null else BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant),
        ) {
            Text(
                text,
                modifier = Modifier.padding(horizontal = 13.dp, vertical = 10.dp),
                color = (if (user) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface)
                    .copy(alpha = if (pending) 0.72f else 1f),
                fontSize = 15.sp,
                lineHeight = 22.sp,
            )
        }
    }
}

@Composable
private fun CallControl(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    label: String,
    active: Boolean = false,
    onClick: () -> Unit,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(
            onClick = onClick,
            modifier = Modifier.size(56.dp),
            shape = CircleShape,
            color = if (active) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant,
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    icon,
                    contentDescription = label,
                    tint = if (active) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        Spacer(Modifier.height(7.dp))
        Text(label, color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
    }
}

@Composable
private fun EndCallControl(onClick: () -> Unit) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(
            onClick = onClick,
            modifier = Modifier.size(64.dp),
            shape = CircleShape,
            color = MaterialTheme.colorScheme.error,
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    Icons.Filled.CallEnd,
                    contentDescription = "挂断",
                    tint = MaterialTheme.colorScheme.onError,
                    modifier = Modifier.size(28.dp),
                )
            }
        }
        Spacer(Modifier.height(5.dp))
        Text("挂断", color = MaterialTheme.colorScheme.onSurfaceVariant, fontSize = 12.sp)
    }
}
