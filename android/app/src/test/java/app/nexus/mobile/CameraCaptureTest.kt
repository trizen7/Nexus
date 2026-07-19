package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraCaptureTest {
    @Test
    fun `camera image names are unique jpeg files`() {
        val first = cameraImageFileName(1000L)
        val second = cameraImageFileName(1001L)

        assertTrue(first.endsWith(".jpg"))
        assertEquals(false, first == second)
    }
}
