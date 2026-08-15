package app.nexus.mobile

import kotlin.math.max

internal enum class VoiceActivityState {
    WAITING_FOR_SPEECH,
    SPEAKING,
    UTTERANCE_COMPLETE,
}

internal data class VoiceActivityConfig(
    val minimumSpeechAmplitude: Int = 8,
    val noiseMultiplier: Double = 1.5,
    val minimumRiseAboveNoise: Int = 4,
    val minimumSpeechMillis: Long = 120,
    val speechGapToleranceMillis: Long = 240,
    val endSilenceMillis: Long = 2_200,
    val noiseAdaptation: Double = 0.12,
)

internal class VoiceActivityDetector(
    private val config: VoiceActivityConfig = VoiceActivityConfig(),
) {
    private var state = VoiceActivityState.WAITING_FOR_SPEECH
    private var noiseFloor: Double? = null
    private var calibrationFinished = false
    private var speechCandidateAt: Long? = null
    private var lastSpeechCandidateAt: Long? = null
    private var speechCandidatePeak = 0
    private var speechReference = 0.0
    private var lastSpeechAt: Long? = null

    fun calibrate(amplitude: Int) {
        if (calibrationFinished) return
        val level = amplitude.coerceAtLeast(0)
        if (level == 0) return
        val current = noiseFloor
        noiseFloor = if (current == null) {
            level.toDouble()
        } else {
            val adaptation = if (level < current) 0.45 else 0.18
            current + (level - current) * adaptation
        }
    }

    fun finishCalibration() {
        calibrationFinished = true
        if (noiseFloor == null) noiseFloor = 1.0
    }

    fun observe(amplitude: Int, elapsedMillis: Long): VoiceActivityState {
        if (state == VoiceActivityState.UTTERANCE_COMPLETE) return state
        if (!calibrationFinished) finishCalibration()

        val level = amplitude.coerceAtLeast(0)
        val baseline = noiseFloor ?: 1.0
        val threshold = max(
            config.minimumSpeechAmplitude.toDouble(),
            max(baseline * config.noiseMultiplier, baseline + config.minimumRiseAboveNoise),
        )
        val voiced = level >= threshold

        when (state) {
            VoiceActivityState.WAITING_FOR_SPEECH -> {
                if (voiced) {
                    speechCandidatePeak = maxOf(speechCandidatePeak, level)
                    val candidateAt = speechCandidateAt ?: elapsedMillis.also { speechCandidateAt = it }
                    lastSpeechCandidateAt = elapsedMillis
                    if (elapsedMillis - candidateAt >= config.minimumSpeechMillis) {
                        state = VoiceActivityState.SPEAKING
                        speechReference = maxOf(speechCandidatePeak, level).toDouble()
                        lastSpeechAt = elapsedMillis
                    }
                } else {
                    val lastCandidate = lastSpeechCandidateAt
                    if (lastCandidate == null || elapsedMillis - lastCandidate > config.speechGapToleranceMillis) {
                        speechCandidateAt = null
                        lastSpeechCandidateAt = null
                        speechCandidatePeak = 0
                        noiseFloor = baseline + (level - baseline) * config.noiseAdaptation
                    }
                }
            }

            VoiceActivityState.SPEAKING -> {
                if (level > speechReference) {
                    speechReference += (level - speechReference) * 0.2
                }
                val continuationThreshold = max(threshold, speechReference * 0.3)
                if (level >= continuationThreshold) lastSpeechAt = elapsedMillis
                val lastVoice = lastSpeechAt ?: elapsedMillis
                if (elapsedMillis - lastVoice >= config.endSilenceMillis) {
                    state = VoiceActivityState.UTTERANCE_COMPLETE
                }
            }

            VoiceActivityState.UTTERANCE_COMPLETE -> Unit
        }
        return state
    }
}
