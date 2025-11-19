#ifndef NATIVE_LLM_H
#define NATIVE_LLM_H

#include <string>

#ifdef __cplusplus
extern "C" {
#endif

bool native_llm_init(const char* model_path);
std::string native_llm_generate(const char* prompt, int max_tokens);
void native_llm_free();

#ifdef __cplusplus
}
#endif

#endif
