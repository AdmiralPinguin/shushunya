package com.shushunyam.warpsanctum

import android.content.Context
import java.io.File

class ModelManager {

    private lateinit var basePath: File
    private lateinit var config: SanctumConfig

    fun init(context: Context) {
        basePath = File(context.getDir("models", Context.MODE_PRIVATE).absolutePath)
        config = SanctumConfig.load(basePath)
    }

    fun getSttModel(language: String): String {
        return File(basePath, config.sttModels[language] ?: "").absolutePath
    }

    fun getLlmModel(profile: String): String {
        return File(basePath, config.llmModels[profile] ?: "").absolutePath
    }
}
