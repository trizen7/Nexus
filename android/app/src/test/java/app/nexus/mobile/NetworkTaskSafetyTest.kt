package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NetworkTaskSafetyTest {
    @Test
    fun `bearer token is sent only to the configured Nexus origin`() {
        val server = "https://Nexus.Example.com/api/"

        assertTrue(shouldAttachBearerToken(server, "https://nexus.example.com/files/1"))
        assertTrue(shouldAttachBearerToken(server, "/api/files/1"))
        assertTrue(shouldAttachBearerToken(server, "files/1"))
        assertFalse(shouldAttachBearerToken(server, "http://nexus.example.com/files/1"))
        assertFalse(shouldAttachBearerToken(server, "https://nexus.example.com:444/files/1"))
        assertFalse(shouldAttachBearerToken(server, "https://cdn.example.com/files/1"))
        assertFalse(shouldAttachBearerToken(server, "not a valid url"))
    }

    @Test
    fun `default ports are treated as the same effective port`() {
        assertTrue(shouldAttachBearerToken("https://nexus.example.com:443", "https://nexus.example.com/file"))
        assertTrue(shouldAttachBearerToken("http://nexus.example.com", "http://nexus.example.com:80/file"))
    }

    @Test
    fun `http login requires confirmation while https does not`() {
        assertTrue(requiresInsecureHttpConfirmation("http://192.168.1.20:8787"))
        assertFalse(requiresInsecureHttpConfirmation("https://nexus.example.com"))
        assertFalse(requiresInsecureHttpConfirmation("HTTPS://nexus.example.com"))
    }

    @Test
    fun `bare local address is normalized to confirmed http url`() {
        assertEquals("http://10.0.0.123:18787", normalizeServerUrl(" 10.0.0.123:18787/ "))
        assertTrue(requiresInsecureHttpConfirmation("10.0.0.123:18787"))
        assertNull(serverUrlValidationError("10.0.0.123:18787"))
    }

    @Test
    fun `server address rejects unsupported schemes and query parameters`() {
        assertTrue(serverUrlValidationError("ftp://10.0.0.123/file") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com/?token=secret") != null)
    }

    @Test
    fun `monitor registry keeps different sessions and replaces only duplicate session`() {
        val registry = SessionMonitorRegistry<String>()

        assertNull(registry.put("one", "job-1"))
        assertNull(registry.put("two", "job-2"))
        assertEquals("job-1", registry.put("one", "job-1-new"))
        assertEquals(setOf("job-1-new", "job-2"), registry.values().toSet())
        assertEquals(2, registry.size)
    }

    @Test
    fun `removing foreground owner selects another running session`() {
        val registry = SessionMonitorRegistry<String>()
        registry.put("one", "job-1")
        registry.put("two", "job-2")

        val removal = registry.remove("two", "job-2")

        assertTrue(removal.removed)
        assertTrue(removal.wasOwner)
        assertEquals("one", removal.nextOwner?.sessionId)
        assertEquals("job-1", removal.nextOwner?.value)
    }

    @Test
    fun `removing non owner leaves current foreground owner unchanged`() {
        val registry = SessionMonitorRegistry<String>()
        registry.put("one", "job-1")
        registry.put("two", "job-2")

        val removal = registry.remove("one", "job-1")

        assertTrue(removal.removed)
        assertFalse(removal.wasOwner)
        assertEquals("two", removal.nextOwner?.sessionId)
    }

    @Test
    fun `logout cleanup remains local and never stops the remote run`() {
        val actions = mutableListOf<String>()

        performLocalLogoutCleanup(
            cancelStream = { actions += "stream" },
            cancelUploads = { actions += "uploads" },
            cancelDownloads = { actions += "downloads" },
            cancelMonitors = { actions += "monitors" },
            cancelNotifications = { actions += "notifications" }
        )

        assertEquals(listOf("stream", "uploads", "downloads", "monitors", "notifications"), actions)
        assertFalse(actions.contains("stop-remote-run"))
    }

    @Test
    fun `notification lockscreen privacy defaults to private`() {
        assertEquals(LockscreenPrivacy.PRIVATE, DEFAULT_LOCKSCREEN_PRIVACY)
    }
}
