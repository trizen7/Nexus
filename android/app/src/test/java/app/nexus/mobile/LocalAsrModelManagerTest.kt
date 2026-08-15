package app.nexus.mobile

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class LocalAsrModelManagerTest {
    @Test
    fun `local asr model catalog has unique verified archives`() {
        assertEquals(1, LOCAL_ASR_MODELS.size)
        assertEquals(LOCAL_ASR_MODELS.size, LOCAL_ASR_MODELS.map { it.id }.distinct().size)
        LOCAL_ASR_MODELS.forEach { model ->
            assertTrue(model.sha256.matches(Regex("[0-9a-f]{64}")))
            assertTrue(model.archiveName.endsWith(".tar.bz2"))
            assertTrue(model.archiveUrl.endsWith(model.archiveName))
            assertTrue(LocalAsrModelSpec.TOKENS_FILE in model.requiredFiles)
            assertTrue(model.modelFiles.isNotEmpty())
        }
    }

    @Test
    fun `catalog contains only benchmark winner`() {
        assertEquals(
            "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05",
            LOCAL_ASR_MODEL.id,
        )
        assertEquals("fa5f63d618e5a01526e275a358bb7772e403f84808a4769fba52cffd8160bf74", LOCAL_ASR_MODEL.sha256)
    }
}
