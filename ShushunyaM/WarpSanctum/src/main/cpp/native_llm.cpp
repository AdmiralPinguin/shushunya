#include "native_llm.h"
#include "llama.h"
#include <mutex>

static llama_context* ctx = nullptr;
static llama_model* model = nullptr;
static std::mutex llm_mutex;

bool native_llm_init(const char* model_path) {
    std::lock_guard<std::mutex> lock(llm_mutex);

    if (ctx != nullptr) return true;

    llama_backend_init(false);

    llama_model_params model_params = llama_model_default_params();
    model = llama_load_model_from_file(model_path, model_params);
    if (!model) return false;

    llama_context_params ctx_params = llama_context_default_params();
    ctx = llama_new_context_with_model(model, ctx_params);
    return ctx != nullptr;
}

std::string native_llm_generate(const char* prompt, int max_tokens) {
    std::lock_guard<std::mutex> lock(llm_mutex);
    if (!ctx) return "";

    std::string result;

    llama_token bos = llama_token_bos(model);
    llama_token eos = llama_token_eos(model);

    std::vector<llama_token> tokens;
    tokens.push_back(bos);

    // токенизация промпта
    {
        auto toks = llama_tokenize(model, prompt, true);
        tokens.insert(tokens.end(), toks.begin(), toks.end());
    }

    llama_eval(ctx, tokens.data(), tokens.size(), 0, 1);

    for (int i = 0; i < max_tokens; i++) {
        llama_token new_tok = llama_sample_token_greedy(ctx);
        if (new_tok == eos) break;

        const char* piece = llama_token_to_str(model, new_tok);
        result += piece;

        llama_eval(ctx, &new_tok, 1, tokens.size(), 1);
        tokens.push_back(new_tok);
    }

    return result;
}

void native_llm_free() {
    std::lock_guard<std::mutex> lock(llm_mutex);
    if (ctx) {
        llama_free(ctx);
        ctx = nullptr;
    }
    if (model) {
        llama_free_model(model);
        model = nullptr;
    }
    llama_backend_free();
}
