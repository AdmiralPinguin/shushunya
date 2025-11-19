#include "native_stt.h"
#include "whisper.h"
#include <mutex>
#include <string>

static whisper_context* ctx = nullptr;
static std::mutex stt_mutex;

bool native_stt_init(const char* model_path) {
    std::lock_guard<std::mutex> lock(stt_mutex);

    if (ctx != nullptr) return true;

    whisper_context_params params = whisper_context_default_params();

    ctx = whisper_init_from_file_with_params(model_path, params);
    return ctx != nullptr;
}

std::string native_stt_transcribe(const float* pcm, int samplesCount, const char* language) {
    std::lock_guard<std::mutex> lock(stt_mutex);
    if (ctx == nullptr) return "";

    whisper_full_params wparams = whisper_full_default_params(WHISPER_SAMPLING_GREEDY);

    wparams.print_progress = false;
    wparams.print_special = false;
    wparams.print_realtime = false;
    wparams.print_timestamps = false;

    wparams.language = language;

    if (whisper_full(ctx, wparams, pcm, samplesCount) != 0) {
        return "";
    }

    const int segments = whisper_full_n_segments(ctx);
    std::string result;

    for (int i = 0; i < segments; i++) {
        result += whisper_full_get_segment_text(ctx, i);
    }

    return result;
}

void native_stt_free() {
    std::lock_guard<std::mutex> lock(stt_mutex);
    if (ctx) {
        whisper_free(ctx);
        ctx = nullptr;
    }
}
