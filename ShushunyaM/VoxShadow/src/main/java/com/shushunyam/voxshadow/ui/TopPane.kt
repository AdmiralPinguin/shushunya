package com.shushunyam.voxshadow.ui

import com.shushunyam.voxshadow.viewmodel.VoxState

/**
 * TopPane — логическое представление верхней части экрана.
 *
 * Верх:
 *  - текст на корейском (для собеседника)
 *  - при желании можно добавить маленький RU-текст
 *
 * Здесь нет реального UI, только контракт по данным.
 */
object TopPane {

    data class TopPaneData(
        val foreignText: String,   // корейский, который видит собеседник
        val hintText: String       // подсказка, например "Говорите сюда"
    )

    fun fromState(state: VoxState): TopPaneData {
        return TopPaneData(
            foreignText = state.topText,
            hintText = if (state.isListeningTop) "Listening (KR)..." else "Tap to speak (KR)"
        )
    }
}
