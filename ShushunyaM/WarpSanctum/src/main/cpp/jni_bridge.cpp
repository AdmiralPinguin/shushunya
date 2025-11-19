#include "jni_bridge.h"
#include "native_stt.h"
#include <string>

JNIEXPORT jboolean JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_init(
        JNIEnv* env,
        jclass clazz,
        jstring modelPath) {

    const char* cpath = env->GetStringUTFChars(modelPath, nullptr);
    bool ok = native_stt_init(cpath);
    env->ReleaseStringUTFChars(modelPath, cpath);

    return ok ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jstring JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_transcribe(
        JNIEnv* env,
        jclass clazz,
        jfloatArray pcmBuffer,
        jint samplesCount,
        jstring language) {

    jfloat* pcmPtr = env->GetFloatArrayElements(pcmBuffer, nullptr);
    const char* langPtr = env->GetStringUTFChars(language, nullptr);

    std::string result = native_stt_transcribe(pcmPtr, samplesCount, langPtr);

    env->ReleaseFloatArrayElements(pcmBuffer, pcmPtr, 0);
    env->ReleaseStringUTFChars(language, langPtr);

    return env->NewStringUTF(result.c_str());
}

JNIEXPORT void JNICALL
Java_com_shushunyam_warpsanctum_NativeSTT_free(
        JNIEnv* env,
        jclass clazz) {
    native_stt_free();
}
