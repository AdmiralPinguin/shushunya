package com.shushunyam.warpsanctum

class LlmController {

    private lateinit var models: ModelManager
    private var initialized = false

    fun init(modelManager: ModelManager) {
        this.models = modelManager
    }

    private fun ensureInit() {
        if (!initialized) {
            val path = models.getLlmModel("translator")
            NativeLLM.init(path)
            initialized = true
        }
    }

    fun translate(direction: String, text: String): String {
        ensureInit()

        val prompt = when (direction) {
            "KR2RU" -> "Переведи с корейского на русский: $text"
            "RU2KR" -> "Переведи с русского на корейский: $text"
            else -> text
        }

        return NativeLLM.generate(prompt, 128)
    }

    fun unload() {
        if (initialized) {
            NativeLLM.free()
            initialized = false
        }
    }
}
