package app.nexus.mobile

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI


fun shouldAttachBearerToken(serverUrl: String, resourceUrl: String): Boolean {
    val normalizedServer = normalizeServerUrl(serverUrl)
    if (serverUrlValidationError(normalizedServer) != null) return false
    val server = parseHttpUri(normalizedServer)
        ?.takeIf { it.userInfo == null && it.rawQuery == null && it.rawFragment == null }
        ?: return false
    val candidate = runCatching { URI(resourceUrl.trim()) }.getOrNull() ?: return false
    val resource = (if (candidate.isAbsolute) parseHttpUri(resourceUrl) else server.resolve(candidate)?.let {
        parseHttpUri(it.toString())
    })?.takeIf { it.userInfo == null } ?: return false
    return server.scheme.equals(resource.scheme, ignoreCase = true) &&
        server.host.equals(resource.host, ignoreCase = true) &&
        effectivePort(server) == effectivePort(resource)
}

fun bearerTokenFor(serverUrl: String, resourceUrl: String, token: String): String? =
    token.takeIf(String::isNotBlank)?.takeIf { shouldAttachBearerToken(serverUrl, resourceUrl) }

fun normalizeServerUrl(serverUrl: String): String {
    val trimmed = serverUrl.trim()
    if (trimmed.isEmpty()) return ""
    val withoutTrailingSlash = trimmed.trimEnd('/')
    return when {
        trimmed.startsWith("http://", ignoreCase = true) ||
            trimmed.startsWith("https://", ignoreCase = true) ||
            "://" in trimmed -> withoutTrailingSlash
        looksLikeBareIpv6Address(withoutTrailingSlash) -> "http://[$withoutTrailingSlash]"
        else -> "http://$withoutTrailingSlash"
    }
}

fun serverUrlValidationError(serverUrl: String): String? {
    val normalized = normalizeServerUrl(serverUrl)
    if (normalized.isBlank()) return "请填写服务器地址"
    val parsed = parseHttpUri(normalized)
        ?: return "服务器地址格式不正确，请填写例如 http://10.0.0.123:端口 或 https://你的域名"
    if (parsed.userInfo != null || parsed.rawQuery != null || parsed.rawFragment != null) {
        return "服务器地址不能包含账号、查询参数或锚点"
    }
    return null
}

private fun parseHttpUri(value: String): URI? = runCatching { URI(value.trim()) }.getOrNull()
    ?.takeIf { it.isAbsolute && (it.scheme.equals("http", true) || it.scheme.equals("https", true)) }
    ?.takeIf { !it.host.isNullOrBlank() }
    ?.takeIf { it.port == -1 || it.port in 1..65535 }

private fun looksLikeBareIpv6Address(value: String): Boolean =
    !value.startsWith("[") && value.count { it == ':' } >= 2 &&
        runCatching { InetAddress.getByName(value.substringBefore('%')) is Inet6Address }.getOrDefault(false)

private fun effectivePort(uri: URI): Int = when {
    uri.port >= 0 -> uri.port
    uri.scheme.equals("http", ignoreCase = true) -> 80
    else -> 443
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
