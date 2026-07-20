package app.nexus.mobile.network

import org.junit.Assert.assertEquals
import org.junit.Test

class HermesStreamParserTest {
    @Test
    fun `parses standard JSON escapes Unicode emoji and backslashes`() {
        val parser = HermesStreamParser()

        parser.accept("event: assistant.delta")
        parser.accept("data: {\"delta\":\"中文 \\uD83D\\uDE00 C:\\\\tmp\\\\file \\b \\f\"}")
        val event = parser.accept("")

        assertEquals(HermesStreamEvent.TextDelta("中文 😀 C:\\tmp\\file \b \u000c"), event)
    }

    @Test
    fun `joins multiple SSE data lines before parsing JSON`() {
        val parser = HermesStreamParser()

        parser.accept("event: assistant.delta")
        parser.accept("data: {\"delta\":")
        parser.accept("data: \"多行数据\"}")

        assertEquals(HermesStreamEvent.TextDelta("多行数据"), parser.accept(""))
    }

    @Test
    fun `parses session stream text and lifecycle events`() {
        val parser = HermesStreamParser()

        val events = listOf(
            parser.accept("event: run.started"),
            parser.accept("data: {\"session_id\":\"mobile\",\"run_id\":\"run-1\"}"),
            parser.accept(""),
            parser.accept("event: assistant.delta"),
            parser.accept("data: {\"delta\":\"星\"}"),
            parser.accept(""),
            parser.accept("event: assistant.delta"),
            parser.accept("data: {\"delta\":\"禾\"}"),
            parser.accept(""),
            parser.accept("event: run.completed"),
            parser.accept("data: {\"completed\":true}"),
            parser.accept("")
        ).filterNotNull()

        assertEquals(
            listOf(
                HermesStreamEvent.RunStarted,
                HermesStreamEvent.TextDelta("星"),
                HermesStreamEvent.TextDelta("禾"),
                HermesStreamEvent.Completed
            ),
            events
        )
    }

    @Test
    fun `transport done is not interpreted as answer completed`() {
        val parser = HermesStreamParser()

        val event = listOf(
            parser.accept("event: done"),
            parser.accept("data: {}"),
            parser.accept("")
        ).filterNotNull().single()

        assertEquals(HermesStreamEvent.StreamEnded, event)
    }
}
