package app.nexus.mobile

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertThrows
import org.junit.Test
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import kotlin.io.path.createTempDirectory

class FileProcessorTest {
    @Test
    fun `bounded copy accepts exactly the maximum number of bytes`() {
        val bytes = byteArrayOf(1, 2, 3, 4)
        val output = ByteArrayOutputStream()

        assertEquals(4L, copyWithByteLimit(ByteArrayInputStream(bytes), output, 4L))
        assertArrayEquals(bytes, output.toByteArray())
    }

    @Test
    fun `bounded copy aborts before writing bytes beyond the maximum`() {
        val output = ByteArrayOutputStream()

        assertThrows(IllegalArgumentException::class.java) {
            copyWithByteLimit(ByteArrayInputStream(byteArrayOf(1, 2, 3, 4, 5)), output, 4L)
        }
        assertEquals(0, output.size())
    }

    @Test
    fun `failed private copy deletes its temporary file`() {
        val directory = createTempDirectory("nexus-file-copy-").toFile()
        val target = File(directory, "oversized.bin")
        try {
            assertThrows(IllegalArgumentException::class.java) {
                copyToFileWithByteLimit(ByteArrayInputStream(ByteArray(5)), target, 4L)
            }
            assertFalse(target.exists())
        } finally {
            directory.deleteRecursively()
        }
    }
}