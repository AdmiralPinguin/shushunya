package com.shushunyam.voxshadow.viewmodel

/**
 * VoxState — полное состояние VoxShadow.
 *
 * topText    — текст наверху (для собеседника, корейский)
 * bottomText — текст внизу (для тебя, русский)
 *
 * isListeningTop / Bottom — индикаторы активного прослушивания микрофона.
 */
data class VoxState(
    val topText: String = "",
    val bottomText: String = "",
    val isListeningTop: Boolean = false,
    val isListeningBottom: Boolean = false
)
