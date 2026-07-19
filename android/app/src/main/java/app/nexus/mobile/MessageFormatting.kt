package app.nexus.mobile

import app.nexus.mobile.network.HermesSession

sealed interface MarkdownBlock {
    data class Heading(val level: Int, val text: String) : MarkdownBlock
    data class Paragraph(val text: String) : MarkdownBlock
    data class BulletList(val items: List<String>) : MarkdownBlock
    data class Code(val language: String, val content: String) : MarkdownBlock
    data class Table(val headers: List<String>, val rows: List<List<String>>) : MarkdownBlock
}

fun parseMarkdownBlocks(source: String): List<MarkdownBlock> {
    val lines = source.replace("\r\n", "\n").lines()
    val blocks = mutableListOf<MarkdownBlock>()
    var index = 0
    while (index < lines.size) {
        val line = lines[index]
        when {
            line.isBlank() -> index += 1
            line.startsWith("```") -> {
                val language = line.removePrefix("```").trim()
                val code = mutableListOf<String>()
                index += 1
                while (index < lines.size && !lines[index].startsWith("```")) code += lines[index++]
                if (index < lines.size) index += 1
                blocks += MarkdownBlock.Code(language, code.joinToString("\n"))
            }
            line.startsWith("#") -> {
                val level = line.takeWhile { it == '#' }.length.coerceIn(1, 3)
                blocks += MarkdownBlock.Heading(level, line.drop(level).trim())
                index += 1
            }
            line.startsWith("- ") -> {
                val items = mutableListOf<String>()
                while (index < lines.size && lines[index].startsWith("- ")) {
                    items += lines[index++].removePrefix("- ").trim()
                }
                blocks += MarkdownBlock.BulletList(items)
            }
            isTableHeader(lines, index) -> {
                val headers = tableCells(lines[index])
                index += 2
                val rows = mutableListOf<List<String>>()
                while (index < lines.size && lines[index].trim().startsWith('|')) {
                    rows += tableCells(lines[index++])
                }
                blocks += MarkdownBlock.Table(headers, rows)
            }
            else -> {
                val paragraph = mutableListOf<String>()
                while (index < lines.size && lines[index].isNotBlank() &&
                    !lines[index].startsWith("#") && !lines[index].startsWith("- ") &&
                    !lines[index].startsWith("```") && !isTableHeader(lines, index)
                ) {
                    paragraph += lines[index++]
                }
                blocks += MarkdownBlock.Paragraph(paragraph.joinToString("\n"))
            }
        }
    }
    return blocks
}

fun filterSessions(sessions: List<HermesSession>, query: String): List<HermesSession> {
    val normalized = query.trim()
    return if (normalized.isEmpty()) sessions else sessions.filter {
        it.displayTitle.contains(normalized, ignoreCase = true) ||
            it.channel.label.contains(normalized, ignoreCase = true)
    }
}

private fun isTableHeader(lines: List<String>, index: Int): Boolean =
    index + 1 < lines.size && lines[index].trim().startsWith('|') &&
        lines[index + 1].trim().startsWith('|') &&
        tableCells(lines[index + 1]).all { it.matches(Regex(":?-{3,}:?")) }

private fun tableCells(line: String): List<String> =
    line.trim().removePrefix("|").removeSuffix("|").split('|').map(String::trim)
