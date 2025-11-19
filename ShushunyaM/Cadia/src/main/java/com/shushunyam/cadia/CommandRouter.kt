package com.shushunyam.cadia

sealed class CoreCommand {

    object OpenVoxShadow : CoreCommand()

    data class ModuleCommand(
        val targetModuleId: String,
        val payload: String
    ) : CoreCommand()
}

object CommandRouter {

    fun route(command: CoreCommand, registry: ModuleRegistry) {
        when (command) {
            is CoreCommand.OpenVoxShadow -> {
                val module = registry.getOrCreateInstance("vox_shadow")
                module?.start()
            }

            is CoreCommand.ModuleCommand -> {
                val module = registry.getOrCreateInstance(command.targetModuleId)
                // Будет расширено позже
                // module?.onCommand(...)
            }
        }
    }
}
