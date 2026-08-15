package app.nexus.mobile

import android.annotation.SuppressLint
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import android.os.Build
import android.os.ParcelFileDescriptor
import android.speech.RecognizerIntent
import androidx.annotation.RequiresApi
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStream
import java.io.RandomAccessFile
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

internal class EchoCanceledSpeechAudioSource private constructor(
    private val audioRecord: AudioRecord,
    private val echoCanceler: AcousticEchoCanceler,
    private val readDescriptor: ParcelFileDescriptor,
    private val output: OutputStream,
    private val recordingFile: File,
    private val recordingOutput: BufferedOutputStream,
) {
    private val capturing = AtomicBoolean(false)
    private val audioFinished = AtomicBoolean(false)
    private val closed = AtomicBoolean(false)
    private val samplesWritten = AtomicLong(0)
    private var captureThread: Thread? = null
    private var finalizedRecording: RecordedVoice? = null
    private var recordingClaimed = false

    @RequiresApi(Build.VERSION_CODES.TIRAMISU)
    fun attachTo(intent: Intent) {
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE, readDescriptor)
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_CHANNEL_COUNT, 1)
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_ENCODING, AudioFormat.ENCODING_PCM_16BIT)
        intent.putExtra(RecognizerIntent.EXTRA_AUDIO_SOURCE_SAMPLING_RATE, SAMPLE_RATE)
    }

    fun start() {
        check(!closed.get()) { "回声消除音频源已关闭" }
        audioRecord.startRecording()
        capturing.set(true)
        captureThread = Thread(::pumpAudio, "nexus-aec-speech-source").apply {
            isDaemon = true
            start()
        }
    }

    fun finishAudio() {
        if (!audioFinished.compareAndSet(false, true)) return
        capturing.set(false)
        runCatching { audioRecord.stop() }
        runCatching { output.close() }
        captureThread?.let { thread ->
            runCatching { thread.join(500) }
            if (thread.isAlive) thread.interrupt()
        }
        captureThread = null
        runCatching { echoCanceler.release() }
        runCatching { audioRecord.release() }

        runCatching { recordingOutput.flush() }
        runCatching { recordingOutput.close() }
        val dataSize = (recordingFile.length() - WAV_HEADER_SIZE).coerceAtLeast(0)
        if (dataSize > 0) {
            runCatching {
                RandomAccessFile(recordingFile, "rw").use { wav ->
                    wav.seek(0)
                    wav.write(VoiceRecorder.wavHeader(dataSize))
                }
                finalizedRecording = RecordedVoice(
                    file = recordingFile,
                    durationMillis = samplesWritten.get() * 1_000L / SAMPLE_RATE,
                )
            }.onFailure { recordingFile.delete() }
        } else {
            recordingFile.delete()
        }
    }

    fun takeRecording(): RecordedVoice? {
        finishAudio()
        return synchronized(this) {
            finalizedRecording?.also { recordingClaimed = true }
        }
    }

    fun close() {
        if (!closed.compareAndSet(false, true)) return
        finishAudio()
        runCatching { readDescriptor.close() }
        synchronized(this) {
            if (!recordingClaimed) recordingFile.delete()
        }
    }

    private fun pumpAudio() {
        val buffer = ByteArray(PCM_FRAME_BYTES)
        try {
            while (capturing.get()) {
                val count = audioRecord.read(buffer, 0, buffer.size, AudioRecord.READ_BLOCKING)
                if (count > 0) {
                    output.write(buffer, 0, count)
                    recordingOutput.write(buffer, 0, count)
                    samplesWritten.addAndGet((count / 2).toLong())
                }
            }
        } catch (_: Exception) {
            // Closing the pipe is how the recognition session stops the blocking writer.
        } finally {
            runCatching { output.close() }
            runCatching { recordingOutput.flush() }
            runCatching { recordingOutput.close() }
        }
    }

    companion object {
        private const val SAMPLE_RATE = 16_000
        private const val PCM_FRAME_MILLIS = 20
        private const val PCM_FRAME_BYTES = SAMPLE_RATE * PCM_FRAME_MILLIS / 1_000 * 2
        private const val WAV_HEADER_SIZE = 44

        @SuppressLint("MissingPermission")
        fun create(cacheDirectory: File): EchoCanceledSpeechAudioSource? {
            if (!AcousticEchoCanceler.isAvailable()) return null
            val minimumBuffer = AudioRecord.getMinBufferSize(
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
            )
            if (minimumBuffer <= 0) return null
            val bufferSize = maxOf(minimumBuffer * 2, SAMPLE_RATE / 2)
            val audioRecord = runCatching {
                AudioRecord.Builder()
                    .setAudioSource(MediaRecorder.AudioSource.VOICE_COMMUNICATION)
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setSampleRate(SAMPLE_RATE)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(bufferSize)
                    .build()
            }.getOrNull() ?: return null
            if (audioRecord.state != AudioRecord.STATE_INITIALIZED) {
                audioRecord.release()
                return null
            }
            val echoCanceler = AcousticEchoCanceler.create(audioRecord.audioSessionId)
            if (echoCanceler == null) {
                audioRecord.release()
                return null
            }
            val enabled = runCatching {
                echoCanceler.enabled = true
                echoCanceler.enabled
            }.getOrDefault(false)
            if (!enabled) {
                echoCanceler.release()
                audioRecord.release()
                return null
            }
            val pipe = runCatching { ParcelFileDescriptor.createPipe() }.getOrNull()
            if (pipe == null) {
                echoCanceler.release()
                audioRecord.release()
                return null
            }
            val recordingFile = runCatching {
                File.createTempFile("voice-call-", ".wav", cacheDirectory)
            }.getOrNull()
            if (recordingFile == null) {
                pipe.forEach { runCatching { it.close() } }
                echoCanceler.release()
                audioRecord.release()
                return null
            }
            val recordingOutput = runCatching {
                BufferedOutputStream(FileOutputStream(recordingFile)).apply {
                    write(ByteArray(WAV_HEADER_SIZE))
                }
            }.getOrNull()
            if (recordingOutput == null) {
                recordingFile.delete()
                pipe.forEach { runCatching { it.close() } }
                echoCanceler.release()
                audioRecord.release()
                return null
            }
            return EchoCanceledSpeechAudioSource(
                audioRecord = audioRecord,
                echoCanceler = echoCanceler,
                readDescriptor = pipe[0],
                output = ParcelFileDescriptor.AutoCloseOutputStream(pipe[1]),
                recordingFile = recordingFile,
                recordingOutput = recordingOutput,
            )
        }
    }
}
