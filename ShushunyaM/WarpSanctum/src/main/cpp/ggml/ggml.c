#include "ggml.h"
#include <stdlib.h>

struct ggml_context {
    int dummy;
};

struct ggml_tensor {
    ggml_type type;
    int ne[2];
};

ggml_context * ggml_init(void * params) {
    return (ggml_context*)malloc(sizeof(ggml_context));
}

void ggml_free(ggml_context * ctx) {
    if (ctx) free(ctx);
}

ggml_tensor * ggml_new_tensor_1d(ggml_context *ctx, ggml_type type, int ne0) {
    ggml_tensor *t = (ggml_tensor*)malloc(sizeof(ggml_tensor));
    t->type = type;
    t->ne[0] = ne0;
    t->ne[1] = 1;
    return t;
}

ggml_tensor * ggml_new_tensor_2d(ggml_context *ctx, ggml_type type, int ne0, int ne1) {
    ggml_tensor *t = (ggml_tensor*)malloc(sizeof(ggml_tensor));
    t->type = type;
    t->ne[0] = ne0;
    t->ne[1] = ne1;
    return t;
}
