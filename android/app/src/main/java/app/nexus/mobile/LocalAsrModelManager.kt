package app.nexus.mobile

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import org.apache.commons.compress.compressors.bzip2.BZip2CompressorInputStream
import java.io.BufferedInputStream
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.security.MessageDigest

internal data class LocalAsrModelSpec(
    val id: String,
    val displayName: String,
    val sizeLabel: String,
    val sha256: String,
    val modelFiles: List<String>,
) {
    val archiveName: String = "$id.tar.bz2"
    val archiveUrl: String =
        "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/$archiveName"
    val requiredFiles: Set<String> = (modelFiles + TOKENS_FILE).toSet()

    companion object {
        const val TOKENS_FILE = "tokens.txt"
    }
}

internal data class LocalAsrModelFiles(
    val spec: LocalAsrModelSpec,
    val directory: File,
) {
    val tokens: File = File(directory, LocalAsrModelSpec.TOKENS_FILE)
    fun modelFile(name: String): File = File(directory, name)
}

internal val LOCAL_ASR_MODEL =
    LocalAsrModelSpec(
        id = "sherpa-onnx-x-asr-480ms-streaming-zipformer-transducer-zh-en-punct-int8-2026-06-05",
        displayName = "X-ASR 中英标点",
        sizeLabel = "约 128 MB",
        sha256 = "fa5f63d618e5a01526e275a358bb7772e403f84808a4769fba52cffd8160bf74",
        modelFiles = listOf("encoder.int8.onnx", "decoder.onnx", "joiner.int8.onnx"),
    )

internal val LOCAL_ASR_MODELS = listOf(LOCAL_ASR_MODEL)

internal object LocalAsrModelManager {
    private const val MIN_TOTAL_MODEL_BYTES = 1_000_000L
    private const val PREFERENCES = "nexus_local_asr"
    private const val KEY_SELECTED_MODEL = "selected_model"
    private const val KEY_SELECTED_STREAMING_MODEL = "selected_streaming_model"
    private const val KEY_SELECTED_OFFLINE_MODEL = "selected_offline_model"
    private const val RETIRED_PUNCTUATION_MODEL_ID =
        "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8"
    private const val RETIRED_PUNCTUATION_PREFERENCES = "nexus_local_punctuation"
    private val RETIRED_MODEL_IDS = listOf(
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2025-09-09",
        "sherpa-onnx-paraformer-zh-int8-2025-10-07",
        "sherpa-onnx-streaming-zipformer-zh-int8-2025-06-30",
        "sherpa-onnx-streaming-zipformer-ctc-zh-int8-2025-06-30",
        "sherpa-onnx-streaming-zipformer-small-ctc-zh-int8-2025-04-01",
        RETIRED_PUNCTUATION_MODEL_ID,
    )

    suspend fun cleanupRetiredModels(context: Context) = withContext(Dispatchers.IO) {
        val applicationContext = context.applicationContext
        val root = modelRoot(applicationContext).canonicalFile
        RETIRED_MODEL_IDS.flatMap { id -> listOf(id, "$id.installing") }
            .map { name -> File(root, name).canonicalFile }
            .forEach { target ->
                check(target.parentFile == root) { "废弃模型目录无效" }
                check(!target.exists() || target.deleteRecursively()) { "无法删除废弃语音模型" }
            }
        RETIRED_MODEL_IDS.forEach { id -> DownloadHelper.cancel(applicationContext, "local-asr-$id") }
        DownloadHelper.cancel(applicationContext, "local-punctuation")
        applicationContext.deleteSharedPreferences(RETIRED_PUNCTUATION_PREFERENCES)
        preferences(applicationContext).let { stored ->
            stored.edit().apply {
                remove(KEY_SELECTED_STREAMING_MODEL)
                remove(KEY_SELECTED_OFFLINE_MODEL)
                if (stored.getString(KEY_SELECTED_MODEL, null) != LOCAL_ASR_MODEL.id) {
                    remove(KEY_SELECTED_MODEL)
                }
            }.apply()
        }
    }

    fun installedFiles(context: Context, spec: LocalAsrModelSpec): LocalAsrModelFiles? {
        val directory = modelDirectory(context, spec)
        val files = LocalAsrModelFiles(spec = spec, directory = directory)
        val modelsComplete = spec.modelFiles.all { files.modelFile(it).isFile }
        val totalModelBytes = spec.modelFiles.sumOf { files.modelFile(it).takeIf(File::isFile)?.length() ?: 0L }
        return if (
            modelsComplete &&
            totalModelBytes >= MIN_TOTAL_MODEL_BYTES &&
            files.tokens.isFile &&
            files.tokens.length() > 0L
        ) {
            files
        } else {
            null
        }
    }

