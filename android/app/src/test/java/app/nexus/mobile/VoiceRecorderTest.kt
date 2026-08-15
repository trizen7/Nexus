package app.nexus.mobile

import java.nio.ByteBuffer
import java.nio.ByteOrder
import org.junit.Assert.assertEquals
import org.junit.Test

class VoiceRecorderTest {
    @Test
    fun `wav header describes mono 16khz pcm payload`() {
        val header = VoiceRecorder.wavHeader(32_000)
        val values = ByteBuffer.wrap(header).order(ByteOrder.LITTLE_ENDIAN)

        assertEquals("RIFF", header.copyOfRange(0, 4).toString(Charsets.US_ASCII))
        assertEquals(32_036, values.getInt(4))
        assertEquals("WAVE", header.copyOfRange(8, 12).toString(Charsets.US_ASCII))
        assertEquals(1, values.getShort(22).toInt())
        assertEquals(16_000, values.getInt(24))
        assertEquals(16, values.getShort(34).toInt())
        assertEquals("data", header.copyOfRange(36, 40).toString(Charsets.US_ASCII))
        assertEquals(32_000, values.getInt(40))
    }
}
