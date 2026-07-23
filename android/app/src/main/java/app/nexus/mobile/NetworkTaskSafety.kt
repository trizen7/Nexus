package app.nexus.mobile

import java.net.Inet6Address
import java.net.InetAddress
import java.net.URI

private const val PRODUCT_TEST_HTTPS_PORT = 18788
private val LOCAL_HOST_LABEL = Regex("^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")

fun shouldAttachBearerToken(serverUrl: String, resourceUrl: String): Boolean {
    val server = parseHttpsUri(normalizeServerUrl(serverUrl))
        ?.takeIf { it.userInfo == null && it.rawQuery == null && it.rawFragment == null }
        ?: return false
    val candidate = runCatching { URI(resourceUrl.trim()) }.getOrNull() ?: return false
    val resource = (if (candidate.isAbsolute) parseHttpsUri(resourceUrl) else server.resolve(candidate)?.let {
        parseHttpsUri(it.toString())
    }) ?: return false
    return server.scheme.equals(resource.scheme, ignoreCase = true) &&
        server.host.equals(resource.host, ignoreCase = true) &&
        effectivePort(server) == effectivePort(resource)
}

fun bearerTokenFor(serverUrl: String, resourceUrl: String, token: String): String? =
    token.takeIf(String::isNotBlank)?.takeIf { shouldAttachBearerToken(serverUrl, resourceUrl) }

fun normalizeServerUrl(serverUrl: String): String {
    val trimmed = serverUrl.trim()
    if (trimmed.isEmpty()) return ""
    val withScheme = when {
        trimmed.startsWith("http://", ignoreCase = true) ||
            trimmed.startsWith("https://", ignoreCase = true) ||
            "://" in trimmed -> trimmed
        looksLikeBareIpv6Address(trimmed.trimEnd('/')) -> "https://[${trimmed.trimEnd('/')}]"
        else -> "https://$trimmed"
    }.trimEnd('/')
    val uri = runCatching { URI(withScheme) }.getOrNull() ?: return withScheme
    if (!uri.scheme.equals("https", ignoreCase = true) || uri.port >= 0 || !isLocalGatewayHost(uri.host)) {
        return withScheme
    }
    return runCatching {
        URI(
            uri.scheme,
            uri.userInfo,
            uri.host,
            PRODUCT_TEST_HTTPS_PORT,
            uri.path,
            uri.query,
            uri.fragment,
        ).toString().trimEnd('/')
    }.getOrDefault(withScheme)
}

fun migrateStoredServerUrl(serverUrl: String): String {
    val trimmed = serverUrl.trim().trimEnd('/')
    val uri = runCatching { URI(trimmed) }.getOrNull() ?: return serverUrl
    if (!uri.scheme.equals("http", ignoreCase = true) || uri.port != 18787 ||
        !isLocalGatewayHost(uri.host) || uri.userInfo != null || uri.rawQuery != null || uri.rawFragment != null
    ) {
        return serverUrl
    }
    return runCatching {
        URI("https", null, uri.host, PRODUCT_TEST_HTTPS_PORT, uri.path, null, null).toString().trimEnd('/')
    }.getOrDefault(serverUrl)
}

fun serverUrlValidationError(serverUrl: String): String? {
    val normalized = normalizeServerUrl(serverUrl)
    if (normalized.isBlank()) return "请填写服务器地址"
    val candidate = runCatching { URI(normalized) }.getOrNull()
        ?: return "服务器地址格式不正确，请填写例如 https://10.0.0.123:18788"
    if (candidate.scheme.equals("http", ignoreCase = true)) {
        return "Nexus App 仅允许 HTTPS，请填写例如 https://10.0.0.123:18788"
    }
    val parsed = parseHttpsUri(normalized)
        ?: return "服务器地址格式不正确，请填写例如 https://10.0.0.123:18788"
    if (parsed.userInfo != null || parsed.rawQuery != null || parsed.rawFragment != null) {
        return "服务器地址不能包含账号、查询参数或锚点"
    }
    return null
}

private fun parseHttpsUri(value: String): URI? = runCatching { URI(value.trim()) }.getOrNull()
    ?.takeIf { it.isAbsolute && it.scheme.equals("https", true) }
    ?.takeIf { !it.host.isNullOrBlank() }
    ?.takeIf { it.port == -1 || it.port in 1..65535 }

private fun looksLikeBareIpv6Address(value: String): Boolean =
    !value.startsWith("[") && value.count { it == ':' } >= 2 &&
        runCatching { InetAddress.getByName(value.substringBefore('%')) is Inet6Address }.getOrDefault(false)

private fun isLocalGatewayHost(host: String?): Boolean {
    if (host.isNullOrBlank()) return false
    val normalized = host.trim().removePrefix("[").removeSuffix("]")
    val withoutScope = normalized.substringBefore('%')
    if (withoutScope.equals("localhost", ignoreCase = true) ||
        withoutScope.equals("localhost.localdomain", ignoreCase = true) ||
        withoutScope.endsWith(".local", ignoreCase = true) ||
        (!withoutScope.contains('.') && !withoutScope.contains(':') && LOCAL_HOST_LABEL.matches(withoutScope))
    ) {
        return true
    }
    if (withoutScope.contains(':')) {
        val address = runCatching { InetAddress.getByName(withoutScope) }.getOrNull() as? Inet6Address
            ?: return false
        val firstByte = address.address.firstOrNull()?.toInt()?.and(0xFF) ?: return false
        return address.isLoopbackAddress || address.isLinkLocalAddress || address.isSiteLocalAddress ||
            firstByte and 0xFE == 0xFC
    }
    val parts = withoutScope.split('.')
    if (parts.size != 4) return false
    val octets = parts.map { it.toIntOrNull() ?: return false }
    if (octets.any { it !in 0..255 }) return false
    return octets[0] == 10 ||
        octets[0] == 127 ||
        (octets[0] == 100 && octets[1] in 64..127) ||
        (octets[0] == 192 && octets[1] == 168) ||
        (octets[0] == 172 && octets[1] in 16..31) ||
        (octets[0] == 169 && octets[1] == 254)
}

private fun effectivePort(uri: URI): Int = when {
    uri.port >= 0 -> uri.port
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
