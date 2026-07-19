package app.nexus.mobile.network

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
            line.startsWith("data:") -> dataLines += line.substringAfter("data:").trim()
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
            "assistant.delta" -> extractJsonString(data, "delta")?.let(HermesStreamEvent::TextDelta)
            "tool.started" -> HermesStreamEvent.ToolStarted(extractJsonString(data, "tool_name"))
            "tool.completed" -> HermesStreamEvent.ToolCompleted(extractJsonString(data, "tool_name"))
            "run.completed" -> HermesStreamEvent.Completed
            "done" -> HermesStreamEvent.StreamEnded
            "error" -> HermesStreamEvent.Error(extractJsonString(data, "message") ?: "未知错误")
            else -> null
        }
    }

    private fun extractJsonString(json: String, key: String): String? {
        val match = Regex("\\\"${Regex.escape(key)}\\\"\\s*:\\s*\\\"((?:\\\\.|[^\\\"])*)\\\"").find(json)
            ?: return null
        return match.groupValues[1]
            .replace("\\n", "\n")
            .replace("\\r", "\r")
            .replace("\\t", "\t")
            .replace("\\\"", "\"")
            .replace("\\\\", "\\")
    }
}
