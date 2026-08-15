package app.nexus.mobile.network

import com.google.gson.JsonParser

data class HermesRuntime(
    val provider: String,
    val model: String,
    val routeSource: String? = null,
    val modelLock: String? = null,
    val requestedProvider: String? = null,
    val requestedModel: String? = null,
) {
    fun matches(expectedProvider: String, expectedModel: String): Boolean {
        if (provider.isBlank() || model.isBlank()) return false
        val expectedProviderId = expectedProvider.removePrefix("custom:")
        val providerMatches = provider == expectedProvider ||
            provider.removePrefix("custom:") == expectedProviderId ||
            (
                provider == "custom" &&
                    routeSource == "session_model_lock" &&
                    modelLock == "confirmed" &&
                    (
                        requestedProvider == null ||
                            requestedProvider.removePrefix("custom:") == expectedProviderId
                        ) &&
                    (requestedModel == null || requestedModel == expectedModel)
                )
        val modelMatches = model == expectedModel
        return providerMatches && modelMatches
    }
}

sealed interface HermesStreamEvent {
    data class RunStarted(val runtime: HermesRuntime? = null) : HermesStreamEvent
    data class TextDelta(val text: String) : HermesStreamEvent
    data class ToolStarted(val name: String?) : HermesStreamEvent
    data class ToolCompleted(val name: String?) : HermesStreamEvent
    data class AssistantCompleted(val runtime: HermesRuntime? = null) : HermesStreamEvent
    data class Completed(val runtime: HermesRuntime? = null) : HermesStreamEvent
    data object StreamEnded : HermesStreamEvent
    data class Error(val message: String) : HermesStreamEvent
}

internal fun friendlyHermesStreamError(message: String): String =
    if (message.contains("No LLM provider configured", ignoreCase = true)) {
        "Hermes 尚未配置可用的调用模型，请先在 Hermes 中完成模型配置"
    } else {
        message
    }

class HermesStreamParser {
    private var eventName: String? = null
    private val dataLines = mutableListOf<String>()

    fun accept(line: String): HermesStreamEvent? {
        when {
            line.startsWith("event:") -> eventName = line.substringAfter("event:").trim()
            line.startsWith("data:") -> dataLines += line.substringAfter("data:").removePrefix(" ")
            line.isBlank() -> return flush()
        }
        return null
    }

    fun flush(): HermesStreamEvent? {
        val name = eventName
        val data = dataLines.joinToString("\n")
        eventName = null
        dataLines.clear()

        return when (name) {
            "run.started" -> HermesStreamEvent.RunStarted(jsonRuntime(data))
            "assistant.delta" -> jsonString(data, "delta")?.let(HermesStreamEvent::TextDelta)
            "tool.started" -> HermesStreamEvent.ToolStarted(jsonString(data, "tool_name"))
            "tool.completed" -> HermesStreamEvent.ToolCompleted(jsonString(data, "tool_name"))
            "assistant.completed" -> HermesStreamEvent.AssistantCompleted(jsonRuntime(data))
            "run.completed" -> HermesStreamEvent.Completed(jsonRuntime(data))
            "done" -> HermesStreamEvent.StreamEnded
            "error" -> HermesStreamEvent.Error(
                friendlyHermesStreamError(jsonString(data, "message") ?: "未知错误")
            )
            else -> null
        }
    }

    private fun jsonString(json: String, key: String): String? = runCatching {
        JsonParser.parseString(json).asJsonObject.get(key)?.takeUnless { it.isJsonNull }?.asString
    }.getOrNull()

    private fun jsonRuntime(json: String): HermesRuntime? = runCatching {
        val runtime = JsonParser.parseString(json).asJsonObject
            .get("runtime")
            ?.takeIf { it.isJsonObject }
            ?.asJsonObject
            ?: return@runCatching null
        val requested = runtime.get("requested")
            ?.takeIf { it.isJsonObject }
            ?.asJsonObject
        HermesRuntime(
            provider = runtime.stringValue("provider"),
            model = runtime.stringValue("model"),
            routeSource = runtime.stringValue("route_source").ifBlank { null },
            modelLock = runtime.stringValue("model_lock").ifBlank { null },
            requestedProvider = requested?.stringValue("provider")?.ifBlank { null },
            requestedModel = requested?.stringValue("model")?.ifBlank { null },
        ).takeIf { it.provider.isNotBlank() || it.model.isNotBlank() }
    }.getOrNull()

    private fun com.google.gson.JsonObject.stringValue(key: String): String =
        get(key)?.takeUnless { it.isJsonNull }?.let { runCatching { it.asString.trim() }.getOrNull() }.orEmpty()
}
