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
    private val entries = java.util.concurrent.ConcurrentHashMap<String, T>()
    val size: Int get() = entries.size

    fun put(sessionId: String, value: T): T? = entries.put(sessionId, value)
    fun remove(sessionId: String, value: T): Boolean = entries.remove(sessionId, value)
    fun values(): List<T> = entries.values.toList()
    fun clear() = entries.clear()
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
