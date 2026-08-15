package app.nexus.mobile

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PcmFrameBufferTest {
    @Test
    fun `buffer preserves frame order while decoder keeps up`() {
        val buffer = PcmFrameBuffer(capacity = 3, frameSamples = 2)
        listOf(shortArrayOf(1, 2), shortArrayOf(3, 4)).forEach { samples ->
            buffer.offer(samples, samples.size)
        }

        assertArrayEquals(shortArrayOf(1, 2), buffer.poll(0)!!.samples)
        assertArrayEquals(shortArrayOf(3, 4), buffer.poll(0)!!.samples)
        assertNull(buffer.poll(0))
        assertEquals(0, buffer.droppedFrameCount)
    }

    @Test
    fun `overflow drops oldest audio and keeps the latest frames`() {
        val buffer = PcmFrameBuffer(capacity = 2, frameSamples = 1)
        buffer.offer(shortArrayOf(1), 1)
        buffer.offer(shortArrayOf(2), 1)
        buffer.offer(shortArrayOf(3), 1)

        assertArrayEquals(shortArrayOf(2), buffer.poll(0)!!.samples)
        assertArrayEquals(shortArrayOf(3), buffer.poll(0)!!.samples)
        assertEquals(1, buffer.droppedFrameCount)
    }

    @Test
    fun `recycled frame storage can be acquired again`() {
        val buffer = PcmFrameBuffer(capacity = 1, frameSamples = 4)
        val samples = buffer.acquire()
        buffer.offer(samples, 3)
        val frame = buffer.poll(0)!!
        assertEquals(3, frame.count)
        buffer.recycle(frame)

        assertEquals(4, buffer.acquire().size)
    }

    @Test
    fun `frames captured while decoder resets remain queued in order`() {
        val buffer = PcmFrameBuffer(capacity = 25, frameSamples = 1)
        buffer.offer(shortArrayOf(1), 1)
        assertArrayEquals(shortArrayOf(1), buffer.poll(0)!!.samples)

        // AudioRecord continues writing while the online decoder resets its stream.
        buffer.offer(shortArrayOf(2), 1)
        buffer.offer(shortArrayOf(3), 1)

        assertArrayEquals(shortArrayOf(2), buffer.poll(0)!!.samples)
        assertArrayEquals(shortArrayOf(3), buffer.poll(0)!!.samples)
        assertEquals(0, buffer.droppedFrameCount)
    }

    @Test
    fun `speech rising from quiet during calibration triggers on the third speech frame`() {
        val detector = PcmBargeInDetector()
        assertFalse(detector.observe(frame(100), 8, 0L).triggered)

        val firstOnset = detector.observe(frame(500), 8, 20L)
        val secondOnset = detector.observe(frame(500), 8, 40L)
        assertFalse(firstOnset.triggered)
        assertTrue(firstOnset.speechCandidate)
        assertFalse(secondOnset.triggered)
        assertTrue(secondOnset.speechCandidate)
        assertTrue(detector.observe(frame(500), 8, 60L).triggered)
    }

    @Test
    fun `empty endpoint replays pre roll while speech onset is pending`() {
        assertTrue(shouldReplayPreRollAtEndpoint("", speechStarted = false, acousticCandidateInProgress = true))
        assertFalse(shouldReplayPreRollAtEndpoint("你好", speechStarted = false, acousticCandidateInProgress = true))
        assertFalse(shouldReplayPreRollAtEndpoint("", speechStarted = true, acousticCandidateInProgress = true))
        assertFalse(shouldReplayPreRollAtEndpoint("", speechStarted = false, acousticCandidateInProgress = false))
    }

    @Test
    fun `steady loud background at startup becomes the calibration floor without triggering`() {
        val detector = PcmBargeInDetector()

        repeat(16) { index ->
            assertFalse(detector.observe(frame(1_000), 8, index * 20L).triggered)
        }
    }

    @Test
    fun `clear speech rise above a loud calibrated background triggers on the third frame`() {
        val detector = PcmBargeInDetector()
        repeat(8) { index -> detector.observe(frame(1_000), 8, index * 20L) }

        assertFalse(detector.observe(frame(4_000), 8, 160L).triggered)
        assertFalse(detector.observe(frame(4_000), 8, 180L).triggered)
        assertTrue(detector.observe(frame(4_000), 8, 200L).triggered)
    }

    @Test
    fun `single impulse during calibration does not trigger barge in`() {
        val detector = PcmBargeInDetector()

        assertFalse(detector.observe(frame(1_000), 8, 0L).triggered)
        repeat(12) { index ->
            assertFalse(detector.observe(frame(100), 8, 20L + index * 20L).triggered)
        }
    }

    @Test
    fun `stable playback residual stays below the absolute speech floor`() {
        val detector = PcmBargeInDetector()

        repeat(30) { index ->
            assertFalse(detector.observe(frame(200), 8, index * 20L).triggered)
        }
    }

    @Test
    fun `quiet calibration still detects sustained low level speech`() {
        val detector = PcmBargeInDetector()
        repeat(8) { index -> detector.observe(frame(100), 8, index * 20L) }

        assertFalse(detector.observe(frame(300), 8, 160L).triggered)
        assertFalse(detector.observe(frame(300), 8, 180L).triggered)
        assertTrue(detector.observe(frame(300), 8, 200L).triggered)
    }

    @Test
    fun `slowly rising background does not trigger barge in`() {
        val detector = PcmBargeInDetector()
        repeat(8) { index -> detector.observe(frame(100), 8, index * 20L) }

        listOf(105, 120, 135, 150, 165, 180, 200, 220, 240).forEachIndexed { index, amplitude ->
            assertFalse(detector.observe(frame(amplitude), 8, 160L + index * 20L).triggered)
        }
    }

    @Test
    fun `barge in cooldown prevents repeated triggers`() {
        val detector = testBargeInDetector()
        repeat(3) { index -> detector.observe(frame(100), 8, index * 20L) }
        repeat(2) { index -> assertFalse(detector.observe(frame(500), 8, 60L + index * 20L).triggered) }
        assertTrue(detector.observe(frame(500), 8, 100L).triggered)

        repeat(3) { index ->
            assertFalse(detector.observe(frame(500), 8, 160L + index * 20L).triggered)
        }
        repeat(2) { index -> assertFalse(detector.observe(frame(100), 8, 520L + index * 20L).triggered) }
        repeat(2) { index -> assertFalse(detector.observe(frame(500), 8, 560L + index * 20L).triggered) }
        assertTrue(detector.observe(frame(500), 8, 600L).triggered)
    }

    @Test
    fun `barge in pre roll contains copied audio from before the trigger`() {
        val detector = PcmBargeInDetector(
            testBargeInConfig().copy(calibrationFrames = 2, preRollFrames = 5),
        )
        val sources = listOf(frame(100), frame(100), frame(500), frame(500), frame(500))

        detector.observe(sources[0], sources[0].size, 0L)
        detector.observe(sources[1], sources[1].size, 20L)
        detector.observe(sources[2], sources[2].size, 40L)
        detector.observe(sources[3], sources[3].size, 60L)
        val triggered = detector.observe(sources[4], sources[4].size, 80L)
        sources[0][0] = 9_999

        assertTrue(triggered.triggered)
        assertEquals(5, triggered.preRoll.size)
        assertArrayEquals(frame(100), triggered.preRoll.first())
        assertArrayEquals(frame(500), triggered.preRoll.last())
    }

    @Test
    fun `default barge in pre roll keeps one and a half seconds for quiet speech`() {
        val detector = PcmBargeInDetector()
        repeat(80) { index -> detector.observe(frame(100), 8, index * 20L) }

        assertEquals(75, detector.snapshotPreRoll().size)
    }

    private fun testBargeInDetector(): PcmBargeInDetector = PcmBargeInDetector(testBargeInConfig())

    private fun testBargeInConfig(): PcmBargeInConfig = PcmBargeInConfig(
        calibrationFrames = 3,
        windowFrames = 5,
        requiredVoicedFrames = 3,
        preRollFrames = 25,
        routeFloorDbfs = -42.0,
        requiredRiseDb = 9.0,
        noiseRiseAdaptation = 0.35,
        noiseFallAdaptation = 0.35,
        cooldownMillis = 500L,
    )

    private fun frame(amplitude: Int): ShortArray = ShortArray(8) { amplitude.toShort() }
}
