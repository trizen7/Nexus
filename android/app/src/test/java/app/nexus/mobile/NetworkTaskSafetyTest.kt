package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NetworkTaskSafetyTest {
    @Test
    fun `bearer token is sent only to the configured Nexus origin`() {
        val secureServer = "https://Nexus.Example.com/api/"

        assertTrue(shouldAttachBearerToken(secureServer, "https://nexus.example.com/files/1"))
        assertTrue(shouldAttachBearerToken(secureServer, "/api/files/1"))
        assertTrue(shouldAttachBearerToken(secureServer, "files/1"))
        assertFalse(shouldAttachBearerToken(secureServer, "http://nexus.example.com/files/1"))
        assertFalse(shouldAttachBearerToken(secureServer, "https://nexus.example.com:444/files/1"))
        assertFalse(shouldAttachBearerToken(secureServer, "https://cdn.example.com/files/1"))
        assertFalse(shouldAttachBearerToken(secureServer, "not a valid url"))

        val localServer = "http://192.168.1.20:18787/api/"
        assertTrue(shouldAttachBearerToken(localServer, "http://192.168.1.20:18787/files/1"))
        assertTrue(shouldAttachBearerToken(localServer, "/api/files/1"))
        assertFalse(shouldAttachBearerToken(localServer, "http://192.168.1.20:8080/files/1"))
        assertFalse(shouldAttachBearerToken("http://nexus.example.com", "http://nexus.example.com/file"))
    }

    @Test
    fun `default HTTPS port and explicit local HTTP port are origin safe`() {
        assertTrue(shouldAttachBearerToken("https://nexus.example.com:443", "https://nexus.example.com/file"))
        assertTrue(shouldAttachBearerToken("http://10.0.0.123:18787", "http://10.0.0.123:18787/file"))
        assertFalse(shouldAttachBearerToken("http://10.0.0.123:18787", "https://10.0.0.123:18787/file"))
    }

    @Test
    fun `private HTTP is allowed while public HTTP is rejected`() {
        assertNull(serverUrlValidationError("http://192.168.1.20:18787"))
        assertNull(serverUrlValidationError("http://localhost:18787"))
        assertNull(serverUrlValidationError("https://nexus.example.com"))

        val error = serverUrlValidationError("http://nexus.example.com")
        assertTrue(error?.contains("公网 HTTP") == true)
        assertTrue(error?.contains("HTTPS 反向代理") == true)
    }

    @Test
    fun `bare local address defaults to HTTP product test port`() {
        assertEquals("http://10.0.0.123:18787", normalizeServerUrl(" 10.0.0.123/ "))
        assertEquals("http://10.0.0.123:9443", normalizeServerUrl("10.0.0.123:9443"))
        assertEquals("https://nexus-box", normalizeServerUrl("nexus-box"))
        assertEquals("http://nexus.local:18787", normalizeServerUrl("nexus.local"))
        assertEquals("http://[::1]:18787", normalizeServerUrl("::1"))
        assertEquals("http://[fd00::1]:18787", normalizeServerUrl("[fd00::1]"))
        assertEquals("https://nexus.example.com", normalizeServerUrl("nexus.example.com"))
        assertNull(serverUrlValidationError("10.0.0.123"))
        assertNull(serverUrlValidationError("[fd00::1]"))
    }

    @Test
    fun `stored legacy local HTTPS address migrates back to HTTP 18787 only`() {
        assertEquals(
            "http://10.0.0.123:18787",
            migrateStoredServerUrl("https://10.0.0.123:18788")
        )
        assertEquals(
            "https://nexus.example.com:18788",
            migrateStoredServerUrl("https://nexus.example.com:18788")
        )
        assertEquals(
            "https://10.0.0.123:9443",
            migrateStoredServerUrl("https://10.0.0.123:9443")
        )
        assertEquals(
            "http://10.0.0.123:18787",
            migrateStoredServerUrl("http://10.0.0.123:18787")
        )
    }

    @Test
    fun `server address rejects unsupported schemes public HTTP unsafe authority and invalid ports`() {
        assertTrue(serverUrlValidationError("ftp://10.0.0.123/file") != null)
        assertTrue(serverUrlValidationError("http://nexus.example.com") != null)
        assertTrue(serverUrlValidationError("http://nexus-box") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com/?token=secret") != null)
        assertTrue(serverUrlValidationError("https://user@nexus.example.com") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com/#fragment") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com:0") != null)
        assertTrue(serverUrlValidationError("https://nexus.example.com:65536") != null)
    }

    @Test
    fun `legacy migration does not rewrite unsafe or unrelated addresses`() {
        assertEquals(
            "https://user@10.0.0.123:18788",
            migrateStoredServerUrl("https://user@10.0.0.123:18788")
        )
        assertEquals(
            "https://10.0.0.123:18788?token=secret",
            migrateStoredServerUrl("https://10.0.0.123:18788?token=secret")
        )
        assertEquals(
            "https://10.0.0.123:18788#fragment",
            migrateStoredServerUrl("https://10.0.0.123:18788#fragment")
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
