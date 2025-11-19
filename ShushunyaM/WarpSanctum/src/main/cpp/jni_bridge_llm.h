#ifndef JNI_BRIDGE_LLM_H
#define JNI_BRIDGE_LLM_H

#include <jni.h>

#ifdef __cplusplus
extern "C" {
#endif

JNIEXPORT jboolean JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_init(
    JNIEnv* env,
    jclass clazz,
    jstring modelPath);

JNIEXPORT jstring JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_generate(
    JNIEnv* env,
    jclass clazz,
    jstring prompt,
    jint maxTokens);

JNIEXPORT void JNICALL
Java_com_shushunyam_warpsanctum_NativeLLM_free(
    JNIEnv* env,
    jclass clazz);

#ifdef __cplusplus
}
#endif

#endif
