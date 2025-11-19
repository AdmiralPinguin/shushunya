package com.shushunyam.app

import android.content.Context
import com.shushunyam.cadia.CadiaEngine
import com.shushunyam.cadia.ModuleDescriptor
import com.shushunyam.cadia.ModuleType
import com.shushunyam.voxshadow.VoxShadowModule

/**
 * AppInitializer — вызывается при старте приложения.
 *
 * Здесь регистрируем модули.
 */
object AppInitializer {

    fun init(context: Context) {

        CadiaEngine.init(context)

        // Регистрируем VoxShadow
        CadiaEngine.registerModule(
            ModuleDescriptor(
                id = "vox_shadow",
                displayName = "Vox Shadow Translator",
                type = ModuleType.FEATURE,
                factory = { VoxShadowModule() }
            )
        )
    }
}
