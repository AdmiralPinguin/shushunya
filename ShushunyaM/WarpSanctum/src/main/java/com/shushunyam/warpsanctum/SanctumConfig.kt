package com.shushunyam.warpsanctum

import java.io.File

/**
 * SanctumConfig — конфиг, в котором прописаны модели.
 *
 * В будущем:
 *   - будет JSON
 *   - можно менять профили
 */
data class SanctumConfig(
    val sttModels: Map<String, String>,
    val llmModels: Map<String, String>
) {
    companion object {

        fun load(basePath: File): SanctumConfig {
            // TODO: загрузка из JSON
            // пока — жёстко зашитые пути
            return SanctumConfig(
                sttModels = mapOf(
                    "ru" to "whisper-tiny.bin",
                    "ko" to "whisper-tiny.bin"
                ),
                llmModels = mapOf(
                    "translator" to "mistral-7b-q4_k_m.gguf"
                )
            )
        }
    }
}
