package app.nexus.mobile.network

import com.google.gson.JsonParser

sealed interface HermesStreamEvent {
    data object RunStarted : HermesStreamEvent
    data class TextDelta(val text: String) : HermesStreamEvent
    data class ToolStarted(val name: String?) : HermesStreamEvent
    data class ToolCompleted(val name: String?) : HermesStreamEvent
    data object Completed : HermesStreamEvent
    data object StreamEnded : HermesStreamEvent
    data class Error(val message: String) : HermesStreamEvent
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
            "run.started" -> HermesStreamEvent.RunStarted
            "assistant.delta" -> jsonString(data, "delta")?.let(HermesStreamEvent::TextDelta)
            "tool.started" -> HermesStreamEvent.ToolStarted(jsonString(data, "tool_name"))
            "tool.completed" -> HermesStreamEvent.ToolCompleted(jsonString(data, "tool_name"))
            "run.completed" -> HermesStreamEvent.Completed
            "done" -> HermesStreamEvent.StreamEnded
            "error" -> HermesStreamEvent.Error(jsonString(data, "message") ?: "未知错误")
            else -> null
        }
    }

    private fun jsonString(json: String, key: String): String? = runCatching {
        JsonParser.parseString(json).asJsonObject.get(key)?.takeUnless { it.isJsonNull }?.asString
    }.getOrNull()
}
