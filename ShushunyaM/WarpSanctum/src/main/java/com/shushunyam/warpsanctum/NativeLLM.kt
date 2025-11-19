package com.shushunyam.warpsanctum

/**
 * Kotlin-обертка над llama.cpp
 */
object NativeLLM {

    init {
        System.loadLibrary("warp_sanctum")
    }

    external fun init(modelPath: String): Boolean

    external fun generate(prompt: String, maxTokens: Int): String

    external fun free()
}
