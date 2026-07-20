package app.nexus.mobile

import java.net.URI

fun shouldAttachBearerToken(serverUrl: String, resourceUrl: String): Boolean {
    val server = parseHttpUri(serverUrl) ?: return false
    val candidate = runCatching { URI(resourceUrl.trim()) }.getOrNull() ?: return false
    val resource = (if (candidate.isAbsolute) parseHttpUri(resourceUrl) else server.resolve(candidate))
        ?: return false
    return server.scheme.equals(resource.scheme, ignoreCase = true) &&
        server.host.equals(resource.host, ignoreCase = true) &&
        effectivePort(server) == effectivePort(resource)
}

fun bearerTokenFor(serverUrl: String, resourceUrl: String, token: String): String? =
    token.takeIf(String::isNotBlank)?.takeIf { shouldAttachBearerToken(serverUrl, resourceUrl) }

fun requiresInsecureHttpConfirmation(serverUrl: String): Boolean =
    parseHttpUri(serverUrl)?.scheme.equals("http", ignoreCase = true)

private fun parseHttpUri(value: String): URI? = runCatching { URI(value.trim()) }.getOrNull()
    ?.takeIf { it.scheme.equals("http", true) || it.scheme.equals("https", true) }
    ?.takeIf { !it.host.isNullOrBlank() }

private fun effectivePort(uri: URI): Int = when {
    uri.port >= 0 -> uri.port
    uri.scheme.equals("https", true) -> 443
    else -> 80
}

internal class SessionMonitorRegistry<T : Any> {
    data class Entry<T>(val sessionId: String, val value: T)
    data class Removal<T>(val removed: Boolean, val wasOwner: Boolean, val nextOwner: Entry<T>?)

    private val entries = linkedMapOf<String, T>()
    private var ownerSessionId: String? = null
    val size: Int get() = entries.size

    @Synchronized
    fun put(sessionId: String, value: T): T? {
        val previous = entries.put(sessionId, value)
        ownerSessionId = sessionId
        return previous
    }

    @Synchronized
    fun remove(sessionId: String, value: T): Removal<T> {
        val removed = entries[sessionId] == value && entries.remove(sessionId) != null
        val wasOwner = removed && ownerSessionId == sessionId
        if (wasOwner) ownerSessionId = entries.keys.lastOrNull()
        return Removal(removed, wasOwner, owner())
    }

    @Synchronized
    fun owner(): Entry<T>? = ownerSessionId?.let { id -> entries[id]?.let { Entry(id, it) } }

    @Synchronized
    fun values(): List<T> = entries.values.toList()

    @Synchronized
    fun clear() {
        entries.clear()
        ownerSessionId = null
    }
}

internal inline fun performLocalLogoutCleanup(
    cancelStream: () -> Unit,
    cancelUploads: () -> Unit,
    cancelDownloads: () -> Unit,
    cancelMonitors: () -> Unit,
    cancelNotifications: () -> Unit
) {
    cancelStream()
    cancelUploads()
    cancelDownloads()
    cancelMonitors()
    cancelNotifications()
}

enum class LockscreenPrivacy { PRIVATE }

val DEFAULT_LOCKSCREEN_PRIVACY = LockscreenPrivacy.PRIVATE
