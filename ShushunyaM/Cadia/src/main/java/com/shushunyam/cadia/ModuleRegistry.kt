package com.shushunyam.cadia

enum class ModuleType {
    FEATURE,
    SERVICE,
    SYSTEM
}

interface ShushuModule {
    val id: String
    val type: ModuleType

    fun start()
    fun stop()
}

data class ModuleDescriptor(
    val id: String,
    val displayName: String,
    val type: ModuleType,
    val factory: () -> ShushuModule
)

class ModuleRegistry {

    private val descriptors: MutableMap<String, ModuleDescriptor> = mutableMapOf()
    private val instances: MutableMap<String, ShushuModule> = mutableMapOf()

    fun register(descriptor: ModuleDescriptor) {
        descriptors[descriptor.id] = descriptor
    }

    fun getExistingInstance(id: String): ShushuModule? {
        return instances[id]
    }

    fun getOrCreateInstance(id: String): ShushuModule? {
        val existing = instances[id]
        if (existing != null) return existing

        val descriptor = descriptors[id] ?: return null
        val module = descriptor.factory.invoke()
        instances[id] = module
        return module
    }

    fun getAllInstances(): Map<String, ShushuModule> = instances.toMap()

    fun isRegistered(id: String): Boolean = descriptors.containsKey(id)

    fun getAllDescriptors(): List<ModuleDescriptor> = descriptors.values.toList()
}
