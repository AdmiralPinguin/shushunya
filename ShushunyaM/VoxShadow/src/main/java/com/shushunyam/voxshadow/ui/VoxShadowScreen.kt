package com.shushunyam.voxshadow.ui

import com.shushunyam.voxshadow.viewmodel.VoxShadowViewModel
import com.shushunyam.voxshadow.viewmodel.VoxState

/**
 * VoxShadowScreen — логический каркас экрана переводчика.
 *
 * Здесь НЕТ Android UI, только связи:
 *  - держим ссылку на ViewModel
 *  - дергаем методы верх/низ
 *
 * Реальный UI (Compose/View) потом будет вызывать эти методы.
 */
class VoxShadowScreen(
    private val viewModel: VoxShadowViewModel
) {

    fun onTopMicToggle() {
        viewModel.toggleTopListening()
    }

    fun onBottomMicToggle() {
        viewModel.toggleBottomListening()
    }

    /**
     * Это будет вызываться UI-слоем, чтобы обновить отображение.
     * Пока просто возвращаем состояние.
     */
    fun getCurrentState(): VoxState {
        return viewModel.state
    }
}
