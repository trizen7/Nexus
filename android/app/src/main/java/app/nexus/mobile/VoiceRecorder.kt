package app.nexus.mobile

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import java.io.File

class VoiceRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    private var output: File? = null
    private var startedAt: Long = 0

    fun start() {
        stop(delete = true)
        val file = File(context.cacheDir, "voice-${System.currentTimeMillis()}.m4a")
        val mediaRecorder = createMediaRecorder().apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setAudioEncodingBitRate(64_000)
            setAudioSamplingRate(44_100)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        output = file
        recorder = mediaRecorder
        startedAt = System.currentTimeMillis()
    }

    fun stop(delete: Boolean = false): RecordedVoice? {
        val active = recorder
        recorder = null
        if (active != null) {
            runCatching { active.stop() }
            active.reset()
            active.release()
        }
        val file = output.also { output = null } ?: return null
        val duration = (System.currentTimeMillis() - startedAt).coerceAtLeast(0)
        if (delete || !file.isFile || file.length() == 0L) {
            file.delete()
            return null
        }
        return RecordedVoice(file, duration)
    }

    fun cancel() {
        stop(delete = true)
    }

    private fun createMediaRecorder(): MediaRecorder =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) MediaRecorder(context) else legacyMediaRecorder()

    @Suppress("DEPRECATION")
    private fun legacyMediaRecorder(): MediaRecorder = MediaRecorder()
}

data class RecordedVoice(val file: File, val durationMillis: Long)
