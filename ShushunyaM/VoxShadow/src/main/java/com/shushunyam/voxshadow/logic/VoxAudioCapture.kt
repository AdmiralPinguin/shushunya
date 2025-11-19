package com.shushunyam.voxshadow.logic

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder

/**
 * VoxAudioCapture — простой push-to-talk захват с микрофона.
 *
 * start() — начинает запись в фоне.
 * stop()  — останавливает и отдает весь накопленный PCM через callback.
 *
 * PCM: 16 kHz, mono, 16-bit -> конвертим в FloatArray [-1.0; 1.0].
 */
class VoxAudioCapture(
    private val onBufferReady: (FloatArray) -> Unit
) {

    private var audioRecord: AudioRecord? = null
    @Volatile
    private var isRecording: Boolean = false
    private var workerThread: Thread? = null

    private val sampleRate = 16000
    private val channelConfig = AudioFormat.CHANNEL_IN_MONO
    private val audioFormat = AudioFormat.ENCODING_PCM_16BIT

    fun start() {
        if (isRecording) return

        val minBufferSize = AudioRecord.getMinBufferSize(
            sampleRate,
            channelConfig,
            audioFormat
        )

        if (minBufferSize <= 0) {
            // Можно залогировать
            return
        }

        val record = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            minBufferSize
        )

        if (record.state != AudioRecord.STATE_INITIALIZED) {
            record.release()
            return
        }

        audioRecord = record
        isRecording = true

        workerThread = Thread {
            val shortBuffer = ShortArray(minBufferSize)
            val floatData = ArrayList<Float>()

            record.startRecording()

            while (isRecording) {
                val read = record.read(shortBuffer, 0, shortBuffer.size)
                if (read > 0) {
                    for (i in 0 until read) {
                        floatData.add(shortBuffer[i] / 32768.0f)
                    }
                }
            }

            try {
                record.stop()
            } catch (_: Throwable) {
            }
            record.release()
            audioRecord = null

            // Собираем в FloatArray
            val pcm = FloatArray(floatData.size)
            for (i in floatData.indices) {
                pcm[i] = floatData[i]
            }

            onBufferReady(pcm)
        }.apply { start() }
    }

    fun stop() {
        if (!isRecording) return
        isRecording = false
        // workerThread сам завершится, когда цикл закончится
    }
}
