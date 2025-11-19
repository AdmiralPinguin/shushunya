package com.shushunyam.voxshadow

import com.shushunyam.cadia.ModuleType
import com.shushunyam.cadia.ShushuModule
import com.shushunyam.voxshadow.viewmodel.VoxShadowViewModel
import com.shushunyam.voxshadow.ui.VoxShadowScreen
import com.shushunyam.warpsanctum.NeuralEngine
import com.shushunyam.cadia.CadiaEngine

/**
 * VoxShadowModule — модуль переводчика верх/низ.
 */
class VoxShadowModule : ShushuModule {

    override val id: String = "vox_shadow"
    override val type: ModuleType = ModuleType.FEATURE

    private var screen: VoxShadowScreen? = null
    private var viewModel: VoxShadowViewModel? = null

    override fun start() {

        // инициализируем нейродвижок один раз
        NeuralEngine.init(CadiaEngine.getAppContext())

        viewModel = VoxShadowViewModel()
        screen = VoxShadowScreen(viewModel!!)

        println("VoxShadowModule started.")
    }

    override fun stop() {
        println("VoxShadowModule stopped.")
        screen = null
        viewModel = null
    }
}
