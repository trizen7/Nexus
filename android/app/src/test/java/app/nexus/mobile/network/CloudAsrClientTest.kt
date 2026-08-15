package app.nexus.mobile.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CloudAsrClientTest {
    @Test
    fun `capabilities parse configured streaming format`() {
        val value = parseCloudAsrCapabilities(
            """{"available":true,"protocol":"nexus.asr.v1","sample_rate":16000,"channels":1,"encoding":"pcm_s16le","frame_millis":20,"supports_partial":true,"supports_punctuation":true}"""
        )

        assertTrue(value.available)
        assertEquals(CLOUD_ASR_PROTOCOL, value.protocol)
        assertEquals(16_000, value.sampleRate)
        assertEquals(1, value.channels)
        assertEquals("pcm_s16le", value.encoding)
        assertEquals(20, value.frameMillis)
        assertTrue(value.supportsPartial)
        assertTrue(value.supportsPunctuation)
    }

    @Test
    fun `capabilities do not assume streaming partials when provider omits them`() {
        val value = parseCloudAsrCapabilities(
            """{"available":true,"protocol":"nexus.asr.v1","sample_rate":16000,"channels":1,"encoding":"pcm_s16le","frame_millis":20}"""
        )

        assertFalse(value.supportsPartial)
        assertFalse(value.supportsPunctuation)
    }

    @Test
    fun `event parser rejects malformed identifiers`() {
        assertNull(parseCloudAsrEvent("""{"type":"partial","utterance_id":0,"text":"x","revision":1}"""))
        assertNull(parseCloudAsrEvent("""{"type":"partial","utterance_id":1,"text":"x","revision":-1}"""))
        assertNull(parseCloudAsrEvent("not-json"))
    }

    @Test
    fun `event gate drops stale partials and duplicate finals`() {
        val gate = CloudAsrEventGate()

        assertTrue(gate.accept(CloudAsrEvent.Partial(1, "你", 1)))
        assertFalse(gate.accept(CloudAsrEvent.Partial(1, "旧", 1)))
        assertTrue(gate.accept(CloudAsrEvent.Partial(1, "你好", 2)))
        assertTrue(gate.accept(CloudAsrEvent.Final(1, "你好。")))
        assertFalse(gate.accept(CloudAsrEvent.Final(1, "重复")))
        assertFalse(gate.accept(CloudAsrEvent.Partial(1, "迟到", 3)))
        assertTrue(gate.accept(CloudAsrEvent.Partial(2, "下一句", 1)))
    }

    @Test
    fun `event gate keeps an older final after the next utterance starts`() {
        val gate = CloudAsrEventGate()

        assertTrue(gate.accept(CloudAsrEvent.Partial(1, "第一句", 1)))
        assertTrue(gate.accept(CloudAsrEvent.SpeechStart(2)))
        assertTrue(gate.accept(CloudAsrEvent.Partial(2, "第二句", 1)))
        assertTrue(gate.accept(CloudAsrEvent.Final(1, "第一句。")))
        assertFalse(gate.accept(CloudAsrEvent.Final(1, "重复")))
    }
}
