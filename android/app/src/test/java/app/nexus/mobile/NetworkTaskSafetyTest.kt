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
    fun `default HTTPS ports are treated as the same effective port`() {
        assertTrue(shouldAttachBearerToken("https://nexus.example.com:443", "https://nexus.example.com/file"))
        assertFalse(shouldAttachBearerToken("http://nexus.example.com", "http://nexus.example.com:80/file"))
    }

    @Test
    fun `HTTP server addresses are rejected without an insecure fallback`() {
        val error = serverUrlValidationError("http://192.168.1.20:18787")

        assertTrue(error?.contains("仅允许 HTTPS") == true)
    }

    @Test
    fun `bare local address defaults to HTTPS product test port`() {
        assertEquals("https://10.0.0.123:18788", normalizeServerUrl(" 10.0.0.123/ "))
        assertEquals("https://10.0.0.123:9443", normalizeServerUrl("10.0.0.123:9443"))
        assertEquals("https://nexus-box:18788", normalizeServerUrl("nexus-box"))
        assertEquals("https://nexus.local:18788", normalizeServerUrl("nexus.local"))
        assertEquals("https://[::1]:18788", normalizeServerUrl("::1"))
        assertEquals("https://[fd00::1]:18788", normalizeServerUrl("[fd00::1]"))
        assertEquals("https://nexus.example.com", normalizeServerUrl("nexus.example.com"))
        assertNull(serverUrlValidationError("10.0.0.123"))
        assertNull(serverUrlValidationError("[fd00::1]"))
    }

    @Test
    fun `stored legacy local address migrates from HTTP 18787 to HTTPS 18788 only`() {
        assertEquals(
            "https://10.0.0.123:18788",
            migrateStoredServerUrl("http://10.0.0.123:18787")
        )
        assertEquals(
            "http://nexus.example.com:18787",
            migrateStoredServerUrl("http://nexus.example.com:18787")
        )
        assertEquals(
            "http://10.0.0.123:8787",
            migrateStoredServerUrl("http://10.0.0.123:8787")
        )
    }

    @Test
    fun `server address rejects unsupported schemes unsafe authority and invalid ports`() {
        assertTrue(serverUrlValidationError("ftp://10.0.0.123/file") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com/?token=secret") != null)
        assertTrue(serverUrlValidationError("https://user@nexus.example.com") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com/#fragment") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com:0") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com:65536") != null)
    }

    @Test
    fun `legacy migration does not rewrite unsafe or unrelated addresses`() {
        assertEquals(
            "http://user@10.0.0.123:18787",
            migrateStoredServerUrl("http://user@10.0.0.123:18787")
        )
        assertEquals(
            "http://10.0.0.123:18787?token=secret",
            migrateStoredServerUrl("http://10.0.0.123:18787?token=secret")
        )
        assertEquals(
            "https://10.0.0.123:18788",
            migrateStoredServerUrl("https://10.0.0.123:18788")
        )
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
