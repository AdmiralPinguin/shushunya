package com.shushunyam.voxshadow.viewmodel

import com.shushunyam.warpsanctum.NeuralEngine
import com.shushunyam.voxshadow.logic.VoxAudioCapture
import com.shushunyam.voxshadow.logic.VoxPipeline

/**
 * Логика для экрана переводчика (верх/низ).
 */
class VoxShadowViewModel {

    var state: VoxState = VoxState()
        private set

    /**
     * Колбэк, который UI может задать, чтобы получать обновления состояния.
     * ВАЖНО: вызывается из разных потоков, UI обязан сам дернуть runOnUiThread.
     */
    var onStateChanged: ((VoxState) -> Unit)? = null

    private val pipeline = VoxPipeline(NeuralEngine)

    // Захват аудио для верхнего (KR) и нижнего (RU) каналов
    private val topCapture = VoxAudioCapture { pcm ->
        val text = NeuralEngine.processStt("top", pcm)
        synchronized(this) {
            state = pipeline.handleKoreanText(state, text)
            onStateChanged?.invoke(state)
        }
    }

    private val bottomCapture = VoxAudioCapture { pcm ->
        val text = NeuralEngine.processStt("bottom", pcm)
        synchronized(this) {
            state = pipeline.handleRussianText(state, text)
            onStateChanged?.invoke(state)
        }
    }

    fun toggleTopListening() {
        val listening = !state.isListeningTop
        state = state.copy(isListeningTop = listening)
        onStateChanged?.invoke(state)

        if (listening) {
            pipeline.onTopStart()
            topCapture.start()
        } else {
            topCapture.stop()
            pipeline.onTopStop()
        }
    }

    fun toggleBottomListening() {
        val listening = !state.isListeningBottom
        state = state.copy(isListeningBottom = listening)
        onStateChanged?.invoke(state)

        if (listening) {
            pipeline.onBottomStart()
            bottomCapture.start()
        } else {
            bottomCapture.stop()
            pipeline.onBottomStop()
        }
    }

    // Доп. методы, если нужно ручное обновление
    fun onKoreanRecognized(text: String) {
        state = pipeline.handleKoreanText(state, text)
        onStateChanged?.invoke(state)
    }

    fun onRussianRecognized(text: String) {
        state = pipeline.handleRussianText(state, text)
        onStateChanged?.invoke(state)
    }
}
