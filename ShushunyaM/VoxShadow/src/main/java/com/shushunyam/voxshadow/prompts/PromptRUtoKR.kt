package com.shushunyam.voxshadow.prompts

/**
 * Шаблон промпта для перевода с русского на корейский.
 */
object PromptRUtoKR {

    fun build(text: String): String {
        return """
            Ты переводчик с русского на корейский.
            Переведи текст естественно и вежливо.
            Не добавляй ничего от себя.

            ТЕКСТ:
            $text
        """.trimIndent()
    }
}
