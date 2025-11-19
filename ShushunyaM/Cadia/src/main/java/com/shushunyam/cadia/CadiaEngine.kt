package com.shushunyam.cadia

import android.content.Context

object CadiaEngine {

    private var appContext: Context? = null
    private val registry: ModuleRegistry = ModuleRegistry()

    fun init(context: Context) {
        if (appContext == null) {
            appContext = context.applicationContext
        }
    }

    fun registerModule(descriptor: ModuleDescriptor) {
        registry.register(descriptor)
    }

    fun getAppContext(): Context {
        return requireNotNull(appContext) {
            "CadiaEngine not initialized. Call CadiaEngine.init(context) first."
        }
    }

    fun startModule(moduleId: String) {
        val module = registry.getOrCreateInstance(moduleId)
        module?.start()
    }

    fun stopModule(moduleId: String) {
        val module = registry.getExistingInstance(moduleId)
        module?.stop()
    }

    fun stopAllModules() {
        registry.getAllInstances().forEach { (_, module) ->
            module.stop()
        }
    }

    fun dispatchCommand(command: CoreCommand) {
        CommandRouter.route(command, registry)
    }
}
