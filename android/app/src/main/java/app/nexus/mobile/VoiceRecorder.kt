package app.nexus.mobile

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong
import kotlin.math.abs

class VoiceRecorder(
    private val context: Context,
    private val voiceCommunication: Boolean = false,
) {
    private var recorder: AudioRecord? = null
    private var output: File? = null
    private var writerThread: Thread? = null
    private var echoCanceler: AcousticEchoCanceler? = null
    private val recording = AtomicBoolean(false)
    private val latestAmplitude = AtomicInteger(0)
    private val samplesWritten = AtomicLong(0)
    @Volatile private var recordingFailure: Throwable? = null

    @SuppressLint("MissingPermission")
    fun start() {
        stop(delete = true)
        val minimumBuffer = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
        )
        check(minimumBuffer > 0) { "当前设备不支持语音录音" }
        val bufferSize = maxOf(minimumBuffer * 2, SAMPLE_RATE / 2)
        val active = AudioRecord(
            if (voiceCommunication) MediaRecorder.AudioSource.VOICE_COMMUNICATION else MediaRecorder.AudioSource.VOICE_RECOGNITION,
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT,
            bufferSize,
        )
        check(active.state == AudioRecord.STATE_INITIALIZED) {
            active.release()
            "麦克风初始化失败"
        }
        echoCanceler = if (voiceCommunication && AcousticEchoCanceler.isAvailable()) {
            runCatching {
                AcousticEchoCanceler.create(active.audioSessionId)?.also { it.enabled = true }
                    ?.takeIf { it.enabled }
            }.getOrNull()
        } else null

        val file = File(context.cacheDir, "voice-${System.currentTimeMillis()}.wav")
        val stream = BufferedOutputStream(FileOutputStream(file)).apply {
            write(ByteArray(WAV_HEADER_SIZE))
        }
        try {
            active.startRecording()
        } catch (error: Exception) {
            stream.close()
            active.release()
            file.delete()
            throw error
        }

        recorder = active
        output = file
        recordingFailure = null
        samplesWritten.set(0)
        latestAmplitude.set(0)
        recording.set(true)
        writerThread = Thread(
            { capturePcm(active, stream, PCM_FRAME_SAMPLES) },
            "nexus-voice-recorder",
        ).apply {
            isDaemon = true
            start()
        }
    }

    fun stop(delete: Boolean = false): RecordedVoice? {
        val active = recorder.also { recorder = null }
        val thread = writerThread.also { writerThread = null }
        recording.set(false)
        if (active != null) runCatching { active.stop() }
        if (thread != null) {
            runCatching { thread.join(2_000) }
            if (thread.isAlive) {
                thread.interrupt()
                runCatching { thread.join(500) }
            }
        }
        if (active != null) runCatching { active.release() }
        runCatching { echoCanceler?.release() }
        echoCanceler = null

        val file = output.also { output = null } ?: return null
        val sampleCount = samplesWritten.getAndSet(0)
        val valid = recordingFailure == null && thread?.isAlive != true && file.isFile && file.length() > WAV_HEADER_SIZE
        recordingFailure = null
        if (delete || !valid) {
            file.delete()
            return null
        }

        val dataSize = file.length() - WAV_HEADER_SIZE
        RandomAccessFile(file, "rw").use { wav ->
            wav.seek(0)
            wav.write(wavHeader(dataSize))
        }
        val duration = sampleCount * 1_000L / SAMPLE_RATE
        return RecordedVoice(file, duration)
    }

    fun maxAmplitude(): Int = latestAmplitude.getAndSet(0)

    fun echoCancellationAvailable(): Boolean = echoCanceler?.enabled == true

    fun cancel() {
        stop(delete = true)
    }

    private fun capturePcm(active: AudioRecord, stream: BufferedOutputStream, sampleBufferSize: Int) {
        val samples = ShortArray(sampleBufferSize)
        try {
            while (recording.get()) {
                val count = active.read(samples, 0, samples.size, AudioRecord.READ_BLOCKING)
                if (count <= 0) continue
                val bytes = ByteArray(count * 2)
                var peak = 0
                var byteIndex = 0
                for (index in 0 until count) {
                    val sample = samples[index].toInt()
                    peak = maxOf(peak, abs(sample))
                    bytes[byteIndex++] = (sample and 0xFF).toByte()
                    bytes[byteIndex++] = ((sample ushr 8) and 0xFF).toByte()
                }
                stream.write(bytes)
                samplesWritten.addAndGet(count.toLong())
                latestAmplitude.getAndUpdate { previous -> maxOf(previous, peak) }
            }
        } catch (error: Throwable) {
            if (recording.get()) recordingFailure = error
        } finally {
            runCatching { stream.flush() }
            runCatching { stream.close() }
        }
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val PCM_FRAME_MILLIS = 20
        private const val PCM_FRAME_SAMPLES = SAMPLE_RATE * PCM_FRAME_MILLIS / 1_000
        private const val CHANNELS = 1
        private const val BITS_PER_SAMPLE = 16
        private const val WAV_HEADER_SIZE = 44

        internal fun wavHeader(dataSize: Long): ByteArray {
            val safeDataSize = dataSize.coerceIn(0, 0xFFFF_FFFFL)
            return ByteBuffer.allocate(WAV_HEADER_SIZE)
                .order(ByteOrder.LITTLE_ENDIAN)
                .put("RIFF".toByteArray(Charsets.US_ASCII))
                .putInt((36L + safeDataSize).coerceAtMost(0xFFFF_FFFFL).toInt())
                .put("WAVE".toByteArray(Charsets.US_ASCII))
                .put("fmt ".toByteArray(Charsets.US_ASCII))
                .putInt(16)
                .putShort(1.toShort())
                .putShort(CHANNELS.toShort())
                .putInt(SAMPLE_RATE)
                .putInt(SAMPLE_RATE * CHANNELS * BITS_PER_SAMPLE / 8)
                .putShort((CHANNELS * BITS_PER_SAMPLE / 8).toShort())
                .putShort(BITS_PER_SAMPLE.toShort())
                .put("data".toByteArray(Charsets.US_ASCII))
                .putInt(safeDataSize.toInt())
                .array()
        }
    }
}

data class RecordedVoice(val file: File, val durationMillis: Long)