    fun selectedModelFiles(context: Context): LocalAsrModelFiles? {
        return installedFiles(context, LOCAL_ASR_MODEL)
    }

    fun selectedModel(context: Context): LocalAsrModelSpec? = selectedModelFiles(context)?.spec

    fun installedModelIds(context: Context): Set<String> = LOCAL_ASR_MODELS
        .filter { installedFiles(context, it) != null }
        .mapTo(mutableSetOf(), LocalAsrModelSpec::id)

    fun isInstalled(context: Context): Boolean = selectedModelFiles(context) != null

    fun select(context: Context, spec: LocalAsrModelSpec): Boolean {
        if (installedFiles(context, spec) == null) return false
        preferences(context).edit()
            .putString(KEY_SELECTED_MODEL, spec.id)
            .apply()
        return true
    }

    suspend fun install(context: Context, spec: LocalAsrModelSpec, onProgress: (Int) -> Unit) {
        val applicationContext = context.applicationContext
        val archive = DownloadHelper.download(
            context = applicationContext,
            key = "local-asr-${spec.id}",
            url = spec.archiveUrl,
            token = null,
            serverUrl = "",
            fileName = spec.archiveName,
            onProgress = onProgress,
        )
        try {
            withContext(Dispatchers.IO) {
                check(sha256(archive).equals(spec.sha256, ignoreCase = true)) {
                    "模型文件校验失败，请重新下载"
                }
                installArchive(applicationContext, spec, archive)
                select(applicationContext, spec)
            }
        } finally {
            archive.delete()
        }
    }

    fun remove(context: Context, spec: LocalAsrModelSpec): Boolean {
        val root = modelRoot(context)
        val target = modelDirectory(context, spec)
        check(target.parentFile?.canonicalFile == root.canonicalFile)
        val removed = !target.exists() || target.deleteRecursively()
        if (removed && preferences(context).getString(KEY_SELECTED_MODEL, null) == spec.id) {
            preferences(context).edit().remove(KEY_SELECTED_MODEL).apply()
        }
        if (removed) SpeechEnginePreferences.resetXAsrSelections(context)
        return removed
    }

    private fun installArchive(context: Context, spec: LocalAsrModelSpec, archive: File) {
        val root = modelRoot(context).apply { mkdirs() }.canonicalFile
        val target = modelDirectory(context, spec).canonicalFile
        val staging = File(root, "${spec.id}.installing").canonicalFile
        check(target.parentFile == root && staging.parentFile == root) { "本地模型目录无效" }

        staging.deleteRecursively()
        check(staging.mkdirs()) { "无法创建模型安装目录" }
        try {
            val extracted = mutableSetOf<String>()
            TarArchiveInputStream(
                BZip2CompressorInputStream(BufferedInputStream(FileInputStream(archive))),
            ).use { tar ->
                while (true) {
                    val entry = tar.nextEntry ?: break
                    if (!entry.isFile) continue
                    val fileName = entry.name.substringAfterLast('/')
                    if (fileName !in spec.requiredFiles || !extracted.add(fileName)) continue
                    FileOutputStream(File(staging, fileName)).use { output -> tar.copyTo(output) }
                }
            }
            check(extracted == spec.requiredFiles) { "模型压缩包缺少必要文件" }
            val totalModelBytes = spec.modelFiles.sumOf { File(staging, it).length() }
            check(totalModelBytes >= MIN_TOTAL_MODEL_BYTES && File(staging, LocalAsrModelSpec.TOKENS_FILE).length() > 0L) {
                "模型文件不完整"
            }

            if (target.exists()) check(target.deleteRecursively()) { "无法替换旧模型" }
            check(staging.renameTo(target)) { "无法完成模型安装" }
        } catch (error: Exception) {
            staging.deleteRecursively()
            throw error
        }
    }

    private fun preferences(context: Context) =
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    private fun modelRoot(context: Context): File = File(context.filesDir, "speech-models")

    private fun modelDirectory(context: Context, spec: LocalAsrModelSpec): File = File(modelRoot(context), spec.id)

    private fun sha256(file: File): String {
        val digest = MessageDigest.getInstance("SHA-256")
        file.inputStream().buffered().use { input ->
            val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
