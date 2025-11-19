#include "jni_bridge_llm.h"
#include "native_llm.h"

JNIEXPORT jboolean JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_init(
    JNIEnv* env,
    jclass clazz,
    jstring modelPath) {

    const char* cpath = env->GetStringUTFChars(modelPath, nullptr);
    bool ok = native_llm_init(cpath);
    env->ReleaseStringUTFChars(modelPath, cpath);

    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jstring JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_generate(
    JNIEnv* env,
    jclass clazz,
    jstring prompt,
    jint maxTokens) {

    const char* cprompt = env->GetStringUTFChars(prompt, nullptr);
    std::string result = native_llm_generate(cprompt, maxTokens);
    env->ReleaseStringUTFChars(prompt, cprompt);

    return env->NewStringUTF(result.c_str());
}

JNIEXPORT void JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_free(
    JNIEnv* env,
    jclass clazz) {
    native_llm_free();
}
