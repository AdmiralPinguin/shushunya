#ifndef JNI_BRIDGE_H
#define JNI_BRIDGE_H

#include <jni.h>

#ifdef __cplusplus
extern "C" {
#endif

JNIEXPORT jboolean JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_init(
        JNIEnv* env,
        jclass clazz,
        jstring modelPath);

JNIEXPORT jstring JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_transcribe(
        JNIEnv* env,
        jclass clazz,
        jfloatArray pcmBuffer,
        jint samplesCount,
        jstring language);

JNIEXPORT void JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_free(
        JNIEnv* env,
        jclass clazz);

#ifdef __cplusplus
}
#endif

#endif
