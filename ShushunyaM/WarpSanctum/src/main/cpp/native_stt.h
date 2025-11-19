#ifndef NATIVE_STT_H
#define NATIVE_STT_H

#include <string>
#include <vector>

#ifdef __cplusplus
extern "C" {
#endif

// Инициализация модели Whisper
bool native_stt_init(const char* model_path);

// Обработка PCM буфера
std::string native_stt_transcribe(const float* pcm, int samplesCount, const char* language);

// Освобождение ресурсов
void native_stt_free();

#ifdef __cplusplus
}
#endif

#endif
