package com.shushunyam.voxshadow.logic

import com.shushunyam.warpsanctum.NeuralEngine
import com.shushunyam.voxshadow.viewmodel.VoxState

/**
 * VoxPipeline — потоковая логика работы переводчика:
 *  - верх (KR → RU)
 *  - низ (RU → KR)
 */
class VoxPipeline(
    private val engine: NeuralEngine  // ← теперь правильный тип
) {

    fun onTopStart() {
        engine.startStt("ko", "top")
    }

    fun onTopStop() {
        engine.stopStt("top")
    }

    fun onBottomStart() {
        engine.startStt("ru", "bottom")
    }

    fun onBottomStop() {
        engine.stopStt("bottom")
    }

    fun handleKoreanText(current: VoxState, koreanText: String): VoxState {
        val ru = engine.translateKRtoRU(koreanText)
        return current.copy(
            topText = koreanText,
            bottomText = ru
        )
    }

    fun handleRussianText(current: VoxState, russianText: String): VoxState {
        val kr = engine.translateRUtoKR(russianText)
        return current.copy(
            topText = kr,
            bottomText = russianText
        )
    }
}
