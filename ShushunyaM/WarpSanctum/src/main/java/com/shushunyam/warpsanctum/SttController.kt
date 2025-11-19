package com.shushunyam.warpsanctum

class SttController {

    private val activeChannels = mutableMapOf<String, String>() // channelId -> lang
    private lateinit var models: ModelManager

    fun init(modelManager: ModelManager) {
        this.models = modelManager
    }

    fun start(language: String, channelId: String) {
        activeChannels[channelId] = language

        val modelPath = models.getSttModel(language)
        NativeSTT.init(modelPath)
    }

    fun stop(channelId: String) {
        activeChannels.remove(channelId)
        NativeSTT.free()
    }

    fun process(channelId: String, pcm: FloatArray): String {
        val lang = activeChannels[channelId] ?: return ""
        return NativeSTT.transcribe(pcm, pcm.size, lang)
    }

    fun unload() {
        activeChannels.clear()
        NativeSTT.free()
    }
}
