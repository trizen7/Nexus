package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Test

class VoiceActivityDetectorTest {
    @Test
    fun `local streaming fallback submits stable text after acoustic silence`() {
        assertEquals(
            true,
            shouldForceLocalSpeechEndpoint(
                speechStarted = true,
                completedText = "今天天气怎么样",
                lastAcousticSpeechAtMillis = 1_000L,
                elapsedMillis = 1_000L + LOCAL_SPEECH_ENDPOINT_FALLBACK_MILLIS,
            ),
        )
    }

    @Test
    fun `local streaming fallback never submits noise or active speech`() {
        assertEquals(false, shouldForceLocalSpeechEndpoint(true, "", 1_000L, 5_000L))
        assertEquals(false, shouldForceLocalSpeechEndpoint(false, "误识别", 1_000L, 5_000L))
        assertEquals(false, shouldForceLocalSpeechEndpoint(true, "还在说", 1_000L, 2_799L))
        assertEquals(false, shouldForceLocalSpeechEndpoint(true, "没有声学证据", null, 5_000L))
    }

    @Test
    fun `silence waits indefinitely without completing or restarting`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(90) }
        detector.finishCalibration()

        var state = VoiceActivityState.WAITING_FOR_SPEECH
        for (elapsed in 0L..60_000L step 100L) {
            state = detector.observe(amplitude = 90, elapsedMillis = elapsed)
        }

        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, state)
    }

    @Test
    fun `short pause inside a sentence does not end the recording`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(80) }
        detector.finishCalibration()

        detector.observe(amplitude = 2_400, elapsedMillis = 0)
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(amplitude = 2_400, elapsedMillis = 200))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(amplitude = 80, elapsedMillis = 2_300))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(amplitude = 2_200, elapsedMillis = 2_350))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(amplitude = 80, elapsedMillis = 4_450))
        assertEquals(VoiceActivityState.UTTERANCE_COMPLETE, detector.observe(amplitude = 80, elapsedMillis = 4_550))
    }

    @Test
    fun `speech must be sustained before end silence can complete`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(50) }
        detector.finishCalibration()

        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(2_000, 0))
        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(50, 100))
        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(50, 5_000))
    }

    @Test
    fun `short empty amplitude poll does not discard speech onset`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(50) }
        detector.finishCalibration()

        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(900, 0))
        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(0, 80))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(850, 160))
    }

    @Test
    fun `low gain microphone can still complete an utterance`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(6) }
        detector.finishCalibration()

        assertEquals(VoiceActivityState.WAITING_FOR_SPEECH, detector.observe(24, 0))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(24, 160))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(6, 2_300))
        assertEquals(VoiceActivityState.UTTERANCE_COMPLETE, detector.observe(6, 2_400))
    }

    @Test
    fun `raised background after speech is still treated as silence`() {
        val detector = VoiceActivityDetector()
        repeat(4) { detector.calibrate(50) }
        detector.finishCalibration()

        detector.observe(1_000, 0)
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(1_000, 160))
        assertEquals(VoiceActivityState.SPEAKING, detector.observe(100, 2_300))
        assertEquals(VoiceActivityState.UTTERANCE_COMPLETE, detector.observe(100, 2_400))
    }
}
