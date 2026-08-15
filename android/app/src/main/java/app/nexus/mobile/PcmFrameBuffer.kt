package app.nexus.mobile

import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ConcurrentLinkedQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.math.log10
import kotlin.math.sqrt

internal data class PcmFrame(val samples: ShortArray, val count: Int)

internal class PcmFrameBuffer(
    capacity: Int,
    private val frameSamples: Int,
) {
    private val frames = ArrayBlockingQueue<PcmFrame>(capacity)
    private val reusable = ConcurrentLinkedQueue<ShortArray>().apply {
        repeat(capacity + 1) { offer(ShortArray(frameSamples)) }
    }
    private val dropped = AtomicInteger(0)

    val droppedFrameCount: Int
        get() = dropped.get()

    fun acquire(): ShortArray = reusable.poll() ?: ShortArray(frameSamples)

    fun offer(samples: ShortArray, count: Int) {
        require(count in 1..samples.size)
        val frame = PcmFrame(samples, count)
        if (frames.offer(frame)) return
        frames.poll()?.let { oldest ->
            recycle(oldest)
            dropped.incrementAndGet()
        }
        if (!frames.offer(frame)) {
            recycle(frame)
            dropped.incrementAndGet()
        }
    }

    fun poll(timeoutMillis: Long): PcmFrame? =
        frames.poll(timeoutMillis, TimeUnit.MILLISECONDS)

    fun recycle(frame: PcmFrame) {
        recycle(frame.samples)
    }

    fun recycle(samples: ShortArray) {
        if (samples.size == frameSamples) reusable.offer(samples)
    }

    fun clear() {
        while (true) recycle(frames.poll() ?: return)
    }
}

internal data class PcmBargeInConfig(
    val calibrationFrames: Int = 8,
    val windowFrames: Int = 5,
    val requiredVoicedFrames: Int = 3,
    val preRollFrames: Int = 75,
    val routeFloorDbfs: Double = -42.0,
    val requiredRiseDb: Double = 9.0,
    val noiseRiseAdaptation: Double = 0.10,
    val noiseFallAdaptation: Double = 0.22,
    val cooldownMillis: Long = 500L,
)

internal data class PcmBargeInObservation(
    val triggered: Boolean,
    val levelDbfs: Double,
    val speechCandidate: Boolean = false,
    val preRoll: List<ShortArray> = emptyList(),
)

/**
 * Playback barge-in gate for local PCM. The ASR decoder remains ungated; this
 * history only makes the audio preceding the acoustic trigger explicit and testable.
 */
internal class PcmBargeInDetector(
    private val config: PcmBargeInConfig = PcmBargeInConfig(),
) {
    private val calibration = ArrayDeque<Double>()
    private val voicedWindow = ArrayDeque<Boolean>()
    private val preRoll = ArrayDeque<ShortArray>()
    private var noiseFloorDbfs: Double? = null
    private var lastTriggeredAt = Long.MIN_VALUE
    private var calibrationObservedFrames = 0

    init {
        require(config.calibrationFrames > 0)
        require(config.windowFrames > 0)
        require(config.requiredVoicedFrames in 1..config.windowFrames)
        require(config.preRollFrames > 0)
        require(config.cooldownMillis >= 0L)
    }

    fun observe(samples: ShortArray, count: Int, elapsedMillis: Long): PcmBargeInObservation {
        require(count in 1..samples.size)
        remember(samples, count)
        val level = pcmRmsDbfs(samples, count)
        val calibrating = calibrationObservedFrames < config.calibrationFrames
        val provisionalNoise = if (calibrating) calibratedNoiseFloorOrNull() else null
        if (calibrating) {
            calibrationObservedFrames += 1
            calibration.addLast(level)
            if (calibrationObservedFrames == config.calibrationFrames) {
                noiseFloorDbfs = calibratedNoiseFloorOrNull() ?: level
            }
        }

        val noise = provisionalNoise ?: noiseFloorDbfs ?: level
        val threshold = maxOf(config.routeFloorDbfs, noise + config.requiredRiseDb)
        // During calibration the current level cannot establish its own speech threshold.
        // A provisional trigger requires a quieter frame observed before it; otherwise a
        // call opened in steady loud background noise would interrupt playback immediately.
        val voiced = (!calibrating || provisionalNoise != null) && level >= threshold
        voicedWindow.addLast(voiced)
        while (voicedWindow.size > config.windowFrames) voicedWindow.removeFirst()

        if (!voiced && calibrationObservedFrames >= config.calibrationFrames) {
            val adaptation = if (level > noise) config.noiseRiseAdaptation else config.noiseFallAdaptation
            noiseFloorDbfs = noise + (level - noise) * adaptation
        }

        val candidateConfirmed = voicedWindow.size >= config.requiredVoicedFrames &&
            voicedWindow.takeLast(config.requiredVoicedFrames).all { it }
        val speechCandidate = voicedWindow.any { it }
        val cooldownElapsed = lastTriggeredAt == Long.MIN_VALUE || elapsedMillis - lastTriggeredAt >= config.cooldownMillis
        val triggered = cooldownElapsed && candidateConfirmed
        if (candidateConfirmed && !cooldownElapsed) voicedWindow.clear()
        if (!triggered) {
            return PcmBargeInObservation(
                triggered = false,
                levelDbfs = level,
                speechCandidate = speechCandidate,
            )
        }

        lastTriggeredAt = elapsedMillis
        voicedWindow.clear()
        return PcmBargeInObservation(
            triggered = true,
            levelDbfs = level,
            speechCandidate = true,
            preRoll = preRoll.map(ShortArray::copyOf),
        )
    }

    fun resetSpeechWindow() {
        voicedWindow.clear()
        preRoll.clear()
    }

    fun snapshotPreRoll(): List<ShortArray> = preRoll.map(ShortArray::copyOf)

    private fun remember(samples: ShortArray, count: Int) {
        preRoll.addLast(samples.copyOf(count))
        while (preRoll.size > config.preRollFrames) preRoll.removeFirst()
    }

    private fun calibratedNoiseFloorOrNull(): Double? {
        if (calibration.isEmpty()) return null
        val sorted = calibration.sorted()
        val percentileIndex = ((sorted.lastIndex) * QUIET_PERCENTILE).toInt()
        return sorted[percentileIndex]
    }

    private companion object {
        const val QUIET_PERCENTILE = 0.20
    }
}

internal fun pcmRmsDbfs(samples: ShortArray, count: Int = samples.size): Double {
    require(count in 1..samples.size)
    var sumSquares = 0.0
    for (index in 0 until count) {
        val normalized = samples[index] / 32768.0
        sumSquares += normalized * normalized
    }
    val rms = sqrt(sumSquares / count).coerceAtLeast(1.0 / 32768.0)
    return 20.0 * log10(rms)
}
