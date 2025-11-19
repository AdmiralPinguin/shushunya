package com.shushunyam.warpsanctum

/**
 * Kotlin-обертка над whisper JNI
 */
object NativeSTT {

    init {
        System.loadLibrary("warp_sanctum")
    }

    external fun init(modelPath: String): Boolean

    external fun transcribe(pcmBuffer: FloatArray, samplesCount: Int, language: String): String

    external fun free()
}
