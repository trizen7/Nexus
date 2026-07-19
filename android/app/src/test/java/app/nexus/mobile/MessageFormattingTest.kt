package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class MessageFormattingTest {
    @Test
    fun `markdown parser recognizes headings lists code and tables`() {
        val blocks = parseMarkdownBlocks(
            """# 标题

- 第一项
- 第二项

```kotlin
val answer = 42
```

| 名称 | 状态 |
| --- | --- |
| 网关 | 正常 |"""
        )

        assertTrue(blocks.any { it is MarkdownBlock.Heading && it.text == "标题" })
        assertTrue(blocks.any { it is MarkdownBlock.BulletList && it.items.size == 2 })
        assertTrue(blocks.any { it is MarkdownBlock.Code && it.language == "kotlin" })
        assertTrue(blocks.any { it is MarkdownBlock.Table && it.rows.single()[1] == "正常" })
    }

    @Test
    fun `session search is case insensitive and keeps matching titles`() {
        val sessions = listOf(
            app.nexus.mobile.network.HermesSession("1", "Android UI", "api_server", 2, 2.0),
            app.nexus.mobile.network.HermesSession("2", "打印助手", "desktop", 4, 1.0)
        )

        assertEquals(listOf("1"), filterSessions(sessions, "android").map { it.id })
        assertEquals(sessions, filterSessions(sessions, " "))
    }
}
