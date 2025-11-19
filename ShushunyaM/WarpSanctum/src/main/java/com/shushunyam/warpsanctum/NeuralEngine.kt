package com.shushunyam.warpsanctum

import android.content.Context

/**
 * Центральный движок нейросетей.
 */
object NeuralEngine {

    private var initialized = false

    private val stt: SttController = SttController()
    private val llm: LlmController = LlmController()
    private val modelManager: ModelManager = ModelManager()

    fun init(context: Context) {
        if (initialized) return
        initialized = true

        modelManager.init(context)
        stt.init(modelManager)
        llm.init(modelManager)

        println("NeuralEngine initialized.")
    }

    // --------- STT API ---------

    fun startStt(language: String, channelId: String) {
        println("STT [$channelId] START ($language)")
        stt.start(language, channelId)
    }

    fun stopStt(channelId: String) {
        println("STT [$channelId] STOP")
        stt.stop(channelId)
    }

    /**
     * Обработка PCM-буфера для указанного канала.
     * Возвращает распознанный текст.
     */
    fun processStt(channelId: String, pcm: FloatArray): String {
        return stt.process(channelId, pcm)
    }

    // --------- LLM API ---------

    fun translateKRtoRU(text: String): String {
        println("LLM translate KR->RU: $text")
        return llm.translate("KR2RU", text)
    }

    fun translateRUtoKR(text: String): String {
        println("LLM translate RU->KR: $text")
        return llm.translate("RU2KR", text)
    }

    fun unloadAll() {
        stt.unload()
        llm.unload()
    }
}
