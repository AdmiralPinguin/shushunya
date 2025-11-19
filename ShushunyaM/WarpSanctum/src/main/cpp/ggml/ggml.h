#ifndef GGML_H
#define GGML_H

#ifdef __cplusplus
extern "C" {
#endif

// реал минимального ggml API, необходимого llama/whisper
typedef struct ggml_context ggml_context;
typedef struct ggml_tensor ggml_tensor;

typedef enum {
    GGML_TYPE_F32  = 0,
    GGML_TYPE_F16  = 1,
    GGML_TYPE_Q4_0 = 2,
    GGML_TYPE_Q4_1 = 3,
    GGML_TYPE_Q8_0 = 7,
    GGML_TYPE_Q8_1 = 8
} ggml_type;

ggml_context * ggml_init(void * params);
void ggml_free(ggml_context * ctx);

ggml_tensor * ggml_new_tensor_1d(ggml_context *ctx, ggml_type type, int ne0);
ggml_tensor * ggml_new_tensor_2d(ggml_context *ctx, ggml_type type, int ne0, int ne1);

#ifdef __cplusplus
}
#endif

#endif
