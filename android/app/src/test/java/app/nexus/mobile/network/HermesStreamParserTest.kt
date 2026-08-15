package app.nexus.mobile.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
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
            parser.accept("data: {\"delta\":\"你\"}"),
            parser.accept(""),
            parser.accept("event: assistant.delta"),
            parser.accept("data: {\"delta\":\"好\"}"),
            parser.accept(""),
            parser.accept("event: run.completed"),
            parser.accept("data: {\"completed\":true}"),
            parser.accept("")
        ).filterNotNull()

        assertEquals(
            listOf(
                HermesStreamEvent.RunStarted(),
                HermesStreamEvent.TextDelta("你"),
                HermesStreamEvent.TextDelta("好"),
                HermesStreamEvent.Completed()
            ),
            events
        )
    }

    @Test
    fun `parses requested and actual runtime metadata`() {
        val parser = HermesStreamParser()

        parser.accept("event: run.completed")
        parser.accept(
            "data: {\"completed\":true,\"runtime\":{" +
                "\"provider\":\"openai-codex\",\"model\":\"gpt-5.6-sol\"," +
                "\"route_source\":\"explicit_provider_request\",\"model_lock\":\"confirmed\"," +
                "\"requested\":{\"provider\":\"openai-codex\",\"model\":\"gpt-5.6-sol\"}}}"
        )

        assertEquals(
            HermesStreamEvent.Completed(
                HermesRuntime(
                    provider = "openai-codex",
                    model = "gpt-5.6-sol",
                    routeSource = "explicit_provider_request",
                    modelLock = "confirmed",
                    requestedProvider = "openai-codex",
                    requestedModel = "gpt-5.6-sol",
                )
            ),
            parser.accept("")
        )
    }

    @Test
    fun `confirmed custom alias matches the requested user configured provider`() {
        val runtime = HermesRuntime(
            provider = "custom",
            model = "gpt-5.6-terra",
            routeSource = "session_model_lock",
            modelLock = "confirmed",
            requestedProvider = "alight",
            requestedModel = "gpt-5.6-terra",
        )

        assertTrue(runtime.matches("alight", "gpt-5.6-terra"))
        assertFalse(runtime.matches("cpa", "gpt-5.6-terra"))
        assertFalse(runtime.matches("alight", "gpt-5.6-sol"))
    }

    @Test
    fun `confirmed custom alias without echoed request still matches the locked model`() {
        val runtime = HermesRuntime(
            provider = "custom",
            model = "gpt-5.6-sol",
            routeSource = "session_model_lock",
            modelLock = "confirmed",
        )

        assertTrue(runtime.matches("alight", "gpt-5.6-sol"))
        assertFalse(runtime.matches("alight", "gpt-5.6-terra"))
    }

    @Test
    fun `confirmed custom alias rejects an explicitly different requested provider`() {
        val runtime = HermesRuntime(
            provider = "custom",
            model = "gpt-5.6-sol",
            routeSource = "session_model_lock",
            modelLock = "confirmed",
            requestedProvider = "cpa",
            requestedModel = "gpt-5.6-sol",
        )

        assertFalse(runtime.matches("alight", "gpt-5.6-sol"))
    }

    @Test
    fun `unconfirmed custom runtime does not match a named provider`() {
        val runtime = HermesRuntime(
            provider = "custom",
            model = "gpt-5.6-terra",
            routeSource = "session_model_lock",
            modelLock = "accepted",
            requestedProvider = "alight",
            requestedModel = "gpt-5.6-terra",
        )

        assertFalse(runtime.matches("alight", "gpt-5.6-terra"))
    }

    @Test
    fun `runtime with a blank provider or model never confirms an explicit selection`() {
        assertFalse(
            HermesRuntime(provider = "", model = "gpt-5.6-sol")
                .matches("openai-codex", "gpt-5.6-sol")
        )
        assertFalse(
            HermesRuntime(provider = "openai-codex", model = "")
                .matches("openai-codex", "gpt-5.6-sol")
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

    @Test
    fun `provider setup CLI error is translated for mobile users`() {
        val parser = HermesStreamParser()

        parser.accept("event: error")
        parser.accept(
            "data: {\"message\":\"No LLM provider configured. Run 'hermes model' to select a provider, " +
                "or run 'hermes setup' for first-time configuration.\"}"
        )

        assertEquals(
            HermesStreamEvent.Error("Hermes 尚未配置可用的调用模型，请先在 Hermes 中完成模型配置"),
            parser.accept(""),
        )
    }
}
